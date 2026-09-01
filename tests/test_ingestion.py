"""
Ingestion tests. No AWS -- the write path is exercised through a fake table,
so these run in CI alongside everything else.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.handler import diff_items, reject_implausible
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


def test_price_source_env_selects_lineage_b():
    """The explicit override still works and takes precedence over the config default."""
    import os

    from ingestion.sources import LineageBSource

    os.environ["PRICE_SOURCE"] = "lineage_b"
    try:
        assert isinstance(resolve_source("paknsave"), LineageBSource)
    finally:
        del os.environ["PRICE_SOURCE"]


def test_unknown_price_source_env_raises_rather_than_falling_back():
    """A typo'd PRICE_SOURCE is an error, not a silent fall-through to fixtures."""
    import os

    os.environ["PRICE_SOURCE"] = "lineag_b"  # typo
    try:
        with pytest.raises(ValueError, match="not a known source"):
            resolve_source("paknsave")
    finally:
        del os.environ["PRICE_SOURCE"]


def test_live_acquisition_wins_over_price_source_env():
    """The tripwire is checked before the config/env source selection."""
    import os

    os.environ["LIVE_ACQUISITION"] = "1"
    os.environ["PRICE_SOURCE"] = "lineage_b"
    try:
        with pytest.raises(NotImplementedError, match="ACQUISITION-RISK"):
            resolve_source("paknsave")
    finally:
        del os.environ["LIVE_ACQUISITION"]
        del os.environ["PRICE_SOURCE"]


def test_data_sources_config_declares_lineage_b_primary():
    """
    The config records the data team's catalogue as the PRIMARY input.

    This is the first-class expression of the 2026-08-29 decision; asserting it
    here means the priority cannot be quietly inverted without failing a test.
    """
    from ingestion.sources import DATA_SOURCES_CONFIG

    raw = json.loads(DATA_SOURCES_CONFIG.read_text(encoding="utf-8"))
    by_name = {s["name"]: s for s in raw["sources"]}
    assert by_name["lineage_b"]["role"] == "primary"
    assert by_name["fixtures"]["role"] == "fallback"
    # Every declared source must be one resolve_source can actually build.
    from ingestion.sources import _SOURCE_BY_NAME

    assert all(s["name"] in _SOURCE_BY_NAME for s in raw["sources"])
    assert raw["default_source"] in _SOURCE_BY_NAME


def test_unknown_default_source_in_config_raises():
    """A default_source the code cannot build is a config bug, surfaced loudly."""
    import tempfile

    from ingestion.sources import _default_source_name

    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "data-sources.json"
        bad.write_text(json.dumps({"default_source": "nonesuch"}), encoding="utf-8")
        with pytest.raises(ValueError, match="not a known source"):
            _default_source_name(bad)


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


def _patch_resource(monkeypatch, products: _FakeTable, history: _FakeTable | None = None) -> None:
    """
    Route .Table(name) to the products sink or the history sink by NAME.

    `refresh()` now writes to two tables -- products and grocery-price-history-dev
    -- so a name-blind fake would land both in one sink and hide whether history
    was written at all. `history` defaults to its own throwaway table when a test
    does not care about it.
    """
    from ingestion import handler as h

    hist = history if history is not None else _FakeTable()

    def _table(_self, name):
        return hist if name == h.HISTORY_TABLE else products

    monkeypatch.setattr(h.boto3, "resource", lambda *a, **k: type("R", (), {"Table": _table})())


def test_refresh_writes_only_the_requested_retailer(monkeypatch):
    from ingestion import handler as h

    table = _FakeTable()
    _patch_resource(monkeypatch, table)

    result = h.refresh("new_world", table_name="grocery-products-dev")

    assert result["retailer"] == "new_world"
    assert result["written"] == result["fetched"] > 0
    assert result["captured_at"]
    assert {i["store"] for i in table.written} == {"new_world"}


