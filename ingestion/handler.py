"""
Price ingestion Lambda.

One invocation refreshes ONE retailer. Step Functions Inline Map fans the three
out, which is what tech.md and design.md 33 specify and what the reviewed
architecture diagram omitted: a single Lambda fanning out to three retailers
couples them into one failure domain, so one retailer being slow or broken
takes the whole refresh with it. Per-source invocations fail independently,
retry independently, and report per-source counts.

Event shape:  {"retailer": "paknsave"}                 -- refresh
              {"retailer": "paknsave", "dry_run": true} -- report, write nothing

EVERY RUN VALIDATES, THEN DIFFS, THEN WRITES. The first version of this module
did none of the three, and a defect in `unit_price()` wrote unit_price_nzd
"2490.00" against a $2.49 broccoli into the live products table -- a
shopper-facing figure, a thousand times over, across six rows, with no signal
that anything had changed. The tests were green; the write was simply believed.

The diff was the first answer to that and it is the WEAKER half: it makes a
change visible after the fact, and a defect on a first write is not a change.
`reject_implausible` is the other half, added 2026-08-31. It refuses the row
outright, using the rule `src/review/snapshot.py` has held since the day before
-- written with the $2,490 broccoli in its docstring, and called by nothing
until now. A rule nobody runs is a comment with a test suite.

The diff is not a safety interlock, and deliberately does not block on a
threshold: with live acquisition, a genuine special can move a real
proportion of a retailer's catalogue, so a percentage gate would either be too
loose to catch a defect or would refuse legitimate refreshes. What it does is
make the change VISIBLE -- the counts and a sample land in the Step Functions
execution history and CloudWatch, so "150 rows changed on a day nothing was on
special" is answerable after the fact instead of undiscoverable. Run with
`dry_run` first when the normaliser has changed.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from ingestion.guard import FixtureGuardError, real_catalogue_present
from ingestion.normalise import to_item
from ingestion.sources import KNOWN_RETAILERS, FixtureSource, resolve_source
from src.history import to_history_item
from src.review import implausible_unit_price_values

REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
TABLE = os.environ.get("PRODUCTS_TABLE", "grocery-products-dev")
# Append-only price history, written alongside the products write. A SEPARATE
# table with a different lifecycle and a different reader (ops/reviewer, never
# the shopper path) -- see src/history. Env-overridable for the same reason
# PRODUCTS_TABLE is.
HISTORY_TABLE = os.environ.get("PRICE_HISTORY_TABLE", "grocery-price-history-dev")

# Fields whose change is worth reporting. Excludes nothing meaningful today;
# named explicitly so adding a field to to_item() is a decision about whether
# a change in it should be visible, not a silent omission.
DIFFED_FIELDS = (
    "price_nzd",
    "unit_price_nzd",
    "gsi1_sk",
    "valid_date",
    "on_special",
    "display_name",
    "pack_grams",
)

# How many changed keys to name in the result. The execution history is not a
# place to dump a catalogue; a handful is enough to recognise a wrong shape.
SAMPLE_LIMIT = 5

#: The structured field a CloudWatch metric filter binds to. A JSON selector,
#: not a substring: `config/alarms.json` explains at length why matching the text
#: "implausible_unit_price" anywhere in a log line would page somebody at 3am for
#: an exception message that happened to quote it.
REJECT_LOG_MESSAGE = "ingestion_row_rejected"

#: The same mechanism for a failed price-history append. See `_append_history`.
HISTORY_FAILED_LOG_MESSAGE = "ingestion_history_write_failed"

#: The same mechanism for a refresh that THREW. See `lambda_handler`.
#:
#: THIS IS NOT REDUNDANT WITH THE ExecutionsFailed ALARM, because that alarm
#: cannot see this. `config/ingestion-state-machine.json` catches `States.ALL`
#: INSIDE the item processor and routes it to a Pass state, so one retailer
#: throwing leaves the other two intact and the EXECUTION SUCCEEDS -- which is
#: the right behaviour and the reason a state-machine-level failure metric
#: reports nothing when a branch dies. A per-branch failure is a Lambda fact,
#: so it is metered from the Lambda's own log.
REFRESH_FAILED_LOG_MESSAGE = "ingestion_refresh_failed"


def reject_implausible(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Split a refresh into rows that may be written and rows that may not.

    THE DEFECT CLASS THIS CATCHES HAS ALREADY REACHED THE LIVE TABLE ONCE.
    `unit_price_nzd` was written as "2490.00" against a $2.49 broccoli across six
    rows -- a shopper-facing figure, wrong by a factor of a thousand, with no
    signal that anything had changed. The diff made it *visible after the fact*;
    it did not stop it, because a diff reports change and a defect on a first
    write is not a change.

    `src/review/snapshot.py` has held exactly this rule since 2026-08-31, written
    with the $2,490 broccoli in its docstring, and nothing called it. The
    deterministic half of the reviewer was built and then not switched on, so
    the one anomaly we had already learned about was still undetected in
    production while an AgentCore Runtime was being proposed to find anomalies
    nobody had thought of.

    A REJECTED ROW IS DROPPED, NOT REPAIRED, AND NOT FATAL.

    - Dropped, because the alternative is publishing a price we already believe
      is wrong. `batch_writer` only overwrites what it writes, so the previous
      good row survives and ages into `STALE_DATA` -- an honest outcome the graph
      already has a path for. "I have nothing current for that" is recoverable;
      "$2,490 per kg" is not.
    - Not repaired, because a corrected value would be a price nobody retrieved.
      That is the same authority `src/review/findings.py` refuses when a finding
      proposes a replacement.
    - Not fatal, and deliberately WITHOUT a percentage threshold. The original
      defect hit six rows out of 2,759 -- 0.2% -- so any threshold loose enough
      to tolerate a real special would have slept through it, and any threshold
      tight enough to catch it would refuse legitimate refreshes. The count is
      the signal; alarming on `>= 1` is the caller's decision, and
      `config/alarms.json` makes it.
    """
    accepted: list[dict] = []
    rejected: list[dict] = []
    for item in items:
        if implausible_unit_price_values(
            price_nzd=item["price_nzd"],
            unit_price_nzd=item["unit_price_nzd"],
            pack_grams=int(item["pack_grams"]),
        ):
            rejected.append(item)
        else:
            accepted.append(item)
    return accepted, rejected


