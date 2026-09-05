"""
Retrieval, and the terminals that describe what it could not find.

SPLIT OUT OF `src/graph/nodes/__init__.py` ON 2026-08-31, when that file reached
925 lines. Purely mechanical: every function below is byte-identical to the one
that was there, and `__init__` re-exports all of them, so no caller changed and
the graph is unaltered.

WHAT MAKES THIS THE RIGHT SEAM. `retrieve_prices` is the ONLY creator of
Citations, which is one of the three independent enforcements of Invariant 1 --
no price may originate from model generation. Everything in this module is
either that node, a filter it hands to the repository, or a terminal that says
honestly what retrieval could not find: a place we cannot map, prices too old to
stand behind, or nothing at all. Those four facts belong together, and the
topology is easier to review when the file that owns the grounding boundary
contains only the grounding boundary.

The three honest-refusal terminals here are deliberately DISTINCT from one
another and from the plan-path terminals in `__init__`. "I have nothing for
that", "everything I have is six weeks old" and "I don't know where that is"
are three different facts about a request, and collapsing any two of them puts
a false statement in the shopper's hands.
"""

from __future__ import annotations

from datetime import date

from src.graph.dietary import map_exclusions
from src.graph.feasibility import minimum_spend
from src.graph.nodes._shared import _join, _next_seq
from src.graph.recipe_plan import (
    TooManyIngredients,
    affordable_set,
    curated_recipes,
    meals_needed,
    resolve_ingredients,
    shortlist,
)
from src.graph.regions import known_regions, locations_for, resolve_region
from src.graph.state import GroceryState
from src.retrieval.base import PriceRepository
from src.retrieval.filters import (
    FreshnessFilter,
    NearFilter,
    max_price_age_days,
    reference_date,
)
from src.schemas.contract import (
    Citation,
    CitationEvent,
    ErrorCode,
    ErrorEvent,
    Intent,
    NoDataEvent,
    NoticeEvent,
    SourceRef,
)

# A pathological request ("prices for fifty things") would blow the latency
# budget against the gateway's 29-second ceiling.
MAX_ITEMS_PER_TURN = 5

MEAL_CATEGORIES = [
    "pantry",
    "produce",
    "meat",
    "dairy",
    "frozen",
    "bakery",
    "chilled",
    "seafood",
]


def _near_filter(state: GroceryState) -> NearFilter | None:
    """
    The request's location as a repository filter, or None for national.

    Req 1.6 is explicit that no location means national results rather than a
    refusal, so None here is a legitimate answer and not a missing value.
    """
    location = state.get("location") or {}
    lat, lon = location.get("lat"), location.get("lon")
    # Coordinates are OPTIONAL since regions became expressible. A location
    # carrying only a region has no point to measure from, and the contract
    # already refuses a location that expresses neither.
    if lat is None or lon is None:
        return None
    return NearFilter(
        lat=float(lat),
        lon=float(lon),
        radius_km=float(location.get("radius_km") or 10.0),
    )


def _location_scope(state: GroceryState) -> tuple[frozenset[str] | None, str | None]:
    """
    The store-location scope for this turn, and any region we could not map.

    Two ways a region arrives. A client may send one structurally
    (`location.region`), which is the shape a dropdown produces. Or the shopper
    may simply say it — "cheapest milk near Albany" — which is what the demo
    scenarios do, so the message is searched too.

    A region we cannot map returns its name rather than None-meaning-no-filter.
    Silently ignoring it would answer a question about Whangarei with Auckland
    prices and give no sign the location was dropped, which is the same failure
    as widening a radius back to national.
    """
    location = state.get("location") or {}
    named = location.get("region")
    if named:
        scope = locations_for(named)
        return (scope, None) if scope else (None, named)

    # Only the message is left. An unrecognised place name here is NOT an
    # error: most messages mention no region at all, and treating every
    # unmatched word as a failed region would refuse ordinary requests.
    region = resolve_region(state.get("message", ""))
    return (region.store_locations if region else None, None)


def emit_unknown_region(state: GroceryState) -> dict:
    """
    The shopper named a place we cannot map to stores.

    Refused rather than ignored, and it names what we DO cover, so the reply is
    actionable instead of merely apologetic — the same shape as
    `emit_dietary_unsupported`.
    """
    return {
        "terminated": True,
        "events": [
            ErrorEvent(
                seq=_next_seq(state),
                code=ErrorCode.INVALID_REQUEST,
                retryable=False,
                message=(
                    f"I don't have stores mapped for {state.get('unknown_region')}. "
                    f"I can look in: {_join(known_regions())}."
                ),
            )
        ],
    }