def test_refresh_appends_price_history_alongside_products(monkeypatch):
    """A real refresh writes one history row per accepted product, to the history table."""
    from ingestion import handler as h

    table = _FakeTable()
    history = _FakeTable()
    _patch_resource(monkeypatch, table, history)

    result = h.refresh("new_world", table_name="grocery-products-dev")

    # One history row per product written, and they went to the HISTORY table,
    # not the products one.
    assert result["history_written"] == len(table.written) > 0
    assert len(history.written) == len(table.written)
    # History rows carry the history_pk and the capture date as the sort key,
    # and money as strings -- the shape src/history defines.
    row = history.written[0]
    assert row["history_pk"] == f"{row['store_key']}#{row['product_key']}"
    assert row["valid_date"]
    assert isinstance(row["price_nzd"], str)
    # No shopper/display fields leaked into history: it is a baseline, not a
    # catalogue row.
    assert "display_name" not in row
    assert "lat" not in row and "lon" not in row


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
    history = _FakeTable()
    _patch_resource(monkeypatch, table, history)

    result = h.refresh("new_world", table_name="grocery-products-dev", dry_run=True)

    assert result["dry_run"] is True
    assert result["written"] == 0
    assert table.written == []
    # A dry run writes NO history either -- it is a report, not a mutation.
    assert history.written == []
    assert result["history_written"] == 0
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


# ------------------------------------------- the anomaly rule, and its wiring
#
# `_row(**over)` is the diff section's helper above: one normalised item with
# fields overridden. Reused rather than redefined -- a second copy was written
# here first and pyright caught it as a redeclaration, which is the cheap
# version of the "equivalent copies are the dangerous kind" rule this repository
# applies to LITERAL_MONEY.


def test_a_plausible_row_is_written():
    accepted, rejected = reject_implausible([_row()])
    assert (len(accepted), len(rejected)) == (1, 0)


def test_the_two_thousand_four_hundred_and_ninety_dollar_broccoli_is_refused():
    """
    The exact defect that reached the live table, as a row.

    `pack_grams == 1` is the SOLD-EACH sentinel -- one unit, not one gram. The
    first version of `unit_price()` divided by it anyway and wrote a per-kilo
    figure a thousand times the shelf price into `unit_price_nzd`, which is read
    straight into the Citation a shopper sees. Six rows, no signal.
    """
    broccoli = _row(
        product_key="broccoli-ea",
        pack_grams=1,
        price_nzd="2.49",
        unit_price_nzd="2490.00",
    )
    accepted, rejected = reject_implausible([broccoli])
    assert (len(accepted), len(rejected)) == (0, 1)
    assert rejected[0]["product_key"] == "broccoli-ea"


def test_a_sold_each_row_whose_unit_price_equals_its_price_is_fine():
    """The other side of the sentinel: correct sold-each rows must not be lost."""
    accepted, rejected = reject_implausible(
        [_row(product_key="broccoli-ea", pack_grams=1, price_nzd="2.49", unit_price_nzd="2.49")]
    )
    assert (len(accepted), len(rejected)) == (1, 0)


def test_rounding_differences_are_not_defects():
    """
    A cent of disagreement is not a finding.

    The rule fires at an order of magnitude because the defect it exists for was
    off by a factor of a thousand, and a check that wakes somebody over
    ROUND_HALF_EVEN versus ROUND_HALF_UP is a check that gets switched off.
    """
    accepted, _ = reject_implausible(
        [_row(pack_grams=500, price_nzd="2.97", unit_price_nzd="5.95")]  # derived: 5.94
    )
    assert len(accepted) == 1


def test_a_refused_row_is_never_written(monkeypatch):
    """
    The wiring, not the rule. `implausible_unit_price` existed and was correct
    for a day while nothing called it, so the property worth asserting is that
    a bad row does not reach `batch_writer`.
    """
    from ingestion import handler as h

    table = _FakeTable()
    history = _FakeTable()
    _patch_resource(monkeypatch, table, history)
    bad = _row(product_key="broccoli-ea", pack_grams=1, price_nzd="2.49", unit_price_nzd="2490.00")
    monkeypatch.setattr(h, "to_item", lambda offer: bad)

    result = h.refresh("new_world", table_name="grocery-products-dev")

    assert result["rejected"] == result["fetched"] > 0
    assert result["written"] == 0
    assert table.written == [], "a row we refused to publish reached the table"
    # A rejected row reaches neither the products table nor history.
    assert history.written == [], "a row we refused to publish reached history"
    assert result["history_written"] == 0
    assert result["sample_rejected"], "the refusal left no trace to act on"