def _existing(table: Any, items: list[dict]) -> dict[tuple[str, str], dict]:
    """
    Current rows for the store keys this refresh touches.

    Queried per store_key rather than scanned: the base table partitions by
    store, so this is one query per store the retailer has, and it stays one
    query per store as the catalogue grows.
    """
    out: dict[tuple[str, str], dict] = {}
    for store_key in sorted({item["store_key"] for item in items}):
        response = table.query(KeyConditionExpression=Key("store_key").eq(store_key))
        rows = list(response.get("Items", []))
        while "LastEvaluatedKey" in response:
            response = table.query(
                KeyConditionExpression=Key("store_key").eq(store_key),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            rows.extend(response.get("Items", []))
        for row in rows:
            out[(row["store_key"], row["product_key"])] = row
    return out


def diff_items(existing: dict[tuple[str, str], dict], items: list[dict]) -> dict:
    """
    What this refresh would change. Pure -- no AWS, exhaustively testable.

    Values are compared as strings because that is how they are stored: money
    is a string at rest (a float cent is a wrong cent), and a Decimal read back
    from DynamoDB compares unequal to the str it was written from.
    """
    added: list[str] = []
    changed: list[dict] = []

    for item in items:
        key = (item["store_key"], item["product_key"])
        before = existing.get(key)
        if before is None:
            added.append(f"{key[0]}/{key[1]}")
            continue
        fields = [
            {
                "field": field,
                "from": str(before.get(field)),
                "to": str(item[field]),
            }
            for field in DIFFED_FIELDS
            if str(before.get(field)) != str(item[field])
        ]
        if fields:
            changed.append({"key": f"{key[0]}/{key[1]}", "fields": fields})

    return {
        "added": len(added),
        "changed": len(changed),
        "unchanged": len(items) - len(added) - len(changed),
        "sample_added": added[:SAMPLE_LIMIT],
        "sample_changed": changed[:SAMPLE_LIMIT],
    }


def _append_history(retailer: str, items: list[dict]) -> int:
    """
    Append one price-history row per accepted product. Returns rows written.

    A HISTORY FAILURE MUST NOT FAIL THE REFRESH, AND THE ORDERING IS WHY.
    The products write has already succeeded by the time this runs. An
    exception here therefore fails a Step Functions branch whose actual job --
    refreshing the prices a shopper reads -- was done, and reports a working
    refresh as a broken one. That is the same trade `src/handler.py` makes for
    the idempotency store: "an idempotency store failure must not fail the
    turn... throwing away the response because the bookkeeping write failed
    would turn a degraded cache into a failed request."

    History is bookkeeping in exactly that sense. It is ops-and-reviewer data
    (`src/history`), it is never read on the shopper path, it never becomes a
    Citation, and every row in it is reproducible by re-running ingestion over
    the same source. Losing a day of it costs the reviewer's baseline one
    sample; failing the refresh costs every shopper a day of stale prices.

    BUT IT IS NOT SWALLOWED. A degradation nobody can see is the failure this
    repository keeps finding -- a rule with no caller, a skip with no
    condition, an alarm on a metric nothing publishes. So the failure prints
    the structured line `config/alarms.json` derives
    `IngestionHistoryWriteFailed` from, and the returned `history_written` of 0
    beside a non-zero `written` is visible in the execution history.

    THIS GUARD IS NOT WHY THE 2026-09-02 DEFECT WAS SILENT, and it does not fix
    it. The table and the IAM grant do (`infra/lib/stateful-stack.ts`,
    `config/iam-ingestion-role.json`); the missing alarm is why nobody knew.
    What this adds is that the NEXT history failure -- a throttle, a transient
    error, a table someone renames -- degrades instead of failing a refresh
    that worked.
    """
    try:
        history_table = boto3.resource("dynamodb", region_name=REGION).Table(HISTORY_TABLE)  # type: ignore[union-attr]
        with history_table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=to_history_item(item))
        return len(items)
    except Exception as exc:
        # Broad by intention. Every failure mode here -- AccessDenied, a missing
        # table, a throttle, a serialization error -- has the same correct
        # response, which is to record it and let the refresh stand. Narrowing
        # it to the ones imagined today would let an unimagined one fail a
        # refresh whose products write already succeeded.
        #
        # The exception TYPE and message go to the log, not the shopper: there
        # is no shopper here at all (Req 11.5 is satisfied by construction --
        # an ingestion Lambda holds no message, location, session or dietary
        # data), but naming the table and the error class is what makes the
        # alarm actionable.
        print(
            json.dumps(
                {
                    "message": HISTORY_FAILED_LOG_MESSAGE,
                    "retailer": retailer,
                    "table": HISTORY_TABLE,
                    "error": type(exc).__name__,
                    "detail": str(exc)[:300],
                    "rows_not_written": len(items),
                }
            )
        )
        return 0


