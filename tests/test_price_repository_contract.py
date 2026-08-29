"""
The PriceRepository contract (Task 2.10).

ONE suite, parameterised over EVERY implementation of the protocol. design.md
§7 has asserted since the beginning that "the stored implementations must
satisfy the same tests as the fixture ones" — while every other test in this
repo constructs InMemoryPriceRepository directly, so nothing enforced it. This
file is what makes that sentence true.

Written BEFORE the DynamoDB adapter exists, deliberately. Written first, it is
the specification that implementation is built to satisfy. Written afterwards,
it becomes a description of whatever that implementation happened to do — which
is a different and much less useful artefact.

HOW TO USE IT WHEN THE ACCOUNT LANDS

    PRICE_REPO_DYNAMO_TABLE=grocery-products-dev python -m pytest \\
        tests/test_price_repository_contract.py

Without that variable the DynamoDB parameter skips, so CI stays credential-free
(design.md §7). With it set, the adapter must pass every test below unmodified.
Adding an implementation should mean running these tests, not writing new ones.

TWO RULES THIS SUITE FOLLOWS, AND WHY

1. PROTOCOL MEMBERS ONLY. No test may touch InMemoryPriceRepository internals
   (`_records`, `all_categories`, …). Anything convenient that is not on the
   Protocol is exactly what will not exist on the stored implementation. Test
   data is therefore *discovered* through `candidates_for_budget`, which is on
   the Protocol, rather than read out of a fixture file.

2. PROPERTIES, NOT TRANSCRIPTS. Assertions are about invariants that must hold
   for any conforming store — ordering, limits, types, exclusions — not about
   specific prices, which differ between a seed fixture and live scraped data.
   The few tests that do need specific products are marked SEEDED and depend
   only on the canonical product keys the seed generator produces.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from src.graph.nodes import MEAL_CATEGORIES
from src.retrieval.base import PriceRecord
from src.retrieval.dynamo import MAX_QUERY_PAGES, DynamoPriceRepository
from src.retrieval.memory import InMemoryPriceRepository
from src.schemas.contract import Store

# The row count past which a full-table Scan per meal-plan turn stops being
# defensible. Not a DynamoDB limit — a judgement, set an order of magnitude
# above the current fixture set so ordinary growth does not trip it, and far
# below the point where the read cost of scanning a real catalogue to return
# fifteen rows becomes the dominant cost of a turn.
SCAN_CEILING_RECORDS = 1000

# --------------------------------------------------------------- registry


def _in_memory():

    return InMemoryPriceRepository()


def _dynamo():

    table = os.environ.get("PRICE_REPO_DYNAMO_TABLE")
    if not table:
        pytest.skip(
            "DynamoDB not configured. Set PRICE_REPO_DYNAMO_TABLE to run the "
            "contract against it. Skipped, not passed — this implementation "
            "is UNVERIFIED against the contract."
        )
    return DynamoPriceRepository(table_name=table)


IMPLEMENTATIONS = [
    pytest.param(_in_memory, id="in_memory"),
    pytest.param(_dynamo, id="dynamodb"),
]


@pytest.fixture(params=IMPLEMENTATIONS)
def repo(request):
    """
    One implementation of the protocol, ready to query.

    NotImplementedError is caught HERE and nowhere else. Construction raising it
    means the adapter is scaffolding and has not been built yet — a skip. A
    *method* raising it mid-test means the adapter claims to exist but does not
    honour the contract, and that must fail loudly rather than be swallowed into
    a green run.
    """
    try:
        return request.param()
    except NotImplementedError as exc:
        pytest.skip(f"implementation is scaffolding, not built yet: {exc}")


# --------------------------------------------------------------- discovery


@pytest.fixture
def known_products(repo) -> list[PriceRecord]:
    """
    Whatever this store actually holds, found through the protocol.

    Deliberately not read from fixtures/products.json: a test that knows the
    fixture file cannot run against DynamoDB, which is the entire point of this
    suite.
    """
    records = repo.candidates_for_budget(
        categories=MEAL_CATEGORIES,
        exclude_categories=[],
        limit_per_category=50,
    )
    if not records:
        pytest.fail(
            "store returned no candidates across every category — it is empty, "
            "or candidates_for_budget does not work. Either way the rest of "
            "this suite would produce vacuous passes."
        )
    return records


@pytest.fixture
def a_product_key(known_products) -> str:
    return known_products[0].product_key


# Seed-dataset assumptions. A conforming store is loaded from the same
# generator (Task 7.4), so these canonical keys exist wherever the data does.
SEED_TERM = "butter"
SEED_KEY = "butter-500g"
# The documented mis-match: "truffle oil" contains "oil", and substring matching
# resolves it to canola oil — a confident, cited, wrong price (Req 4.3).
FUZZY_TRAP_TERM = "truffle oil"
FUZZY_TRAP_VICTIM = "oil-canola-750ml"


# =============================================================== ordering
# Req 1.1, 8.2. The GSI exists so this is one query already sorted; the
# in-memory store pre-sorts. Both must present the same order to a caller.


class TestCheapestForProduct:
    def test_returns_prices_cheapest_first(self, repo, known_products):
        """The ordering guarantee, checked on every product the store holds."""
        for record in known_products:
            prices = [r.price_nzd for r in repo.cheapest_for_product(record.product_key, limit=50)]
            assert prices == sorted(prices), (
                f"{record.product_key} came back unsorted: {prices}. Callers "
                f"take element 0 as the cheapest and never re-sort."
            )

    def test_unknown_product_returns_empty_not_an_error(self, repo):
        """
        Req 4.1/4.2: absence is a success outcome the caller routes on, not an
        exception it has to catch.
        """
        assert repo.cheapest_for_product("no-such-product-99kg") == []

    def test_every_record_is_the_requested_product(self, repo, a_product_key):
        """Cross-contamination would show one product's price under another."""
        for record in repo.cheapest_for_product(a_product_key, limit=50):
            assert record.product_key == a_product_key

    def test_limit_is_respected(self, repo, a_product_key):
        assert len(repo.cheapest_for_product(a_product_key, limit=2)) <= 2

    def test_limit_keeps_the_cheapest_not_an_arbitrary_slice(self, repo, a_product_key):
        """
        A store that applied the limit before sorting would pass the ordering
        test and still return the wrong rows.
        """
        everything = repo.cheapest_for_product(a_product_key, limit=50)
        if len(everything) < 2:
            pytest.skip("product is stocked at fewer than two stores")

        limited = repo.cheapest_for_product(a_product_key, limit=1)
        assert limited[0].price_nzd == everything[0].price_nzd

    def test_store_filter_excludes_other_stores(self, repo, a_product_key):
        """Req 1.5-adjacent: a preferred-store constraint must actually bind."""
        everything = repo.cheapest_for_product(a_product_key, limit=50)
        wanted = everything[0].store

        filtered = repo.cheapest_for_product(a_product_key, limit=50, stores=[wanted])
        assert filtered, "filtering to a store that stocks it returned nothing"
        assert {r.store for r in filtered} == {wanted}

    def test_store_filter_preserves_ordering(self, repo, a_product_key):
        prices = [
            r.price_nzd
            for r in repo.cheapest_for_product(a_product_key, limit=50, stores=list(Store))
        ]
        assert prices == sorted(prices)

    def test_empty_store_filter_is_not_treated_as_no_filter(self, repo, a_product_key):
        """
        An explicit empty list means "no store qualifies". Callers pass None for
        "any store" — the retrieval node does exactly that. Coercing [] to None
        would silently widen a filter, which is the dangerous direction.
        """
        assert repo.cheapest_for_product(a_product_key, stores=[]) == []


