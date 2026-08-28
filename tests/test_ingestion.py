"""
Ingestion tests. No AWS -- the write path is exercised through a fake table,
so these run in CI alongside everything else.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from ingestion.handler import diff_items
from ingestion.normalise import gsi1_sk, store_key, to_item, unit_price
from ingestion.sources import (
    FIXTURES,
    KNOWN_RETAILERS,
    FixtureSource,
    RawOffer,
    resolve_source,
)


def _offer(**over) -> RawOffer:
    base = {
        "product_key": "butter-500g",
        "store": "paknsave",
        "store_location": "Sylvia Park",
        "display_name": "Pams Butter 500g",
        "canonical_name": "Butter 500g",
        "category": "dairy",
        "price_nzd": Decimal("2.97"),
        "unit": "500g",
        "pack_grams": 500,
        "on_special": True,
        "captured_at": "2026-07-31",
        "lat": -36.8912,
        "lon": 174.8437,
    }
    base.update(over)
    return RawOffer(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ sources


def test_live_acquisition_refuses_rather_than_falling_back():
    """
    The gate is a refusal, not a fallback.

    A misconfiguration that silently reverts to fixtures would hide that
    someone tried to turn on live acquisition; one that silently starts
    requesting retailer sites is the 4.2 exposure. Neither is acceptable, so
    it raises.
    """
    import os

    os.environ["LIVE_ACQUISITION"] = "1"
    try:
        with pytest.raises(NotImplementedError, match="ACQUISITION-RISK"):
            resolve_source("paknsave")
    finally:
        del os.environ["LIVE_ACQUISITION"]


def test_default_source_is_fixtures_for_every_known_retailer():
    for retailer in KNOWN_RETAILERS:
        assert isinstance(resolve_source(retailer), FixtureSource)


def test_unknown_retailer_is_rejected():
    with pytest.raises(ValueError, match="unknown retailer"):
        FixtureSource("countdown-express")


def test_fixture_source_returns_only_its_retailer():
    offers = FixtureSource("woolworths").fetch()
    assert offers
    assert {o.store for o in offers} == {"woolworths"}


def test_every_offer_carries_a_capture_date():
    """Condition 9: a price the shopper cannot date is one they cannot judge."""
    for retailer in KNOWN_RETAILERS:
        assert all(o.captured_at for o in FixtureSource(retailer).fetch())


# --------------------------------------------------------------- normalise


def test_price_is_a_string_at_rest():
    """DynamoDB's numeric type round-trips through float, and a float cent is wrong."""
    item = to_item(_offer())
    assert item["price_nzd"] == "2.97"
    assert isinstance(item["price_nzd"], str)
    assert isinstance(item["unit_price_nzd"], str)


def test_sort_key_orders_lexicographically_by_price():
    """
    The GSI's whole value is that string order equals price order.

    Without zero-padding "1000" sorts before "297", which would return the
    dearest option first for exactly the query the index exists to serve.
    """
    cheap = gsi1_sk(Decimal("2.97"), "paknsave", "Mangere")
    dear = gsi1_sk(Decimal("10.00"), "paknsave", "Mangere")
    assert cheap < dear
    assert cheap.split("#")[0] == "000000297"


def test_equal_prices_break_ties_on_store_key():
    """Two stores at the same price must still order deterministically."""
    a = gsi1_sk(Decimal("2.97"), "paknsave", "Mangere")
    b = gsi1_sk(Decimal("2.97"), "paknsave", "Sylvia Park")
    assert a < b


def test_store_key_slug_matches_the_seeded_form():
    assert store_key("paknsave", "Sylvia Park") == "paknsave#sylvia-park"


def test_unit_price_is_per_kilogram():
    assert unit_price(Decimal("2.97"), 500) == Decimal("5.94")


def test_zero_pack_grams_is_refused_not_divided():
    with pytest.raises(ValueError, match="pack_grams"):
        unit_price(Decimal("2.97"), 0)


def test_undated_offer_is_refused():
    with pytest.raises(ValueError, match="no capture date"):
        to_item(_offer(captured_at=""))


def test_item_carries_no_marketing_fields():
    """Condition 7: facts only. A field that does not exist cannot leak."""
    item = to_item(_offer())
    forbidden = {"image", "image_url", "description", "marketing", "reviews", "rating"}
    assert not (forbidden & set(item))


# ------------------------------------------------------------------ handler


class _FakeBatch:
    def __init__(self, sink: list) -> None:
        self._sink = sink

    def put_item(self, Item):
        self._sink.append(Item)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeTable:
    def __init__(self, rows: list | None = None) -> None:
        self.written: list = []
        self._rows = rows or []

    def batch_writer(self):
        return _FakeBatch(self.written)

    def query(self, **kw):
        return {"Items": list(self._rows)}


def test_refresh_writes_only_the_requested_retailer(monkeypatch):
    from ingestion import handler as h

    table = _FakeTable()
    monkeypatch.setattr(
        h.boto3, "resource", lambda *a, **k: type("R", (), {"Table": lambda s, n: table})()
    )

    result = h.refresh("new_world", table_name="grocery-products-dev")

    assert result["retailer"] == "new_world"
    assert result["written"] == result["fetched"] > 0
    assert result["captured_at"]
    assert {i["store"] for i in table.written} == {"new_world"}


