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


def test_a_deployment_does_not_default_to_the_fixture_catalogue(monkeypatch):
    """
    The 2026-09-03 defect, as a test. It ran nightly for days.

    `default_source: fixtures` is the right default for a laptop and the wrong
    one for a Lambda on a schedule: the deployed function resolved a
    `FixtureSource` from the config and wrote 152 invented rows over the real
    catalogue every night, undoing three separate removals. The refusal is what
    makes an unmade choice loud instead of quiet.

    Parametrised over EVERY declared signal rather than the one that matters
    today, so adding a signal to `DEPLOYMENT_ENV_VARS` without arming it fails
    here rather than looking armed in review.
    """
    from ingestion.guard import DEPLOYMENT_ENV_VARS, FixtureGuardError

    monkeypatch.delenv("PRICE_SOURCE", raising=False)
    for signal in DEPLOYMENT_ENV_VARS:
        for other in DEPLOYMENT_ENV_VARS:
            monkeypatch.delenv(other, raising=False)
        monkeypatch.setenv(signal, "grocery-ingestion-dev")
        with pytest.raises(FixtureGuardError, match="refusing to default"):
            resolve_source("paknsave")


def test_an_explicit_fixture_selection_is_still_allowed_in_a_deployment(monkeypatch):
    """
    Only the IMPLICIT default is refused.

    An operator who sets `PRICE_SOURCE=fixtures` on a deployed function has
    chosen the fixture catalogue, and there are honest reasons to (a fresh
    empty table, a smoke test). What stands behind that choice is the second
    guard: `refresh()` still refuses to WRITE those rows over a real catalogue.
    Refusing here as well would only mean the operator sets a different variable.
    """
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "grocery-ingestion-dev")
    monkeypatch.setenv("PRICE_SOURCE", "fixtures")

    assert isinstance(resolve_source("paknsave"), FixtureSource)


def test_a_deployment_selecting_the_real_catalogue_is_not_refused(monkeypatch):
    """The refusal is about the fixtures, not about being deployed."""
    from ingestion.sources import LineageBSource

    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "grocery-ingestion-dev")
    monkeypatch.setenv("PRICE_SOURCE", "lineage_b")

    assert isinstance(resolve_source("paknsave"), LineageBSource)


def test_live_acquisition_wins_over_the_deployment_refusal(monkeypatch):
    """
    The acquisition tripwire is first in the precedence and stays first.

    Both are refusals, so the ORDER only shows up in which one you are told
    about -- and being told "you tried to turn on live acquisition" matters more
    than being told about a source default, because only one of them is about a
    request leaving the account.
    """
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "grocery-ingestion-dev")
    monkeypatch.setenv("LIVE_ACQUISITION", "1")
    monkeypatch.delenv("PRICE_SOURCE", raising=False)

    with pytest.raises(NotImplementedError, match="ACQUISITION-RISK"):
        resolve_source("paknsave")


def test_the_lambda_runtime_variable_is_a_signal_on_its_own():
    """
    `APP_STAGE` cannot be the only signal, and this is why.

    It is UNSET on the deployed function today (setting it is the last step of
    the production cutover), so a check keyed on it alone would read as armed in
    review and do nothing in the account -- the exact shape of control this
    repository keeps finding. `AWS_LAMBDA_FUNCTION_NAME` is set by the runtime
    and cannot be forgotten.
    """
    from ingestion.guard import DEPLOYMENT_ENV_VARS, deployment_signal

    assert "AWS_LAMBDA_FUNCTION_NAME" in DEPLOYMENT_ENV_VARS
    assert (
        deployment_signal({"AWS_LAMBDA_FUNCTION_NAME": "grocery-ingestion-dev"})
        == "AWS_LAMBDA_FUNCTION_NAME=grocery-ingestion-dev"  # pragma: allowlist secret
    )
    # The signal is returned rather than a bool so the refusal can name its
    # evidence; a reader at 3am needs to know WHICH variable said so.
    assert deployment_signal({"APP_STAGE": "pilot"}) == "APP_STAGE=pilot"