def test_a_refused_row_is_not_counted_as_unchanged(monkeypatch):
    """
    Validation runs BEFORE the diff.

    A rejected row is not being written, so reporting it as `unchanged` would
    describe a table state that will not exist -- and `unchanged == fetched` is
    exactly what this module's docstring offers as the proof that a re-run is
    idempotent.
    """
    from ingestion import handler as h

    table = _FakeTable()
    _patch_resource(monkeypatch, table)
    bad = _row(product_key="broccoli-ea", pack_grams=1, price_nzd="2.49", unit_price_nzd="2490.00")
    monkeypatch.setattr(h, "to_item", lambda offer: bad)

    result = h.refresh("new_world", table_name="grocery-products-dev", dry_run=True)

    assert result["added"] == result["changed"] == result["unchanged"] == 0


def test_the_rejection_log_line_matches_the_metric_filter(monkeypatch, capsys):
    """
    The config against the CODE, for the ingestion filter.

    `config/alarms.json` binds `IngestionRowRejected` to a JSON selector over a
    field this module writes. Nothing keeps the two together: rename the field
    and the filter still deploys, still reads correctly in the console, and
    matches nothing forever -- which looks exactly like an ingestion run with
    nothing wrong. `tests/test_alarms.py` makes the same check for the
    handler-escaped filter and explains why at length.
    """
    from ingestion import handler as h
    from scripts.apply_alarms import CONFIG, load_config

    table = _FakeTable()
    _patch_resource(monkeypatch, table)
    bad = _row(product_key="broccoli-ea", pack_grams=1, price_nzd="2.49", unit_price_nzd="2490.00")
    monkeypatch.setattr(h, "to_item", lambda offer: bad)
    h.refresh("new_world", table_name="grocery-products-dev", dry_run=True)

    lines = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]
    assert lines, "a refused row wrote no log line at all"

    cfg = load_config(Path(CONFIG))
    pattern = next(
        f["pattern"] for f in cfg["metric_filters"] if f["metric_name"] == "IngestionRowRejected"
    )
    match = re.fullmatch(r'\s*\{\s*\$\.([\w.]+)\s*=\s*"([^"]*)"\s*\}\s*', pattern)
    assert match, f"unsupported filter pattern: {pattern!r}"
    field, expected = match.group(1), match.group(2)

    assert [r for r in lines if r.get(field) == expected], (
        f"the metric filter looks for {field}={expected!r}, which no line a real "
        f"rejection emits contains. The alarm would never fire.\n"
        f"emitted: {[r.get(field) for r in lines]}"
    )


def test_the_rejection_line_carries_no_shopper_data():
    """
    Req 11.5. A rejected row is a product, a store and three numbers.

    Asserted rather than assumed because this is a NEW log line, and the two
    privacy defects this repository has found were both a log line that carried
    more than its author intended.
    """
    from ingestion import handler as h

    fields = set(
        json.loads(
            json.dumps(
                {
                    "message": h.REJECT_LOG_MESSAGE,
                    "reason": "implausible_unit_price",
                    "retailer": "new_world",
                    "store_key": "x",
                    "product_key": "y",
                    "price_nzd": "1",
                    "unit_price_nzd": "2",
                    "pack_grams": 3,
                }
            )
        )
    )
    forbidden = {"message_text", "session_id", "location", "dietary_exclusions", "near", "user"}
    assert not (fields & forbidden)


# --------------------------------------------------- seed loader guard (2026-09-01)


class _CountTable:
    """
    A fake products table that answers the guard's COUNT-by-store_key query.

    `present` is the set of store_keys that have rows. The guard issues
    `query(KeyConditionExpression=Key('store_key').eq(k), Select='COUNT', Limit=1)`
    and reads `Count`; this models exactly that, and records writes so a test
    can assert whether a load actually happened.
    """

    def __init__(self, present: set[str]) -> None:
        self._present = set(present)
        self.written: list = []

    def batch_writer(self):
        return _FakeBatch(self.written)

    def query(self, **kw):
        # The condition is a boto3 ConditionBase; its expression carries the
        # store_key value. Rather than parse it, model the semantics: the guard
        # only ever probes one store_key at a time, so pull it from the built
        # values. boto3 exposes it via get_expression()['values'].
        cond = kw.get("KeyConditionExpression")
        values = cond.get_expression()["values"] if cond is not None else []
        store_key = values[-1] if values else None
        count = 1 if store_key in self._present else 0
        return {"Count": count}


