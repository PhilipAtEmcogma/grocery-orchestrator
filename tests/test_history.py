"""
Price-history tests. No AWS -- the pure logic (item shaping, read-time average,
deviation) is exercised directly, and the DynamoDB read/write is a thin adapter
verified by the ingestion suite's fake-table tests.

The load-bearing assertion here is the LAST one: the shopper path must never
import `src/history`, because an average over a window is not a price anyone can
pay and must never reach a citation.
"""

from __future__ import annotations

from decimal import Decimal

from src.history import (
    PriceHistoryRecord,
    summarise,
    to_history_item,
)


def _product_item(**over) -> dict:
    base = {
        "store_key": "paknsave#albany",
        "product_key": "butter-500g",
        "price_nzd": "4.79",
        "unit_price_nzd": "9.58",
        "valid_date": "2026-07-31",
        "on_special": False,
        # Fields history deliberately drops:
        "display_name": "Pams Butter 500g",
        "lat": Decimal("-36.7"),
        "lon": Decimal("174.7"),
        "category": "dairy",
    }
    base.update(over)
    return base


def _record(price: str, valid_date: str, **over) -> PriceHistoryRecord:
    base = {
        "store_key": "paknsave#albany",
        "product_key": "butter-500g",
        "price_nzd": Decimal(price),
        "unit_price_nzd": Decimal("9.58"),
        "valid_date": valid_date,
        "on_special": False,
    }
    base.update(over)
    return PriceHistoryRecord(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------ item shaping


def test_to_history_item_keeps_only_baseline_fields():
    item = to_history_item(_product_item())
    assert set(item) == {
        "history_pk",
        "valid_date",
        "store_key",
        "product_key",
        "price_nzd",
        "unit_price_nzd",
        "on_special",
    }


def test_to_history_item_builds_the_composite_partition_key():
    item = to_history_item(_product_item())
    assert item["history_pk"] == "paknsave#albany#butter-500g"
    # The capture date is the sort key -- what makes the write append across days.
    assert item["valid_date"] == "2026-07-31"


def test_to_history_item_keeps_money_as_strings():
    item = to_history_item(_product_item(price_nzd="4.79", unit_price_nzd="9.58"))
    assert item["price_nzd"] == "4.79"
    assert isinstance(item["price_nzd"], str)
    assert isinstance(item["unit_price_nzd"], str)


def test_to_history_item_drops_shopper_and_display_fields():
    """History is a baseline, not a catalogue row -- see the never-shopper-facing rule."""
    item = to_history_item(_product_item())
    for leaked in ("display_name", "lat", "lon", "category"):
        assert leaked not in item


# ------------------------------------------------------------ read-time average


def test_summarise_computes_the_windowed_average():
    records = [
        _record("4.00", "2026-07-01"),
        _record("5.00", "2026-07-15"),
        _record("6.00", "2026-07-31"),
    ]
    baseline = summarise(records, window_days=90)
    assert baseline.sample_count == 3
    assert baseline.average_nzd == Decimal("5.00")
    assert baseline.min_nzd == Decimal("4.00")
    assert baseline.max_nzd == Decimal("6.00")
    # Latest is by date, not by list order.
    assert baseline.latest_nzd == Decimal("6.00")
    assert baseline.latest_date == "2026-07-31"


def test_summarise_quantises_the_average_to_the_cent():
    records = [_record("1.00", "2026-07-01"), _record("1.01", "2026-07-02")]
    # (1.00 + 1.01) / 2 = 1.005 -> 1.00 or 1.01 depending on rounding; either
    # way it is quantised to two places, not left at 1.005.
    baseline = summarise(records, window_days=30)
    assert baseline.average_nzd is not None
    assert baseline.average_nzd.as_tuple().exponent == -2


def test_summarise_of_an_empty_window_is_unknown_not_zero():
    """An average of nothing is unknown; a reviewer told 'baseline $0' chases a phantom."""
    baseline = summarise([], window_days=90)
    assert baseline.sample_count == 0
    assert baseline.average_nzd is None
    assert baseline.min_nzd is None
    assert baseline.latest_nzd is None


# ------------------------------------------------------------ deviation


def test_deviation_ratio_flags_a_spike():
    baseline = summarise(
        [_record("2.00", "2026-07-01"), _record("2.00", "2026-07-02")], window_days=30
    )
    # A price ten times the $2.00 baseline.
    assert baseline.deviation_ratio(Decimal("20.00")) == Decimal("10")
    assert baseline.deviation_ratio(Decimal("1.00")) == Decimal("0.5")


def test_deviation_ratio_is_none_without_a_baseline():
    baseline = summarise([], window_days=30)
    assert baseline.deviation_ratio(Decimal("4.79")) is None


# ------------------------------------------------------------ the invariant


def test_the_shopper_path_does_not_import_history():
    """
    History and its averages are ops/reviewer only. If the graph or the
    retrieval layer imported `src.history`, an average could reach a citation --
    the one thing the never-shopper-facing rule forbids. Asserted by walking the
    import graph, the same way the observability no-AWS property is guarded.
    """
    import ast
    from pathlib import Path

    roots = [Path("src/graph"), Path("src/retrieval")]
    offenders: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                    "src.history"
                ):
                    offenders.append(str(path))
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("src.history"):
                            offenders.append(str(path))
    assert not offenders, f"the shopper path must not import src.history: {offenders}"
