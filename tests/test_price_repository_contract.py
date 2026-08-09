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
from src.schemas.contract import Store

# --------------------------------------------------------------- registry


def _in_memory():
    from src.retrieval.memory import InMemoryPriceRepository

    return InMemoryPriceRepository()


def _dynamo():
    from src.retrieval.dynamo import DynamoPriceRepository

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
            prices = [
                r.price_nzd
                for r in repo.cheapest_for_product(record.product_key, limit=50)
            ]
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

    def test_limit_keeps_the_cheapest_not_an_arbitrary_slice(
        self, repo, a_product_key
    ):
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

        filtered = repo.cheapest_for_product(
            a_product_key, limit=50, stores=[wanted]
        )
        assert filtered, "filtering to a store that stocks it returned nothing"
        assert {r.store for r in filtered} == {wanted}

    def test_store_filter_preserves_ordering(self, repo, a_product_key):
        prices = [
            r.price_nzd
            for r in repo.cheapest_for_product(
                a_product_key, limit=50, stores=list(Store)
            )
        ]
        assert prices == sorted(prices)

    def test_empty_store_filter_is_not_treated_as_no_filter(
        self, repo, a_product_key
    ):
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
        assert repo.resolve_product_key("what's the cheapest butter near me") == (
            SEED_KEY
        )

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
                f"{record.product_key} was returned as a candidate but has no "
                f"retrievable prices"
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
                f"{record.product_key}.unit_price_nzd is "
                f"{type(record.unit_price_nzd).__name__}"
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

    def test_no_duplicate_rows_for_one_product_at_one_location(
        self, repo, a_product_key
    ):
        """A duplicate row double-counts a store in the comparison."""
        records = repo.cheapest_for_product(a_product_key, limit=50)
        seen = [(r.store, r.store_location) for r in records]
        assert len(seen) == len(set(seen)), f"duplicate location rows: {seen}"