def test_handler_rejects_an_unknown_retailer():
    from ingestion import handler as h

    with pytest.raises(ValueError, match="retailer must be one of"):
        h.lambda_handler({"retailer": "pak-n-save"})


# ------------------------------------------------- seed-parity (the big one)


def test_ingestion_reproduces_the_seeded_records_exactly():
    """
    Every field ingestion writes must equal what the seed loader wrote.

    This is the test that matters, and it did not exist when the first version
    of `unit_price()` shipped. That version dropped generate_fixtures.py's
    `if grams > 1` guard and used ROUND_HALF_UP where the generator uses the
    default ROUND_HALF_EVEN, so a refresh silently rewrote six `broccoli-each`
    rows with `unit_price_nzd: "2490.00"` against a $2.49 item -- a
    shopper-facing figure, a thousand times over -- plus four rounding drifts.
    The unit tests all passed; none of them compared ingestion's output to the
    seed it is supposed to reproduce.

    It also pins `refresh()`'s idempotency claim: if these agree, a scheduled
    run over unchanged fixtures is genuinely a no-op.
    """
    seed = {
        (r["store_key"], r["product_key"]): r
        for r in json.loads(FIXTURES.read_text(encoding="utf-8"))
    }
    compared = 0
    for retailer in KNOWN_RETAILERS:
        for offer in FixtureSource(retailer).fetch():
            item = to_item(offer)
            expected = seed[(item["store_key"], item["product_key"])]
            for field in (
                "price_nzd",
                "unit_price_nzd",
                "gsi1_sk",
                "gsi1_pk",
                "valid_date",
                "store",
                "store_location",
                "display_name",
                "canonical_name",
                "category",
                "unit",
                "pack_grams",
                "on_special",
            ):
                assert str(item[field]) == str(expected[field]), (
                    f"{item['store_key']}/{item['product_key']}.{field}: "
                    f"ingestion {item[field]!r} != seed {expected[field]!r}"
                )
            compared += 1

    assert compared == len(seed) == 152


def test_unit_priced_goods_keep_their_shelf_price():
    """pack_grams == 1 means 'sold each', not 'weighs one gram'."""
    assert unit_price(Decimal("2.49"), 1) == Decimal("2.49")


def test_rounding_matches_the_generator_on_exact_halves():
    """ROUND_HALF_UP vs the generator's default disagree at 2.245; drift is churn."""
    assert unit_price(Decimal("4.49"), 2000) == Decimal("2.24")


# ---------------------------------------------------- diff-before-write


def _row(**over) -> dict:
    item = to_item(_offer())
    item.update(over)
    return item


def test_diff_reports_nothing_when_the_refresh_is_a_no_op():
    """`unchanged == fetched` is what idempotent looks like from the outside."""
    items = [to_item(_offer())]
    existing = {(items[0]["store_key"], items[0]["product_key"]): dict(items[0])}

    delta = diff_items(existing, items)

    assert (delta["added"], delta["changed"], delta["unchanged"]) == (0, 0, 1)


def test_diff_names_the_field_that_moved():
    """
    The reporting that would have made the unit_price defect visible.

    Six rows silently changed unit_price_nzd from "2.49" to "2490.00" and
    nothing said so. A refresh that reports which field moved, from what to
    what, turns that from undiscoverable into a line in the execution history.
    """
    items = [to_item(_offer())]
    stale = dict(items[0])
    stale["unit_price_nzd"] = "2490.00"
    existing = {(items[0]["store_key"], items[0]["product_key"]): stale}

    delta = diff_items(existing, items)

    assert delta["changed"] == 1
    moved = delta["sample_changed"][0]["fields"]
    assert [f["field"] for f in moved] == ["unit_price_nzd"]
    assert moved[0]["from"] == "2490.00"
    assert moved[0]["to"] == "5.94"


def test_diff_counts_a_row_the_table_does_not_have_as_added():
    delta = diff_items({}, [to_item(_offer())])
    assert (delta["added"], delta["changed"], delta["unchanged"]) == (1, 0, 0)


def test_diff_compares_as_strings_so_decimal_round_trips_do_not_read_as_changes():
    """DynamoDB hands back Decimal; the item holds str. Equal values must compare equal."""
    items = [to_item(_offer())]
    stored = dict(items[0])
    stored["pack_grams"] = Decimal("500")
    delta = diff_items({(items[0]["store_key"], items[0]["product_key"]): stored}, items)
    assert delta["changed"] == 0


def test_dry_run_reports_the_diff_and_writes_nothing(monkeypatch):
    from ingestion import handler as h

    table = _FakeTable()
    monkeypatch.setattr(
        h.boto3,
        "resource",
        lambda *a, **k: type("R", (), {"Table": lambda s, n: table})(),
    )

    result = h.refresh("new_world", table_name="grocery-products-dev", dry_run=True)

    assert result["dry_run"] is True
    assert result["written"] == 0
    assert table.written == []
    # It still did the work of finding out what would happen.
    assert result["fetched"] > 0
    assert result["added"] + result["changed"] + result["unchanged"] == result["fetched"]


def test_handler_passes_dry_run_through_from_the_event(monkeypatch):
    from ingestion import handler as h

    seen = {}
    monkeypatch.setattr(
        h, "refresh", lambda retailer, **kw: seen.update(retailer=retailer, **kw) or {}
    )

    h.lambda_handler({"retailer": "paknsave", "dry_run": True})

    assert seen == {"retailer": "paknsave", "dry_run": True}
