"""
The two refusals that keep the fixture catalogue out of a real table.

ONE HAZARD, TWO LAYERS. `fixtures/products.json` is 152 hand-written rows --
one product per key, invented prices, a 2026-07-31 capture date. It is the
right catalogue for a laptop, for the offline tests, and for a first load into
an empty table. It is never a catalogue a shopper should be served, and writing
it into a table that already holds the data team's real rows does not merely
add noise: the fixture keys SHADOW the real ones. `milk-2l` resolves before
`standard-milk-2l`, so `cheapest milk near Albany` answers with a fabricated
Devonport price while the real Albany row sits unread behind it.

THE ROWS WERE REMOVED THREE TIMES AND CAME BACK EVERY NIGHT IN BETWEEN.

    2026-08-30  removed from the live table       ARCHITECTURE.md 3j
    2026-09-01  found back; removed again         3t, OPEN-REVIEW-near-filter-drift.md
    2026-09-03  found back; vector finally found; schedule DISABLED, rows removed

PR #64 guarded `scripts/load_seed_data.py`, because a stray run of the seed
loader was the vector a human could see. It was not the vector. The account
check on 2026-09-03 found the SCHEDULED INGESTION LAMBDA rewriting all 152
fixture rows into `grocery-products-dev` every night at 03:18: it resolved a
`FixtureSource` from `default_source` in `config/data-sources.json`, which is
the correct default for a laptop and the wrong one for a deployment. Every
removal was undone the following night by the thing whose job is to keep the
table current.

IT WAS SILENT BECAUSE IT HAD REACHED STEADY STATE. `added 0, changed 0,
unchanged 152` is exactly what the diff should report when yesterday's
re-injection is still sitting there, so the one control that could have seen it
correctly reported nothing to see. The freshness stopgap hid the other half:
`max_price_age_days` at 45 keeps a 2026-07-31 row fresh until 2026-09-14, where
the original 14 would have turned every fixture answer into `STALE_DATA` on
2026-08-14 and made the shadowing loud.

SO THERE ARE TWO REFUSALS HERE, AT TWO DIFFERENT LAYERS, AND NEITHER SUBSUMES
THE OTHER.

  1. `deployment_signal` backs a refusal in `ingestion.sources.resolve_source`:
     a DEPLOYMENT may not arrive at the fixtures by DEFAULT. This is the one
     that stops the nightly re-injection, and it acts before any table is
     touched -- including on a table that is empty today and real tomorrow.

  2. `real_catalogue_present` backs a refusal in `ingestion.handler.refresh`
     and in `scripts.load_seed_data.load`: nothing WRITES the fixtures over a
     table that already holds the real catalogue, however the fixtures came to
     be selected -- an explicit `PRICE_SOURCE=fixtures`, a laptop run against
     the live table, a future caller that has not been written yet.

Layer 1 is about a default nobody chose. Layer 2 is about a write, whoever
chose it. A change that only had layer 1 would still let an operator shadow the
catalogue by hand; a change that only had layer 2 would leave a deployed Lambda
whose default behaviour is "serve fixtures" and whose refusal depends on the
real rows already being there.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from boto3.dynamodb.conditions import Key


class FixtureGuardError(RuntimeError):
    """
    The fixture catalogue was about to reach a table that is not a fixture table.

    ONE CLASS FOR BOTH REFUSALS, deliberately. They are raised at different
    layers, but they are the same fact -- invented prices about to be treated as
    collected ones -- and a caller that wanted to handle one and not the other
    would be a caller working around this module. `except FixtureGuardError`
    should mean "I am recovering the fixture catalogue on purpose", and there is
    no such caller in this repository.
    """


#: Store keys that exist ONLY in the data team's real Lineage B catalogue and
#: never in the fixtures. Their presence is a cheap, reliable signal that the
#: real catalogue is loaded -- one Query per probe rather than a Scan, which
#: neither the orchestrator nor the ingestion role is granted.
#:
#: `tests/test_ingestion.py` asserts these stay disjoint from the fixtures AND
#: present in Lineage B, so a catalogue change that invalidated a probe fails
#: the build rather than silently disarming both guards. That test is the
#: reason this tuple is shared rather than copied: it can only assert about one
#: definition.
REAL_ONLY_STORE_KEYS: tuple[str, ...] = ("paknsave#albany", "new_world#albany")


def real_catalogue_present(table: Any) -> str | None:
    """
    A real-only store key present in this table, or None.

    Takes a DynamoDB Table RESOURCE rather than a name, so a caller that
    already has one does not open a second, and -- the part that matters
    offline -- so the probe runs against whatever table the caller is actually
    writing to. `refresh()` builds the table once and hands the same object to
    the probe, the diff and the write; a probe that built its own would be
    asking a different table whether the write it is guarding is safe.

    Cheap by design: `Select="COUNT"` with `Limit=1` per probe key, stopping at
    the first hit. Returns the key it found so the refusal can name it.
    """
    for store_key in REAL_ONLY_STORE_KEYS:
        response = table.query(
            KeyConditionExpression=Key("store_key").eq(store_key),
            Select="COUNT",
            Limit=1,
        )
        if response.get("Count", 0) > 0:
            return store_key
    return None


#: Environment variables that mean "this process is a deployment, not a laptop".
#:
#: `AWS_LAMBDA_FUNCTION_NAME` is set by the Lambda runtime itself. It cannot be
#: forgotten by a deploy step, cannot drift, and is present on the very first
#: invocation of a function nobody has finished configuring -- which is exactly
#: when the fixture default does its damage.
#:
#: `APP_STAGE` is this project's own stage name, and it is listed SECOND and
#: never alone on purpose. It is currently UNSET on the deployed function
#: (`Philip_demo/17`: setting it is the last step of the production cutover), so
#: a check keyed on it would read as armed today and do nothing -- the shape of
#: control this repository keeps finding. It is here so that a non-Lambda
#: deployment of this code, a container or a Runtime, is covered the moment it
#: names its stage.
DEPLOYMENT_ENV_VARS: tuple[str, ...] = ("AWS_LAMBDA_FUNCTION_NAME", "APP_STAGE")


def deployment_signal(env: Mapping[str, str] | None = None) -> str | None:
    """
    The variable saying this process is a deployment, as `NAME=value`, or None.

    Returns the signal rather than a bool so a refusal can name the evidence it
    acted on. "This is a deployment" is not a useful thing to read in a log at
    3am; naming the variable and the function it held tells the reader which
    function refused and lets them check the same variable themselves.

    An empty value is not a signal. `APP_STAGE=""` is what an unset variable
    looks like to a deploy tool that always passes the flag, and treating it as
    a deployment would refuse laptop runs for a variable nobody set.
    """
    config: Mapping[str, str] = os.environ if env is None else env
    for name in DEPLOYMENT_ENV_VARS:
        value = config.get(name, "").strip()
        if value:
            return f"{name}={value}"
    return None