def _log_rejection(retailer: str, item: dict) -> None:
    """
    One structured line per refused row, which is what the metric is derived from.

    PRINTED AS JSON, NOT LOGGED THROUGH POWERTOOLS. The working agreement forbids
    importing `aws_lambda_powertools` outside `src/handler.py` and
    `src/observability/powertools.py`, because that import is what would end the
    no-AWS property CI depends on — and this module is exercised by
    `tests/test_ingestion.py` with no account. A metric filter over a JSON
    selector needs no library at either end; `config/alarms.json` already derives
    `HandlerEscaped` from a log line exactly this way.

    NOTHING HERE IS SHOPPER DATA (Req 11.5). A rejected row is a product, a
    store and three numbers we are refusing to publish. There is no message, no
    location, no session and no dietary information anywhere in an ingestion
    Lambda — which is the same reason the review snapshot can exist at all.
    """
    print(
        json.dumps(
            {
                "message": REJECT_LOG_MESSAGE,
                "reason": "implausible_unit_price",
                "retailer": retailer,
                "store_key": item["store_key"],
                "product_key": item["product_key"],
                "price_nzd": str(item["price_nzd"]),
                "unit_price_nzd": str(item["unit_price_nzd"]),
                "pack_grams": int(item["pack_grams"]),
            },
            separators=(",", ":"),
        )
    )


