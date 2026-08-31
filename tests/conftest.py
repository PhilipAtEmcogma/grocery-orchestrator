"""
Shared test configuration.

Two things, both applying to every turn the suite runs: the freshness reference
date, and a cold graph cache.

`fixtures/products.json` carries a FIXED capture date. Freshness is measured
against the wall clock in production, which is correct there and wrong here: the
fixtures would drift into staleness as calendar time passes, and one day every
planning and price-check test in the suite would start returning STALE_DATA for
a reason that has nothing to do with the code. A test suite whose result depends
on the day you run it is not a test suite.

So the suite pins "now" to a date at which the committed fixtures are fresh.
Tests that care about staleness set their own filter explicitly rather than
relying on this.

The graph cache is cleared for the opposite reason: so that no test result
depends on WHICH tests ran before it. `compiled_graph` resolves node functions
from `src.graph.nodes` at build time, so a test that monkeypatches a node gets
the patched behaviour only if the graph is compiled after the patch. Several
tests do exactly that -- `tests/test_grounding.py` tampers with
`retrieve_prices` to prove `run_turn` really calls the citation check -- and
they would silently stop testing anything if they were handed a graph an
earlier test had already compiled. Today they happen to be safe because each
constructs its own repository and model, which is a different cache key; that
is luck, and this fixture replaces it with a guarantee.
"""

from __future__ import annotations

import pytest

from src.graph.build import clear_graph_cache
from src.retrieval.filters import AS_OF_ENV

# Shortly after the fixtures' 2026-07-31 capture, so they are comfortably inside
# the 14-day window without sitting on its edge.
FIXTURE_AS_OF = "2026-08-05"


@pytest.fixture(autouse=True)
def _pin_freshness_reference(monkeypatch):
    monkeypatch.setenv(AS_OF_ENV, FIXTURE_AS_OF)


@pytest.fixture(autouse=True)
def _cold_graph_cache():
    # Both sides: nothing inherited on the way in, nothing left behind on the
    # way out. Clearing only on entry would still let the last test in a module
    # leave a graph built against a patched node module for the next one.
    clear_graph_cache()
    yield
    clear_graph_cache()


@pytest.fixture
def no_recipes(monkeypatch):
    """
    Run a meal-plan turn down the FREE-COMPOSITION path.

    Since Pilot Task 15c a meal_plan turn tries the curated catalogue first and
    only falls through to `generate_plan` when no recipe fits. That is the right
    default for the product and the wrong one for a test whose subject IS
    `generate_plan` -- the repair loop, the tier a plan call uses, the four
    failure terminals. Those tests would otherwise pass while exercising a node
    they never reach.

    Emptying the catalogue is how production reaches the same path (a diet that
    excludes everything, a budget nothing fits, a catalogue with no viable
    recipe), so this is the real fallback rather than a switch invented for
    tests. `select_recipes` emits its notice and routes to `generate_plan`,
    exactly as it would for a shopper.

    Named in each test's signature rather than made autouse: a fixture that
    silently disables a shipped feature for a whole suite is how a suite ends up
    testing a configuration nobody runs.

    PATCHED AT EVERY LOOKUP SITE, not at the definition. `from x import f` binds
    the function into the importing module's namespace, so patching
    `src.graph.recipe_plan.curated_recipes` alone changes nothing for a caller
    that already imported it. One of these three moved when the retrieval half
    was split out of `src/graph/nodes/__init__.py`, and monkeypatch's
    `AttributeError` is what caught it -- `setattr` on a missing name RAISES
    rather than silently creating one, which is the behaviour that turns a
    stale patch target into a failing test instead of a passing one that
    patches nothing.
    """
    monkeypatch.setattr("src.graph.recipe_plan.curated_recipes", lambda: [])
    monkeypatch.setattr("src.graph.nodes.retrieval.curated_recipes", lambda: [])
    monkeypatch.setattr("src.graph.nodes.recipes.curated_recipes", lambda: [])