def test_an_empty_deployment_variable_is_not_a_deployment():
    """
    `APP_STAGE=""` is what an unset variable looks like to a deploy tool that
    always passes the flag. Treating it as a deployment would refuse laptop runs
    over a variable nobody set.
    """
    from ingestion.guard import deployment_signal

    assert deployment_signal({}) is None
    assert deployment_signal({"APP_STAGE": ""}) is None
    assert deployment_signal({"AWS_LAMBDA_FUNCTION_NAME": "   "}) is None


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


def test_a_missing_lineage_b_catalogue_is_an_error_not_an_empty_refresh(tmp_path):
    """
    The trap the deployment refusal walks people towards, closed.

    Told "set PRICE_SOURCE explicitly", the obvious next move is
    `PRICE_SOURCE=lineage_b` on the deployed function -- where `datasets/` is not
    in the archive (`scripts/build_lambda.py` ships src, config, fixtures and
    ingestion). `Path.glob` on a missing directory yields nothing and raises
    nothing, so that would report `fetched 0, written 0, added 0, changed 0`: a
    refresh that succeeded and did nothing, which is the failure shape this whole
    area keeps producing.
    """
    from ingestion.sources import LineageBSource

    source = LineageBSource("paknsave", path=tmp_path / "not-shipped")

    with pytest.raises(FileNotFoundError, match="does not ship datasets"):
        source.fetch()


def test_an_empty_but_present_catalogue_is_not_an_error(tmp_path):
    """
    The distinction the check turns on. A directory that exists and holds no
    rows for this retailer is a DATA fact -- Woolworths has none in Lineage B at
    all (docs/OPEN-REVIEW-chain-coverage.md) -- and the nightly refresh for that
    retailer must stay a quiet zero rather than a nightly page.
    """
    from ingestion.sources import LineageBSource

    assert LineageBSource("woolworths", path=tmp_path).fetch() == []