# =============================================================== resolution
# Req 4.3 and design.md §8. The highest-risk function in the layer: a wrong
# match produces a confident, cited, wrong price.


class TestResolveProductKey:
    def test_resolves_a_plain_term(self, repo):
        """SEEDED."""
        assert repo.resolve_product_key(SEED_TERM) == SEED_KEY

    def test_strips_noise_words(self, repo):
        """SEEDED. The model is meant to send a clean term; users do not."""
        assert repo.resolve_product_key("what's the cheapest butter near me") == (SEED_KEY)

    def test_is_case_insensitive(self, repo):
        """SEEDED."""
        assert repo.resolve_product_key(SEED_TERM.upper()) == SEED_KEY

    def test_unknown_term_returns_none(self, repo):
        assert repo.resolve_product_key("wagyu ribeye") is None

    def test_empty_input_returns_none(self, repo):
        for junk in ("", "   ", "the of a"):
            assert repo.resolve_product_key(junk) is None, f"{junk!r} resolved"

    def test_does_not_fuzzy_match_a_qualified_term(self, repo):
        """
        SEEDED. THE test in this file.

        'truffle oil' must not resolve to canola oil. Substring matching is the
        tempting implementation and it produces a confidently wrong price, which
        is worse than no answer. Under-matching is recoverable; mis-matching
        silently misleads. Do not relax this to raise an eval score —
        design.md §8 records it as a decision, not a limitation.
        """
        resolved = repo.resolve_product_key(FUZZY_TRAP_TERM)
        assert resolved != FUZZY_TRAP_VICTIM, (
            f"{FUZZY_TRAP_TERM!r} resolved to {FUZZY_TRAP_VICTIM!r} — the store "
            f"is substring matching. This is the exact failure Req 4.3 forbids."
        )
        assert resolved is None or resolved.startswith("truffle")

    def test_an_unstocked_modifier_does_not_fall_back_to_the_base_product(
        self, repo, known_products
    ):
        """
        Generalises the truffle-oil case across the catalogue: prefixing any
        stocked product with an unknown qualifier must not resolve to it.
        """
        for record in known_products[:8]:
            base = repo.resolve_product_key(record.canonical_name)
            if base is None:
                continue
            qualified = repo.resolve_product_key(f"artisanal {record.canonical_name}")
            assert qualified != base or qualified is None, (
                f"'artisanal {record.canonical_name}' resolved to {base!r}; an "
                f"unrecognised modifier must yield None, not the base product."
            )

    def test_resolution_round_trips_to_records(self, repo):
        """SEEDED. A key that resolves but returns nothing is a broken mapping."""
        key = repo.resolve_product_key(SEED_TERM)
        assert key is not None
        assert repo.cheapest_for_product(key), (
            f"{SEED_TERM!r} resolved to {key!r} but that key has no prices"
        )

    def test_every_discovered_key_is_queryable(self, repo, known_products):
        """Whatever the store advertises, it must be able to serve."""
        for record in known_products[:15]:
            assert repo.cheapest_for_product(record.product_key), (
                f"{record.product_key} was returned as a candidate but has no retrievable prices"
            )