def refresh(retailer: str, table_name: str = TABLE, *, dry_run: bool = False) -> dict[str, Any]:
    """
    Fetch one retailer, report what would change, and write unless dry_run.

    `batch_writer` overwrites on key match, so a re-run is idempotent: the same
    fixture produces the same items, and the diff proves it rather than
    asserting it -- `unchanged` equal to `fetched` is what idempotent looks
    like from the outside.

    IT REFUSES TO WRITE THE FIXTURE CATALOGUE OVER THE REAL ONE. See
    `ingestion/guard.py` for why, at length; the short version is that the
    fixture keys SHADOW the real ones, so this is not a duplicate-row problem
    but a fabricated-price one, and it reached the live table on three separate
    occasions before anyone found the vector.

    THE REFUSAL APPLIES TO A DRY RUN TOO, which is the one part of this that
    looks wrong at first. A dry run reports what a real run would do. A real
    run refuses, so a dry run that instead reported a cheerful diff would be
    describing a table state that will never exist -- the same reason
    `reject_implausible` runs BEFORE the diff rather than after it. The refusal
    names the store key it found, which is the answer a dry run was being asked
    for anyway.

    THERE IS NO `force` HERE, and its absence is deliberate. The seed loader has
    one because a human at a terminal can have a reason and can be made to type
    it. This function is invoked by a schedule, and an escape hatch on a
    scheduled path is a way for the defect to come back with an audit trail
    saying it was intended. A first load into an empty table is unaffected: the
    guard fires on the real catalogue being PRESENT, and an empty table has no
    catalogue to shadow.
    """
    source = resolve_source(retailer)

    # Built before the fetch, because the probe and the write must be asking
    # the same table. Reading a fixture file we have already decided not to
    # write would be work done to reach the same refusal more slowly.
    table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)  # type: ignore[union-attr]

    if isinstance(source, FixtureSource):
        found = real_catalogue_present(table)
        if found is not None:
            raise FixtureGuardError(
                f"refusing to refresh {table_name} from the FIXTURE catalogue: it "
                f"already holds the real one (found rows at {found!r}, a store key "
                f"that exists only in Lineage B). fixtures/products.json is 152 "
                f"hand-written rows whose product keys SHADOW the real ones, so "
                f"this write would serve invented prices -- 'cheapest milk near "
                f"Albany' would answer with a fixture Devonport price instead of "
                f"the real Albany one. See docs/OPEN-REVIEW-near-filter-drift.md. "
                f"To refresh from the collected catalogue, set PRICE_SOURCE=lineage_b."
            )

    offers = source.fetch()
    fetched = [to_item(offer) for offer in offers]

    # VALIDATE BEFORE DIFFING, not after. The diff answers "what would change",
    # and a rejected row must not appear in that answer as an `unchanged` or a
    # `changed` — it is not being written at all, so counting it either way
    # would describe a table state that will not exist.
    items, rejected = reject_implausible(fetched)
    for item in rejected:
        _log_rejection(retailer, item)

    delta = diff_items(_existing(table, items), items)

    history_written = 0
    if not dry_run:
        with table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)

        # Append-only price history, in the SAME not-dry-run guard. One row per
        # accepted (store, product, capture date): a new date appends, a
        # same-day re-run overwrites an identical row. Ops/reviewer only -- this
        # is never read on the shopper path and never becomes a citation. A
        # dry_run writes nothing here either, for the same reason it writes
        # nothing to products: it is a report, not a mutation.
        history_written = _append_history(retailer, items)

    dates = sorted({item["valid_date"] for item in items})
    return {
        "retailer": retailer,
        "fetched": len(offers),
        "written": 0 if dry_run else len(items),
        # Appended to the price-history table alongside the products write.
        # Reported so a refresh's history contribution is visible in the Step
        # Functions execution rather than only discoverable by querying.
        "history_written": history_written,
        "dry_run": dry_run,
        # Reported so a stale refresh is visible in the execution history
        # rather than only discoverable by querying the table.
        "captured_at": dates[-1] if dates else None,
        "table": table_name,
        # Named `rejected` rather than folded into the diff counts, because a
        # row we refused to write and a row that did not change are different
        # facts and only one of them is about the retailer.
        "rejected": len(rejected),
        "sample_rejected": [
            f"{i['store_key']}/{i['product_key']} "
            f"price={i['price_nzd']} unit={i['unit_price_nzd']} pack_grams={i['pack_grams']}"
            for i in rejected[:SAMPLE_LIMIT]
        ],
        **delta,
    }