def test_the_archive_ships_every_catalogue_the_deployed_function_reads():
    """
    The packaging allowlist against the code that reads from it.

    The deployed ingestion function runs `PRICE_SOURCE=lineage_b` (decided
    2026-09-04, config/data-sources.json), so `LINEAGE_B_DIR` has to be inside
    the Lambda archive. `build_lambda.py`'s INCLUDE_DIRS is an allowlist, and an
    allowlist that stops covering what the code reads is a packaging change
    nothing offline would otherwise notice: every test here resolves these paths
    against the REPO, where they always exist, and the failure only appears in
    the account. `fixtures` is asserted for the same reason and has been shipped
    since before ingestion had a second source.
    """
    from ingestion.sources import FIXTURES, LINEAGE_B_DIR
    from scripts.build_lambda import INCLUDE_DIRS, ROOT

    shipped = [(ROOT / entry).resolve() for entry in INCLUDE_DIRS]

    for needed in (LINEAGE_B_DIR, FIXTURES):
        target = needed.resolve()
        assert any(target == d or target.is_relative_to(d) for d in shipped), (
            f"{needed} is read at runtime but no INCLUDE_DIRS entry covers it; "
            f"the deployed function would not find it. Shipped: {INCLUDE_DIRS}"
        )


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
    """
    A products table that answers BOTH query shapes this code issues.

    `_existing()` queries by store_key and reads `Items`; the fixture guard
    probes with `Select="COUNT"` and reads `Count`. A fake that modelled only
    the first would answer the guard's probe with no `Count` key at all, the
    guard would read that as zero, and every test in this file would exercise
    the not-guarded path while looking like it had a guard. `real_only` is the
    set of store keys this table pretends the real catalogue occupies.
    """

    def __init__(self, rows: list | None = None, real_only: set[str] | None = None) -> None:
        self.written: list = []
        self._rows = rows or []
        self._real_only = set(real_only or ())

    def batch_writer(self):
        return _FakeBatch(self.written)

    def query(self, **kw):
        if kw.get("Select") == "COUNT":
            # The condition is a boto3 ConditionBase; rather than parse its
            # expression, take the value it was built with -- the probe only
            # ever asks about one store_key at a time.
            cond = kw.get("KeyConditionExpression")
            values = cond.get_expression()["values"] if cond is not None else []
            store_key = values[-1] if values else None
            return {"Count": 1 if store_key in self._real_only else 0}
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
    from ingestion.guard import REAL_ONLY_STORE_KEYS
    from ingestion.lineage_b import transform
    from ingestion.normalise import store_key as make_store_key
    from ingestion.sources import LINEAGE_B_DIR

    fixture_keys = {r["store_key"] for r in json.loads(FIXTURES.read_text(encoding="utf-8"))}
    assert not (set(REAL_ONLY_STORE_KEYS) & fixture_keys), (
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
    for probe in REAL_ONLY_STORE_KEYS:
        assert probe in real_keys, f"probe key {probe!r} is not in Lineage B; the guard is disarmed"


def test_load_refuses_when_the_real_catalogue_is_present(monkeypatch):
    """The core fix: a plain load must not silently shadow the real catalogue."""
    from scripts import load_seed_data as ld

    table = _FakeTable(real_only={"paknsave#albany"})  # real catalogue loaded
    _patch_loader_table(monkeypatch, table)

    with pytest.raises(SystemExit, match="REFUSING to load fixtures"):
        ld.load("grocery-products-dev")
    assert table.written == [], "the guard let fixtures be written over real data"


def test_load_proceeds_when_the_table_has_no_real_rows(monkeypatch):
    """A clean table (fresh or fixture-only) still loads, so first setup is unaffected."""
    from scripts import load_seed_data as ld

    table = _FakeTable()  # no real-only stores
    _patch_loader_table(monkeypatch, table)

    written = ld.load("grocery-products-dev")
    assert written == len(json.loads(FIXTURES.read_text(encoding="utf-8")))
    assert table.written, "nothing was written into a clean table"


def test_force_bypasses_the_guard(monkeypatch):
    """--force is the deliberate escape hatch, and it must actually load."""
    from scripts import load_seed_data as ld

    table = _FakeTable(real_only={"new_world#albany"})
    _patch_loader_table(monkeypatch, table)

    written = ld.load("grocery-products-dev", force=True)
    assert written == len(json.loads(FIXTURES.read_text(encoding="utf-8")))
    assert table.written, "--force did not load"


def test_real_catalogue_present_returns_the_found_key():
    """
    The probe names which store it found, so both refusals can be specific.

    Takes a table rather than a table NAME, which is what lets the loader and
    `refresh()` share it: each hands over the table object it is about to write
    to, so the probe is always asking about the write it is guarding rather than
    about a table of the same name it opened itself.
    """
    from ingestion.guard import real_catalogue_present

    # Only new_world#albany is present; paknsave#albany is probed first and
    # misses, so the second probe is the one that should be reported.
    assert real_catalogue_present(_FakeTable(real_only={"new_world#albany"})) == "new_world#albany"

    # A clean table reports nothing.
    assert real_catalogue_present(_FakeTable()) is None


def test_the_loader_and_the_lambda_share_one_probe():
    """
    Two copies of "is the real catalogue there?" are two things to keep in step,
    and the one that drifts is the one nobody is looking at. The seed loader
    imports the guard rather than carrying its own copy.
    """
    from ingestion import guard
    from scripts import load_seed_data as ld

    assert ld.real_catalogue_present is guard.real_catalogue_present


# ----------------------------------------- the ingestion write guard (2026-09-03)


def test_refresh_refuses_to_write_fixtures_over_the_real_catalogue(monkeypatch):
    """
    The vector PR #64 did not close, closed.

    That PR guarded `scripts/load_seed_data.py` -- the path a human could take.
    The account check on 2026-09-03 found the SCHEDULED LAMBDA taking a path no
    human took, every night at 03:18, writing the same 152 fixture rows back
    over the real catalogue. A guard on the loader could never have reached it,
    which is why this one lives where the write is.
    """
    from ingestion import handler as h
    from ingestion.guard import FixtureGuardError

    table = _FakeTable(real_only={"paknsave#albany"})
    _patch_resource(monkeypatch, table)

    with pytest.raises(FixtureGuardError, match="refusing to refresh"):
        h.refresh("paknsave", table_name="grocery-products-dev")

    assert table.written == [], "the guard let the fixture catalogue over the real one"


def test_the_refusal_names_the_store_key_it_found(monkeypatch):
    """
    A refusal that cannot be checked is one an operator has to take on trust.

    Naming the probe key means the next question -- "is the real catalogue
    actually in there?" -- is answerable with one query rather than an argument.
    """
    from ingestion import handler as h
    from ingestion.guard import FixtureGuardError

    _patch_resource(monkeypatch, _FakeTable(real_only={"new_world#albany"}))

    with pytest.raises(FixtureGuardError, match="new_world#albany"):
        h.refresh("new_world", table_name="grocery-products-dev")


def test_the_refusal_applies_to_a_dry_run_too(monkeypatch):
    """
    The part that looks wrong until you say it out loud.

    A dry run reports what a real run WOULD do. The real run refuses, so a dry
    run that answered with a cheerful diff would be describing a table state
    that will never exist -- the same reason `reject_implausible` runs before
    the diff rather than after it.
    """
    from ingestion import handler as h
    from ingestion.guard import FixtureGuardError

    table = _FakeTable(real_only={"paknsave#albany"})
    _patch_resource(monkeypatch, table)

    with pytest.raises(FixtureGuardError, match="refusing to refresh"):
        h.refresh("paknsave", table_name="grocery-products-dev", dry_run=True)


def test_a_first_load_into_a_table_with_no_real_catalogue_still_works(monkeypatch):
    """
    The guard fires on the real catalogue being PRESENT, not on the fixtures
    being selected. An empty table has nothing to shadow, so seeding one is
    unaffected -- which is what makes it safe to have no `force` here at all.
    """
    from ingestion import handler as h

    table = _FakeTable()
    _patch_resource(monkeypatch, table)

    result = h.refresh("new_world", table_name="grocery-products-dev")

    assert result["written"] == result["fetched"] > 0


def test_a_real_source_is_not_refused_by_the_fixture_guard(monkeypatch):
    """
    The guard keys on the SOURCE, not on the table having rows.

    Refusing to write to a table that holds the real catalogue would refuse the
    nightly refresh -- the job whose entire purpose is to write to that table.
    What is refused is the fixture catalogue reaching it.
    """
    from ingestion import handler as h

    class _CollectedSource:
        retailer = "paknsave"

        def fetch(self):
            return [_offer(product_key="standard-milk-2l", store_location="Albany")]

    monkeypatch.setattr(h, "resolve_source", lambda retailer: _CollectedSource())
    table = _FakeTable(real_only={"paknsave#albany"})
    _patch_resource(monkeypatch, table)

    result = h.refresh("paknsave", table_name="grocery-products-dev")

    assert result["written"] == 1
    assert table.written, "a refresh from the collected catalogue was blocked"


def test_a_failed_refresh_writes_the_line_the_alarm_reads(monkeypatch, capsys):
    """
    A thrown branch is invisible to every alarm unless the Lambda says so first.

    `config/ingestion-state-machine.json` catches `States.ALL` INSIDE the item
    processor and routes it to a Pass state, so one retailer throwing leaves the
    other two intact and the EXECUTION SUCCEEDS. That is the right state machine
    -- and it means `AWS/States ExecutionsFailed` reports nothing for the very
    failure it reads as covering. This line is the only place the fact survives.
    """
    from ingestion import handler as h
    from ingestion.guard import FixtureGuardError

    _patch_resource(monkeypatch, _FakeTable(real_only={"paknsave#albany"}))

    with pytest.raises(FixtureGuardError):
        h.lambda_handler({"retailer": "paknsave"})

    records = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]
    failures = [r for r in records if r.get("message") == h.REFRESH_FAILED_LOG_MESSAGE]

    assert failures, f"no {h.REFRESH_FAILED_LOG_MESSAGE} line; lines seen: {records}"
    assert failures[0]["retailer"] == "paknsave"
    # The error class and message are what make the alarm actionable: they are
    # the difference between "ingestion is broken" and "the fixture guard fired".
    assert failures[0]["error"] == "FixtureGuardError"
    assert "refusing to refresh" in failures[0]["detail"]


