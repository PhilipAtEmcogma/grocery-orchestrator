"""
Location and freshness filters, applied INSIDE the repository.

Both filters live here rather than in the retrieval node, and that placement is
the whole point. `cheapest_for_product` returns the cheapest `limit` records; if
the caller filtered afterwards, a product whose five cheapest rows are all out
of radius or all stale would come back empty and the graph would report
`no_data` — "I don't have price data for butter" — about a product stocked
fresh at the shop down the road. That is exactly the truncation defect Pilot
Task 6 fixed for the store filter, and pushing these two down the same seam is
what stops it being reintroduced.

`PriceRepository` therefore takes them as parameters, and both implementations
apply them before the limit is imposed.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "freshness.json"

# Mean Earth radius. Haversine over a city is accurate to well under the
# precision a shopper cares about; nobody is choosing between two supermarkets
# on 50 metres.
_EARTH_RADIUS_KM = 6371.0088


# Pins the date freshness is measured against. Unset in production, where the
# wall clock is the right answer. Set for tests and for demos, which run against
# a CAPTURED SNAPSHOT: the committed fixtures carry a fixed capture date, so
# under a wall clock they drift into staleness as calendar time passes and every
# demo turns red on a day nobody chose. Same shape as USE_DYNAMODB / USE_BEDROCK
# -- an explicit, visible switch rather than a hidden default.
AS_OF_ENV = "FRESHNESS_AS_OF"


def reference_date() -> date:
    """Today, unless FRESHNESS_AS_OF pins it to a snapshot date."""
    override = os.environ.get(AS_OF_ENV)
    return date.fromisoformat(override) if override else date.today()


FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "products.json"


def fixture_snapshot_date() -> date:
    """
    The capture date of the committed fixture catalogue, read from the data.

    Derived rather than duplicated as a constant, so regenerating the fixtures
    with a newer capture cannot leave a stale date behind in code.
    """
    records = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    rows = records if isinstance(records, list) else records.get("records", [])
    return max(date.fromisoformat(r["valid_date"]) for r in rows)


def pin_to_fixture_snapshot() -> date:
    """
    Evaluate freshness as of the fixture capture, for anything running offline.

    The eval harnesses, the demos and the dev server all read the committed
    fixture catalogue, which is a SNAPSHOT with a fixed capture date. Judging it
    against the wall clock is not a stricter test, it is a wrong one: every
    price goes stale on a date nobody chose, and the meal-plan suite drops to
    18% for a reason that has nothing to do with the code under test.

    Called explicitly by each entry point rather than inferred from which
    repository is wired, because a rule this consequential should be visible at
    the call site. Production sets nothing and gets the wall clock.
    """
    import os

    as_of = fixture_snapshot_date()
    os.environ.setdefault(AS_OF_ENV, as_of.isoformat())
    return as_of


def max_price_age_days(config_path: Path | None = None) -> int:
    """The reviewable staleness threshold. See config/freshness.json."""
    raw = json.loads((config_path or CONFIG_PATH).read_text(encoding="utf-8"))
    return int(raw["max_price_age_days"])


@dataclass(frozen=True, slots=True)
class NearFilter:
    """
    Restrict results to stores within `radius_km` of a point.

    Req 1.5. Frozen because it is carried through the repository boundary and a
    filter that could be mutated in transit is a filter that can silently widen
    — and widening is the dangerous direction, since it returns the very stores
    the shopper ruled out.
    """

    lat: float
    lon: float
    radius_km: float

    def covers(self, lat: float, lon: float) -> bool:
        return haversine_km(self.lat, self.lon, lat, lon) <= self.radius_km


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass(frozen=True, slots=True)
class FreshnessFilter:
    """
    Exclude prices captured too long ago to stand behind.

    `as_of` is injected rather than read from the clock inside, so tests pin a
    date at which the committed fixtures are fresh. Fixtures carry a fixed
    capture date, so a wall-clock rule would quietly turn every demo red on a
    day nobody chose — a suite whose result depends on when you run it is not a
    suite.
    """

    as_of: date
    max_age_days: int

    def is_fresh(self, valid_date: str) -> bool:
        return self.age_days(valid_date) <= self.max_age_days

    def age_days(self, valid_date: str) -> int:
        return (self.as_of - date.fromisoformat(valid_date)).days