# =============================================================== candidates
# Req 5.1/5.4. Exclusions are safety-critical and verified against retrieved
# products, never against what a model claims it applied.


class TestCandidatesForBudget:
    def test_excluded_categories_never_appear(self, repo):
        """
        Dietary safety. A single leaked record here is a seafood product in an
        allergic user's meal plan.
        """
        excluded = ["seafood"]
        records = repo.candidates_for_budget(
            categories=MEAL_CATEGORIES,
            exclude_categories=excluded,
            limit_per_category=10,
        )
        leaked = [r for r in records if r.category in excluded]
        assert not leaked, (
            f"excluded category leaked: {[(r.product_key, r.category) for r in leaked]}"
        )

    def test_exclusion_beats_inclusion_on_conflict(self, repo):
        """
        The same category in both lists must exclude. Dropping a restriction is
        the dangerous direction of error; dropping an inclusion is merely
        disappointing.
        """
        records = repo.candidates_for_budget(
            categories=["dairy", "seafood"],
            exclude_categories=["seafood"],
            limit_per_category=10,
        )
        assert all(r.category != "seafood" for r in records)

    def test_only_requested_categories_are_returned(self, repo):
        records = repo.candidates_for_budget(
            categories=["dairy"], exclude_categories=[], limit_per_category=10
        )
        assert {r.category for r in records} <= {"dairy"}

    def test_limit_per_category_is_respected(self, repo):
        limit = 2
        records = repo.candidates_for_budget(
            categories=MEAL_CATEGORIES,
            exclude_categories=[],
            limit_per_category=limit,
        )
        by_category: dict[str, set[str]] = {}
        for record in records:
            by_category.setdefault(record.category, set()).add(record.product_key)

        for category, keys in by_category.items():
            assert len(keys) <= limit, f"{category} returned {len(keys)} products"

    def test_excluding_everything_returns_nothing(self, repo):
        assert (
            repo.candidates_for_budget(
                categories=MEAL_CATEGORIES,
                exclude_categories=MEAL_CATEGORIES,
                limit_per_category=5,
            )
            == []
        )

    def test_unknown_category_is_empty_not_an_error(self, repo):
        assert (
            repo.candidates_for_budget(
                categories=["not-a-category"],
                exclude_categories=[],
                limit_per_category=5,
            )
            == []
        )

    def test_candidates_are_distinct_products(self, repo, known_products):
        """
        Pre-filtering exists to hand the planner a viable *set*. The same
        product at six stores is one choice, not six.
        """
        keys = [r.product_key for r in known_products]
        assert len(keys) == len(set(keys)), "candidates repeated a product"