def test_the_failure_line_covers_a_bad_event_too(capsys):
    """
    Not only the guard. An unknown retailer is a state-machine definition bug,
    and the Catch hides it exactly as thoroughly.
    """
    from ingestion import handler as h

    with pytest.raises(ValueError, match="retailer must be one of"):
        h.lambda_handler({"retailer": "countdown"})

    records = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]
    failures = [r for r in records if r.get("message") == h.REFRESH_FAILED_LOG_MESSAGE]

    assert failures, f"no {h.REFRESH_FAILED_LOG_MESSAGE} line; lines seen: {records}"
    assert failures[0]["error"] == "ValueError"
    assert failures[0]["retailer"] == "countdown"


def test_a_successful_refresh_writes_no_failure_line(monkeypatch, capsys):
    """
    The other half of an alarm that means something: it must be quiet when
    nothing is wrong. A metric filter that also matched a healthy run would page
    somebody every night.
    """
    from ingestion import handler as h

    _patch_resource(monkeypatch, _FakeTable())

    h.lambda_handler({"retailer": "new_world"})

    assert h.REFRESH_FAILED_LOG_MESSAGE not in capsys.readouterr().out


# --------------------------------------------------------- price history


def test_history_failure_does_not_lose_the_products_write(monkeypatch, capsys):
    """
    A refresh whose history append fails still WROTE PRICES, and must say so.

    THE ORDERING IS THE WHOLE POINT. `refresh()` writes products first, then
    appends history. Before this guard the history write was unconditional, so
    an exception there failed a Step Functions branch whose actual job -- the
    prices a shopper reads -- had already been done, and reported a working
    refresh as a broken one.

    Same trade `src/handler.py` makes for the idempotency store: bookkeeping
    that fails must not discard work that succeeded. History is bookkeeping in
    exactly that sense -- ops/reviewer only, never a Citation, and reproducible
    by re-running ingestion over the same source.
    """
    from ingestion import handler as h

    products = _FakeTable()

    def _table(_self, name):
        if name == h.HISTORY_TABLE:
            raise RuntimeError("ResourceNotFoundException: table does not exist")
        return products

    monkeypatch.setattr(h.boto3, "resource", lambda *a, **k: type("R", (), {"Table": _table})())

    result = h.refresh("new_world", table_name="grocery-products-dev")

    assert result["written"] > 0, "the products write must still have happened"
    assert result["history_written"] == 0, "and the history contribution reports zero"

    # Visible, not swallowed. The metric filter binds to this line, and
    # tests/test_alarms.py asserts the pattern matches it.
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip().startswith("{")]
    assert any(json.loads(ln).get("message") == h.HISTORY_FAILED_LOG_MESSAGE for ln in lines), (
        "a degraded history write must emit the structured line the alarm is derived from"
    )


def test_history_is_not_written_on_a_dry_run(monkeypatch):
    """
    A dry run is a report, not a mutation -- on BOTH tables.

    Worth its own test because the history append moved into a helper: a guard
    that protects the products write and not the history one would make
    `--dry-run` write half a refresh, which is the shape of mistake that is
    only ever found in production.
    """
    from ingestion import handler as h

    table = _FakeTable()
    history = _FakeTable()
    _patch_resource(monkeypatch, table, history)

    result = h.refresh("new_world", table_name="grocery-products-dev", dry_run=True)

    assert result["written"] == 0
    assert result["history_written"] == 0
    assert history.written == []
