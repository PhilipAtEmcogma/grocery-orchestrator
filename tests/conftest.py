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
