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

from ingestion.normalise import to_item
from ingestion.sources import KNOWN_RETAILERS, resolve_source
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
    """
    source = resolve_source(retailer)
    offers = source.fetch()
    fetched = [to_item(offer) for offer in offers]

    # VALIDATE BEFORE DIFFING, not after. The diff answers "what would change",
    # and a rejected row must not appear in that answer as an `unchanged` or a
    # `changed` — it is not being written at all, so counting it either way
    # would describe a table state that will not exist.
    items, rejected = reject_implausible(fetched)
    for item in rejected:
        _log_rejection(retailer, item)

    table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)  # type: ignore[union-attr]
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
        history_table = boto3.resource("dynamodb", region_name=REGION).Table(HISTORY_TABLE)  # type: ignore[union-attr]
        with history_table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=to_history_item(item))
        history_written = len(items)

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


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    event = event or {}
    retailer = event.get("retailer")
    if retailer not in KNOWN_RETAILERS:
        # Raise rather than return an error shape: an unknown retailer is a
        # state-machine definition bug, and Step Functions should surface it as
        # a failed execution rather than a successful one with a sad payload.
        raise ValueError(f"retailer must be one of {KNOWN_RETAILERS}, got {retailer!r}")
    return refresh(retailer, dry_run=bool(event.get("dry_run")))
