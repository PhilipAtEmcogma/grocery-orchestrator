"""
Append-only price history (`grocery-price-history-dev`).

WHY THIS EXISTS. The deterministic ingestion rules can see that a single row is
internally inconsistent -- `implausible_unit_price` catches a unit price that
disagrees with its own pack size. What they CANNOT see is a row that is
internally consistent and simply wrong: $12.99 for a $1.29 item, with a matching
unit price, passes every check. `docs/ARCHITECTURE.md` §3p records that the
largest class of anomaly the rules miss needs a BASELINE -- "this price doubled
overnight" -- and a baseline needs history. This module is that history.

WHAT IT IS NOT. It is NOT a shopper-facing price source, and nothing here ever
becomes a Citation. A shopper-visible price must be a single retrieved fact at a
named store with a capture date (the grounding invariant); an average over a
window is none of those things -- it is not a price anyone can actually pay at
any actual store. History and its averages are for OPS AND THE REVIEWER ONLY.
The graph and `src/retrieval/` never import this module, and a test asserts it.

APPEND-ONLY, AT DAILY GRANULARITY. The sort key is the capture date, so each
refresh appends one row per (store, product, capture date) and never rewrites an
earlier day. Re-running ingestion on the same capture date is idempotent -- it
overwrites that day's row with an identical one -- which matches how the
products table behaves and keeps a repeated run from inventing a second data
point for a day that had one. A finer (per-second) key would make same-day
re-runs duplicate the history, which is the opposite of what a baseline wants.

READ-TIME AVERAGE, NOT STORED. `average_price` computes over whatever window the
caller asks for, at read time. Nothing stores a running average, for two
reasons: it keeps the table genuinely append-only (a stored average is a
mutable field on an immutable row), and the average always reflects the window
the caller chose rather than one frozen at write time.

MONEY IS `Decimal` IN PYTHON AND A STRING AT REST, exactly as the products table
does it, and for the same reason: the DynamoDB Number type round-trips through
float and a float cent is a wrong cent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: The physical table. A separate table from `grocery-products-dev` because it
#: has a different lifecycle (append-only, long retention) and a different
#: reader (ops/reviewer, never the shopper path). Mixing a high-retention audit
#: log into the hot serving table would key one for the other's access pattern.
HISTORY_TABLE = "grocery-price-history-dev"


@dataclass(frozen=True, slots=True)
class PriceHistoryRecord:
    """
    One (store, product) price as captured on one date.

    Frozen because a history row is immutable by definition: once a capture date
    has a price, that fact does not change. The only fields carried are the ones
    a baseline needs -- who, what, how much, when. No shopper data exists in
    ingestion to carry (Req 11.5), which is the same reason the review snapshot
    can be built at all.
    """

    store_key: str
    product_key: str
    price_nzd: Decimal
    unit_price_nzd: Decimal
    valid_date: str
    on_special: bool

    @property
    def history_pk(self) -> str:
        """`store_key#product_key` -- the whole price history of one product at one store."""
        return f"{self.store_key}#{self.product_key}"


def to_history_item(product_item: dict) -> dict:
    """
    A products-table item (from `ingestion.normalise.to_item`) as a history row.

    Reads the same normalised item the products write uses, so the two cannot
    disagree about what a refresh recorded. Money stays a string, exactly as it
    is stored on the products side.

    `history_pk` collapses the base table's two-part key into one partition, so
    "this product's price over time at this store" is a single query. The
    capture date is the sort key, which is what makes the write append rather
    than overwrite across days.
    """
    return {
        "history_pk": f"{product_item['store_key']}#{product_item['product_key']}",
        "valid_date": product_item["valid_date"],
        "store_key": product_item["store_key"],
        "product_key": product_item["product_key"],
        # Strings at rest, as on the products table.
        "price_nzd": str(product_item["price_nzd"]),
        "unit_price_nzd": str(product_item["unit_price_nzd"]),
        "on_special": bool(product_item["on_special"]),
    }


def _record_from_item(item: dict) -> PriceHistoryRecord:
    """A stored history item back into a typed record."""
    return PriceHistoryRecord(
        store_key=item["store_key"],
        product_key=item["product_key"],
        price_nzd=Decimal(item["price_nzd"]),
        unit_price_nzd=Decimal(item["unit_price_nzd"]),
        valid_date=item["valid_date"],
        on_special=bool(item["on_special"]),
    )


@dataclass(frozen=True, slots=True)
class PriceBaseline:
    """
    A read-time summary of one product's price history at one store.

    Everything here is DERIVED at read time from the rows in the window -- none
    of it is stored. `average_nzd` is `None` when the window holds no rows,
    because an average of nothing is not zero, it is unknown, and a reviewer
    told "the baseline is $0" would chase a phantom anomaly.
    """

    history_pk: str
    window_days: int
    sample_count: int
    average_nzd: Decimal | None
    min_nzd: Decimal | None
    max_nzd: Decimal | None
    latest_nzd: Decimal | None
    latest_date: str | None

    def deviation_ratio(self, price: Decimal) -> Decimal | None:
        """
        How far `price` sits from the windowed average, as a ratio.

        1.0 is exactly average, 2.0 is double, 0.5 is half. `None` when there is
        no baseline to compare against or the average is zero. This is the
        number the reviewer's enriched snapshot carries so a model can say "this
        is 10x its own history" without being handed the arithmetic to get
        wrong -- the ratio is computed here, in code.
        """
        if self.average_nzd is None or self.average_nzd == 0:
            return None
        return price / self.average_nzd


def summarise(records: list[PriceHistoryRecord], *, window_days: int) -> PriceBaseline:
    """
    Compute a `PriceBaseline` from history rows. Pure -- no AWS, no clock.

    The caller supplies the rows (already windowed by the query) and the window
    length for the record; this function does the arithmetic. Kept pure so it is
    exhaustively testable and so the average can be recomputed over any window
    without touching storage.
    """
    if not records:
        return PriceBaseline(
            history_pk="",
            window_days=window_days,
            sample_count=0,
            average_nzd=None,
            min_nzd=None,
            max_nzd=None,
            latest_nzd=None,
            latest_date=None,
        )

    prices = [r.price_nzd for r in records]
    # Quantised to the cent: an average is a comparison aid, and a fraction of a
    # cent is noise a reviewer does not need and a Decimal would otherwise carry.
    average = (sum(prices) / Decimal(len(prices))).quantize(Decimal("0.01"))
    latest = max(records, key=lambda r: r.valid_date)

    return PriceBaseline(
        history_pk=records[0].history_pk,
        window_days=window_days,
        sample_count=len(records),
        average_nzd=average,
        min_nzd=min(prices),
        max_nzd=max(prices),
        latest_nzd=latest.price_nzd,
        latest_date=latest.valid_date,
    )