def _log_refresh_failure(retailer: Any, exc: BaseException) -> None:
    """
    One structured line per refresh that threw, which is what the metric reads.

    WITHOUT THIS, A FAILED BRANCH IS INVISIBLE TO EVERY ALARM.
    `config/ingestion-state-machine.json` catches `States.ALL` inside the item
    processor and routes it to a Pass state, so one retailer throwing does not
    abort the Map and the execution SUCCEEDS with that branch marked failed.
    That design is right -- it is why a broken Pak'nSave does not discard a good
    New World refresh -- but it means `AWS/States ExecutionsFailed` reports zero
    for exactly the failure it reads as covering. The only place a per-branch
    failure exists as an observable fact is this Lambda's own log.

    Printed as JSON, not through Powertools, for the same reason `_log_rejection`
    is: AGENTS.md forbids that import outside `src/handler.py` and
    `src/observability/powertools.py`, and this module is exercised by
    `tests/test_ingestion.py` with no account.

    NOTHING HERE IS SHOPPER DATA (Req 11.5). An ingestion Lambda holds no
    message, location, session or dietary information. `detail` is truncated
    because a boto3 error can carry a long request context and a log line is not
    a place to store one, not because anything in it is sensitive.
    """
    print(
        json.dumps(
            {
                "message": REFRESH_FAILED_LOG_MESSAGE,
                "retailer": str(retailer)[:64],
                "error": type(exc).__name__,
                "detail": str(exc)[:300],
            },
            separators=(",", ":"),
        )
    )


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    event = event or {}
    retailer = event.get("retailer")
    try:
        if retailer not in KNOWN_RETAILERS:
            # Raise rather than return an error shape: an unknown retailer is a
            # state-machine definition bug, and Step Functions should surface it
            # as a failed branch rather than a successful one with a sad payload.
            raise ValueError(f"retailer must be one of {KNOWN_RETAILERS}, got {retailer!r}")
        return refresh(retailer, dry_run=bool(event.get("dry_run")))
    except Exception as exc:
        # Broad, and it RE-RAISES. This is not error handling -- the failure is
        # still a failure, the branch still fails, and nothing is recovered. It
        # is the one point where a failure that Step Functions is about to
        # convert into a successful execution can still be written down.
        _log_refresh_failure(retailer, exc)
        raise