# =============================================================== record shape
# AGENTS.md: money is Decimal in Python, string on the wire and in storage.
# Never float. The numeric DynamoDB type round-trips through float in most
# paths, which is how a shopping list acquires a total of $23.159999999998.


class TestRecordShape:
    def test_money_is_decimal_never_float(self, repo, known_products):
        for record in known_products:
            assert isinstance(record.price_nzd, Decimal), (
                f"{record.product_key}.price_nzd is {type(record.price_nzd).__name__}. "
                f"Parse the stored string to Decimal; do not use the numeric type."
            )
            assert isinstance(record.unit_price_nzd, Decimal), (
                f"{record.product_key}.unit_price_nzd is {type(record.unit_price_nzd).__name__}"
            )

    def test_money_survives_a_round_trip_exactly(self, repo, known_products):
        """
        A Decimal built from a float carries the float's error. Re-parsing the
        record's own string form must reproduce it bit for bit.
        """
        for record in known_products:
            assert Decimal(str(record.price_nzd)) == record.price_nzd

    def test_prices_are_positive(self, repo, known_products):
        for record in known_products:
            assert record.price_nzd > 0, f"{record.product_key} priced at zero or less"

    def test_required_fields_are_populated(self, repo, known_products):
        for record in known_products:
            assert record.product_key
            assert record.display_name, "the retailer's own wording is shown to users"
            assert record.category
            assert record.unit
            assert record.valid_date, "Req 1.3/7.8: capture date is surfaced in the UI"

    def test_store_is_the_contract_enum(self, repo, known_products):
        """A raw string here reaches the wire and breaks the frontend's mapping."""
        for record in known_products:
            assert isinstance(record.store, Store)

    def test_pack_grams_is_an_int(self, repo, known_products):
        """Quantities are scaled by this; a float would reintroduce drift."""
        for record in known_products:
            assert isinstance(record.pack_grams, int)

    def test_no_duplicate_rows_for_one_product_at_one_location(self, repo, a_product_key):
        """A duplicate row double-counts a store in the comparison."""
        records = repo.cheapest_for_product(a_product_key, limit=50)
        seen = [(r.store, r.store_location) for r in records]
        assert len(seen) == len(set(seen)), f"duplicate location rows: {seen}"


# ================================= Pilot Task 6: query pagination and scan scale
#
# `cheapest_for_product` issued ONE query with `Limit=limit * 5` and ignored
# `LastEvaluatedKey`. DynamoDB applies `Limit` to items READ, before any
# application-side filter, so when a store filter was supplied and none of the
# first page happened to be at those stores, the method returned an empty list.
#
# The graph reads an empty list as `no_data` and tells the shopper "I don't have
# price data for butter" — about a product that store stocks. An honest-failure
# outcome produced by a silent truncation is worse than a loud error, because it
# is indistinguishable from the truth.
#
# It cannot fire on the fixtures: six records per product is a single page. It
# fires at real scale, where a popular product spans three chains and many
# stores.


def _item(ref: int, store: str, location: str, price: str) -> dict:
    return {
        "product_key": "butter-500g",
        "store": store,
        "store_location": location,
        "display_name": f"Butter {ref}",
        "canonical_name": "butter",
        "category": "dairy",
        "price_nzd": Decimal(price),
        "unit": "500g",
        "unit_price_nzd": Decimal(price) * 2,
        "pack_grams": 500,
        "on_special": False,
        "valid_date": "2026-07-31",
        "lat": Decimal("-36.9"),
        "lon": Decimal("174.8"),
        "store_key": f"{store}#{location}",
    }