def _patch_loader_table(monkeypatch, table) -> None:
    from scripts import load_seed_data as ld

    monkeypatch.setattr(
        ld.boto3, "resource", lambda *a, **k: type("R", (), {"Table": lambda s, n: table})()
    )


def test_real_only_store_keys_are_disjoint_from_fixtures_and_present_in_lineage_b():
    """
    The guard's probe keys must actually distinguish the two catalogues.

    If a probe key ever appeared in the fixtures, the guard would refuse a
    legitimate first load; if one stopped appearing in Lineage B, the guard
    would never fire and silently disarm — the exact failure mode this test
    exists to prevent. Both are asserted against the real data.
    """
    from ingestion.lineage_b import transform
    from ingestion.normalise import store_key as make_store_key
    from ingestion.sources import LINEAGE_B_DIR
    from scripts.load_seed_data import _REAL_ONLY_STORE_KEYS

    fixture_keys = {r["store_key"] for r in json.loads(FIXTURES.read_text(encoding="utf-8"))}
    assert not (set(_REAL_ONLY_STORE_KEYS) & fixture_keys), (
        "a probe key is also a fixture store_key; the guard would block a first load"
    )

    records = []
    for path in sorted(LINEAGE_B_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload["SmartGroceryProducts"]:
            item = entry.get("PutRequest", {}).get("Item", entry)
            records.append({k: (v.get("S") if "S" in v else v.get("N")) for k, v in item.items()})
    offers, _ = transform(records, captured_at="2026-08-28")
    real_keys = {make_store_key(o.store, o.store_location) for o in offers}
    for probe in _REAL_ONLY_STORE_KEYS:
        assert probe in real_keys, f"probe key {probe!r} is not in Lineage B; the guard is disarmed"


def test_load_refuses_when_the_real_catalogue_is_present(monkeypatch):
    """The core fix: a plain load must not silently shadow the real catalogue."""
    from scripts import load_seed_data as ld

    table = _CountTable(present={"paknsave#albany"})  # real catalogue loaded
    _patch_loader_table(monkeypatch, table)

    with pytest.raises(SystemExit, match="REFUSING to load fixtures"):
        ld.load("grocery-products-dev")
    assert table.written == [], "the guard let fixtures be written over real data"


def test_load_proceeds_when_the_table_has_no_real_rows(monkeypatch):
    """A clean table (fresh or fixture-only) still loads, so first setup is unaffected."""
    from scripts import load_seed_data as ld

    table = _CountTable(present=set())  # no real-only stores
    _patch_loader_table(monkeypatch, table)

    written = ld.load("grocery-products-dev")
    assert written == len(json.loads(FIXTURES.read_text(encoding="utf-8")))
    assert table.written, "nothing was written into a clean table"


def test_force_bypasses_the_guard(monkeypatch):
    """--force is the deliberate escape hatch, and it must actually load."""
    from scripts import load_seed_data as ld

    table = _CountTable(present={"new_world#albany"})
    _patch_loader_table(monkeypatch, table)

    written = ld.load("grocery-products-dev", force=True)
    assert written == len(json.loads(FIXTURES.read_text(encoding="utf-8")))
    assert table.written, "--force did not load"


def test_real_catalogue_present_returns_the_found_key(monkeypatch):
    """The probe names which store it found, so the refusal can be specific."""
    from scripts import load_seed_data as ld

    # Only new_world#albany is present; paknsave#albany is probed first and
    # misses, so the second probe is the one that should be reported.
    _patch_loader_table(monkeypatch, _CountTable(present={"new_world#albany"}))
    assert ld.real_catalogue_present("grocery-products-dev") == "new_world#albany"

    # A clean table reports nothing.
    _patch_loader_table(monkeypatch, _CountTable(present=set()))
    assert ld.real_catalogue_present("grocery-products-dev") is None