def current_freshness(as_of: date | None = None) -> FreshnessFilter:
    """
    The staleness rule, from config, against an injectable reference date.

    `as_of` is a parameter rather than a call to `date.today()` inside because
    the committed fixtures carry a fixed capture date. Under a wall clock they
    drift into staleness as calendar time passes and every demo turns red on a
    day nobody chose; a suite whose result depends on when you run it is not a
    suite.
    """
    return FreshnessFilter(as_of=as_of or reference_date(), max_age_days=max_price_age_days())


def emit_stale_data(state: GroceryState) -> dict:
    """
    Everything we hold for this request is too old to stand behind.

    Not a `no_data`: we HAVE prices, and saying otherwise would be false. Not a
    silent answer either. The product's claim is not "here is a price" but
    "here is the CHEAPEST price", and a comparison drawn from stale rows can be
    wrong in a way a stale price alone is not, because the winner changes when a
    special rotates. ACQUISITION-RISK.md finds the binding constraint is the
    Fair Trading Act and that it attaches to the comparison published rather
    than to the fetch, which is what makes this a refusal rather than a
    disclaimer.

    The capture date is named so the answer is checkable rather than merely
    apologetic.
    """
    stale = state.get("stale_only") or {}
    newest = max(stale.values(), default="")
    items = _join(sorted(stale))
    return {
        "terminated": True,
        "events": [
            ErrorEvent(
                seq=_next_seq(state),
                code=ErrorCode.STALE_DATA,
                # The data may well refresh; this is worth trying again later,
                # unlike a budget that genuinely does not stretch.
                retryable=True,
                message=(
                    f"My prices for {items} were last checked on {newest}, which is "
                    f"too long ago to compare them fairly — specials change weekly. "
                    f"I'd rather say so than quote you a price that has moved."
                ),
            )
        ],
    }


