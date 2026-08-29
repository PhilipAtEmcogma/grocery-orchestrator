"""
Shared test configuration.

The one thing here is the freshness reference date, and it is here rather than
in each test because it applies to every turn the suite runs.

`fixtures/products.json` carries a FIXED capture date. Freshness is measured
against the wall clock in production, which is correct there and wrong here: the
fixtures would drift into staleness as calendar time passes, and one day every
planning and price-check test in the suite would start returning STALE_DATA for
a reason that has nothing to do with the code. A test suite whose result depends
on the day you run it is not a test suite.

So the suite pins "now" to a date at which the committed fixtures are fresh.
Tests that care about staleness set their own filter explicitly rather than
relying on this.
"""

from __future__ import annotations

import pytest

from src.retrieval.filters import AS_OF_ENV

# Shortly after the fixtures' 2026-07-31 capture, so they are comfortably inside
# the 14-day window without sitting on its edge.
FIXTURE_AS_OF = "2026-08-05"


@pytest.fixture(autouse=True)
def _pin_freshness_reference(monkeypatch):
    monkeypatch.setenv(AS_OF_ENV, FIXTURE_AS_OF)