class _PagingTable:
    """A GSI that hands back one page at a time, as DynamoDB does."""

    def __init__(self, pages: list[list[dict]]) -> None:
        self._pages = pages
        self.queries = 0

    def query(self, **kwargs):
        self.queries += 1
        index = int(kwargs.get("ExclusiveStartKey", {}).get("n", 0))
        page = self._pages[index] if index < len(self._pages) else []
        response: dict = {"Items": page}
        if index + 1 < len(self._pages):
            response["LastEvaluatedKey"] = {"n": index + 1}
        return response


def _repo_with(pages: list[list[dict]]) -> tuple[DynamoPriceRepository, _PagingTable]:
    """A repository wired to a fake table, without touching AWS."""
    repo = object.__new__(DynamoPriceRepository)
    table = _PagingTable(pages)
    repo._table = table  # type: ignore[attr-defined]
    repo._table_name = "grocery-products-dev"  # type: ignore[attr-defined]
    return repo, table


def test_a_store_filter_does_not_report_no_data_for_a_stocked_product():
    """
    The defect, stated as the shopper sees it.

    Page one is entirely PAK'nSAVE; the Woolworths price the shopper asked for
    is on page two. Before pagination this returned [] and the graph said "I
    don't have price data for that".
    """
    pages = [
        [_item(i, "paknsave", "mangere", "2.9") for i in range(5)],
        [_item(9, "woolworths", "ponsonby", "3.5")],
    ]
    repo, table = _repo_with(pages)

    found = repo.cheapest_for_product("butter-500g", limit=5, stores=[Store.WOOLWORTHS])

    assert len(found) == 1, "the second page holds the only matching store"
    assert found[0].store is Store.WOOLWORTHS
    assert table.queries == 2, "the first page was short of matches; follow the key"


def test_paging_stops_as_soon_as_enough_matches_are_held():
    """Bounded work: no reason to read page two when page one satisfied the limit."""
    pages = [
        [_item(i, "paknsave", "mangere", "2.9") for i in range(5)],
        [_item(9, "paknsave", "albany", "3.5")],
    ]
    repo, table = _repo_with(pages)

    found = repo.cheapest_for_product("butter-500g", limit=3)

    assert len(found) == 3
    assert table.queries == 1, "stop once the limit is satisfied"


def test_paging_is_bounded_when_nothing_ever_matches():
    """
    Latency has to stay bounded against the gateway ceiling. Exhausting the cap
    is the honest `no_data` case: this store has nothing near the cheapest end.
    """
    pages = [[_item(i, "paknsave", "mangere", "2.9")] for i in range(50)]
    repo, table = _repo_with(pages)

    found = repo.cheapest_for_product("butter-500g", limit=5, stores=[Store.NEW_WORLD])

    assert found == []
    assert table.queries == MAX_QUERY_PAGES, "must not walk the whole index"


def test_the_scan_ceiling_is_asserted_rather_than_assumed():
    """
    `candidates_for_budget` SCANS the products table on every meal-plan turn.

    That is defensible at fixture scale — 152 records is one page — and
    indefensible at pilot scale, where a scan reads the entire catalogue to
    return about fifteen rows. DYNAMODB-SCHEMA.md says the replacement (a
    category index, or a materialised candidate view) must be chosen from real
    access patterns and load evidence, and there is currently neither: no
    deployment, no traffic.

    So the decision is deferred deliberately, and this test is what stops
    "accepted for the fixture dataset" quietly becoming production. When the
    seed set grows past the ceiling, this fails and forces the choice.
    """
    records = InMemoryPriceRepository().all_records
    assert len(records) <= SCAN_CEILING_RECORDS, (
        f"the products dataset has {len(records)} records, past the {SCAN_CEILING_RECORDS} "
        f"at which a full-table Scan per meal-plan turn stops being defensible. "
        f"Pilot Task 6 requires choosing a queryable candidate pattern before "
        f"going further — see DYNAMODB-SCHEMA.md, 'Location, freshness and "
        f"meal-candidate access patterns'."
    )