def retrieve_prices(state: GroceryState, repo: PriceRepository) -> dict:
    """
    The grounding node. The ONLY place Citations are created.

    For a price check this resolves EVERY item the user asked about. Citation
    refs are numbered globally across all items rather than restarting per
    item, so a ref identifies exactly one price everywhere it appears.

    Items that do not resolve are recorded rather than dropped. A user who
    asks about three things and is answered about two has been quietly
    misled; partial results with an explicit gap are the honest outcome. The
    same applies to items past MAX_ITEMS_PER_TURN, which are named rather than
    discarded — for the user there is no difference between a question that
    was answered wrongly and one that was never acknowledged.
    """
    constraints = state.get("constraints", {})
    intent = state.get("intent")
    household_size = constraints.get("household_size", 1)
    days_covered = constraints.get("days", 1)
    infeasible_upfront = False

    # Req 1.5/1.6 and 8.4. Both filters are handed to the REPOSITORY rather
    # than applied to what it returns: filtering afterwards would drop an
    # in-radius, in-date price behind five that are neither, and the graph reads
    # an empty list as `no_data`.
    #
    # No location means national results (Req 1.6), never a refusal. A location
    # narrows and must never silently widen back.
    near = _near_filter(state)
    locations, unknown_region = _location_scope(state)
    if unknown_region:
        # Nothing is retrieved for a place we cannot map. Returning national
        # prices instead would answer a question the shopper did not ask.
        return {"unknown_region": unknown_region, "citations": [], "records": [], "events": []}
    freshness = current_freshness()

    records: list = []
    item_groups: dict[str, list[str]] = {}
    unresolved: list[str] = []
    # Items that HAVE prices, all of them too old to stand behind. Kept apart
    # from `unresolved` because "I have nothing for that" and "everything I have
    # is six weeks old" are different facts and only one of them is about the
    # product.
    stale_only: dict[str, str] = {}
    skipped: list[str] = []
    citations: list[Citation] = []
    events: list = []
    seq = _next_seq(state)
    ref_n = 0
    recipe_shortlist: list[str] = []
    recipe_refs: dict[str, dict[str, str]] = {}
    recipe_meals_wanted = 0
    # `id(record) -> ref`, so the recipe path can reuse a citation the candidate
    # sweep already produced instead of citing the same price twice. Identity,
    # not equality: `PriceRecord` is frozen and two stores can hold
    # byte-identical rows, and the ref belongs to the record retrieval cited.
    ref_by_record: dict[int, str] = {}

    def add(rec) -> str:
        nonlocal ref_n
        ref_n += 1
        ref = f"c{ref_n}"
        citation = Citation(
            ref=ref,
            store=rec.store,
            store_location=rec.store_location,
            product_name=rec.display_name,
            price_nzd=rec.price_nzd,
            unit=rec.unit,
            unit_price_nzd=rec.unit_price_nzd,
            on_special=rec.on_special,
            valid_date=rec.valid_date,
            source=SourceRef(
                table=repo.table_name,
                pk=rec.store_key,
                sk=rec.product_key,
            ),
        )
        citations.append(citation)
        records.append(rec)
        ref_by_record[id(rec)] = ref
        events.append(CitationEvent(seq=seq + len(events), citation=citation))
        return ref

    if intent == Intent.PRICE_CHECK:
        stores = constraints.get("preferred_stores") or None
        requested = constraints.get("query_items", [])
        skipped = list(requested[MAX_ITEMS_PER_TURN:])
        for term in requested[:MAX_ITEMS_PER_TURN]:
            key = repo.resolve_product_key(term)
            found = (
                repo.cheapest_for_product(
                    key,
                    limit=5,
                    stores=stores,
                    near=near,
                    locations=locations,
                    freshness=freshness,
                )
                if key
                else []
            )
            if key is not None and not found:
                # Nothing fresh. Ask again without the freshness filter, ONLY
                # to tell "we hold nothing for this" apart from "everything we
                # hold is out of date". A second query costs one round trip on
                # a path that is already returning nothing, and it buys the
                # difference between an honest refusal and a misleading one.
                aged = repo.cheapest_for_product(
                    key, limit=1, stores=stores, near=near, locations=locations
                )
                if aged:
                    stale_only[term] = aged[0].valid_date
                    continue
            if key is None or not found:
                unresolved.append(term)
                continue
            if key in item_groups:
                continue  # the user named the same product twice
            item_groups[key] = [add(rec) for rec in found]
    else:
        # `map_exclusions` returns (categories, unsupported). classify_intent
        # already recorded the unsupported terms and the router refuses the
        # turn before it reaches here, so retrieval trusts that the mapping
        # exists — anything unmappable that got this far is a routing bug and
        # must fail rather than silently produce an unsafe plan.
        exclude_categories, unsupported = map_exclusions(constraints.get("dietary_exclusions", []))
        if unsupported:
            raise RuntimeError(
                f"routing bug: retrieve_prices reached with unsupported "
                f"exclusions {unsupported!r}. classify_intent should have "
                f"routed to emit_dietary_unsupported."
            )
        # The budget goes to retrieval, not to the model. The model never
        # sees a price, so it cannot keep itself inside a budget; what it CAN
        # be given is a candidate set where every possible selection is
        # affordable. Without this the plan node could only discover the
        # overspend afterwards, and its one repair lever -- smaller portions --
        # does not reduce what a pack costs.
        budget = constraints.get("budget_nzd")
        candidates = repo.candidates_for_budget(
            categories=MEAL_CATEGORIES,
            exclude_categories=exclude_categories,
            limit_per_category=3,
            budget_nzd=budget,
            near=near,
            locations=locations,
            freshness=freshness,
        )
        if not candidates:
            # Same question as the price-check path: is the catalogue empty for
            # this request, or merely out of date? A plan costed from stale
            # prices is a shopping list whose total is fiction.
            aged = repo.candidates_for_budget(
                categories=MEAL_CATEGORIES,
                exclude_categories=exclude_categories,
                limit_per_category=1,
                budget_nzd=budget,
                near=near,
                locations=locations,
            )
            if aged:
                stale_only["your meal plan"] = max(r.valid_date for r in aged)

        # Refuse before generating when the budget cannot cover the request at
        # any price. Checked here rather than after costing a draft because
        # the candidate set is now capped to the budget, so a draft built from
        # it always "fits" -- affordability alone can no longer tell us the
        # request was possible.
        if budget is not None:
            floor = minimum_spend(candidates, household_size, days_covered)
            if floor is not None and budget < floor:
                infeasible_upfront = True

        for rec in candidates:
            add(rec)

        # ---- recipes (Req 2.9, Pilot Task 15c) -------------------------
        #
        # THE RECIPE PATH RETRIEVES THROUGH THIS NODE, LIKE EVERYTHING ELSE.
        # A `select_recipes` node placed BEFORE retrieval would have been the
        # smaller change and it would have put a model call upstream of the only
        # thing allowed to produce a Citation. That topology -- "generate_* is
        # unreachable except through retrieve_prices; no edge skips it" -- is one
        # of the three independent enforcements of Invariant 1, and it is worth
        # more than the convenience.
        #
        # So the ingredient terms of the curated catalogue are resolved HERE,
        # cited HERE, and `select_recipes` is offered only recipes already proven
        # costable, dietary-viable against the resolved products, and affordable
        # at this household's share of the budget. The model chooses among
        # options that are all already correct.
        #
        # `shortlist` is empty on a turn where nothing survives. That is a
        # FALLBACK, not an error -- see `select_recipes`.
        try:
            resolved_ingredients = resolve_ingredients(
                repo,
                curated_recipes(),
                near=near,
                locations=locations,
                freshness=freshness,
            )
        except TooManyIngredients:
            # The catalogue outgrew the per-turn bound. Refusing the recipe path
            # and falling back to free composition is the honest outcome; a
            # truncated ingredient set would silently offer recipes whose
            # costability depended on iteration order.
            resolved_ingredients = {}

        # Cite the recipe ingredients that are not already in the candidate set.
        # `add()` numbers refs globally, so a ref identifies exactly one price
        # everywhere it appears -- including across the two retrieval modes.
        for record in resolved_ingredients.values():
            if id(record) not in ref_by_record:
                add(record)

        wanted = meals_needed(curated_recipes(), household_size=household_size, days=days_covered)
        citation_map = {c.ref: c for c in citations}
        record_map = dict(zip([c.ref for c in citations], records, strict=True))
        offers = shortlist(
            curated_recipes(),
            resolved_ingredients,
            ref_by_record,
            citation_map,
            household_size=household_size,
            days=days_covered,
            exclude_categories=exclude_categories,
        )
        # Trim the offer to a set that fits the budget TOGETHER, so any
        # selection the model makes is affordable by construction. Same reason
        # `candidates_for_budget` caps its candidate set: a price-blind model
        # cannot keep itself inside a budget, and the set it chooses from can.
        offers = affordable_set(
            offers,
            citation_map,
            record_map,
            household_size=household_size,
            days=days_covered,
            budget_nzd=budget,
        )
        recipe_shortlist = [o.recipe.recipe_id for o in offers]
        recipe_refs = {o.recipe.recipe_id: o.refs for o in offers}
        recipe_meals_wanted = wanted

    # Honest gaps for items we could not answer, alongside the ones we could.
    # Only when SOME items resolved; if none did, routing sends the turn to
    # the terminal no_data path instead.
    if item_groups and unresolved:
        for term in unresolved:
            events.append(
                NoDataEvent(
                    seq=seq + len(events),
                    requested_item=term[:80],
                    message=f"I don't have price data for {term}.",
                )
            )

    # Items we never looked at, because the request exceeded the cap. A notice
    # rather than a no_data event: we are not claiming there is no price for
    # these, only that we did not check. Emitted even when nothing resolved,
    # since "I checked five of your seven items and found nothing" and "I found
    # nothing" are different statements and only one of them is true.
    if skipped:
        events.append(
            NoticeEvent(
                seq=seq + len(events),
                message=(
                    f"I can look up {MAX_ITEMS_PER_TURN} items at a time, so I "
                    f"didn't check {_join(skipped)}. Ask me again for those."
                ),
            )
        )

    return {
        "stale_only": stale_only,
        "records": records,
        "citations": citations,
        "citation_index": {c.ref: c for c in citations},
        # strict=True: `add()` appends to both lists in lockstep, so a length
        # mismatch is a bug in this node. Under strict=False it truncated
        # silently, and a short index now surfaces downstream as "this
        # citation was not retrieved" -- a grounding violation pointing at
        # the wrong culprit. Fail where the mistake is.
        "record_index": dict(zip([c.ref for c in citations], records, strict=True)),
        "item_groups": item_groups,
        "unresolved_items": unresolved,
        "skipped_items": skipped,
        "budget_impossible": infeasible_upfront,
        "recipe_shortlist": recipe_shortlist,
        "recipe_refs": recipe_refs,
        "recipe_meals_wanted": recipe_meals_wanted,
        "events": events,
    }


def emit_no_data(state: GroceryState) -> dict:
    """The 'I don't have data for that' path. A SUCCESS outcome, not an error."""
    items = state.get("unresolved_items") or state.get("constraints", {}).get("query_items", [])
    if not items:
        items = ["that item"]

    seq = _next_seq(state)
    return {
        "terminated": True,
        "events": [
            NoDataEvent(
                seq=seq + i,
                requested_item=item[:80],
                message=(
                    f"I don't have price data for {item} at any of the stores "
                    f"near you. I can check something else if you like."
                ),
            )
            for i, item in enumerate(items)
        ],
    }
