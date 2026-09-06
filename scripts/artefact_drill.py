"""
The artefact bucket's restore and deletion drill (Pilot Task 12).

WHY A DRILL AND NOT A UNIT TEST. `infra/test/observability-stack.test.ts`
asserts the bucket is versioned, encrypted, private, and carries a
noncurrent-version lifecycle rule per prefix. Every one of those is a claim
about the TEMPLATE. None of them proves that an artefact overwritten by
accident can actually be got back, which is the only reason versioning is on.
That difference is the same one the alarm drill exists for: `apply_alarms.py
--dry-run` proves an alarm is well-formed, and only firing it proves it pages
anybody.

WHAT IT DOES, in one prefix, on one object it created itself:

  1. PUT v1, read it back, confirm the bytes.
  2. PUT v2 over the same key -- the accident being simulated.
  3. Confirm the live read now returns v2, and that v1 still exists as a
     noncurrent version.
  4. RESTORE: copy v1 forward to become the current version. This is the
     recovery a person would actually perform, and it is deliberately not
     "delete v2" -- restoring by deleting is how a second mistake is made
     during recovery.
  5. DELETE the key and its versions, and confirm the listing is empty.

Everything it writes is under `drills/`, which is NOT one of the four
lifecycle-managed prefixes, and it removes what it wrote. A drill that leaves
artefacts behind teaches people to ignore the bucket's contents.

USAGE

    python scripts/artefact_drill.py --bucket <name>          # run it
    python scripts/artefact_drill.py --bucket <name> --keep   # leave the object

The bucket name is the `ArtefactBucket` output of `Grocery-Obs-dev`. There is
no default and no discovery: this script mutates a bucket, and guessing which
one is not a thing it should ever do.

NO CREDENTIALS, NO RUN. It calls S3, so it belongs in the same category as
`apply_alarms.py` without `--dry-run` -- an operator action recorded in the
architecture log, never something CI does.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

REGION = "ap-southeast-2"

#: Deliberately outside the four lifecycle-managed prefixes. A drill writing
#: into `evaluations/` would put a synthetic object among real measurements,
#: and the whole point of the prefixes is that what is in them is trustworthy.
DRILL_PREFIX = "drills/"


def _client():
    import boto3
    from botocore.config import Config

    return boto3.client("s3", region_name=REGION, config=Config(retries={"max_attempts": 3}))


def _put(s3, bucket: str, key: str, body: bytes) -> str:
    response = s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="text/plain")
    version = response.get("VersionId")
    if not version:
        raise SystemExit(
            "The PUT returned no VersionId, which means versioning is NOT enabled on "
            f"{bucket}. That is the finding -- the restore below cannot work, and "
            "every overwrite so far has been unrecoverable. Check the stack deployed."
        )
    return version


def _get(s3, bucket: str, key: str, version: str | None = None) -> bytes:
    kwargs = {"Bucket": bucket, "Key": key}
    if version:
        kwargs["VersionId"] = version
    return s3.get_object(**kwargs)["Body"].read()


def _versions(s3, bucket: str, key: str) -> list[dict]:
    paginator = s3.get_paginator("list_object_versions")
    found: list[dict] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=key):
        found.extend(page.get("Versions", []))
        found.extend(page.get("DeleteMarkers", []))
    return [v for v in found if v["Key"] == key]


def _step(number: int, text: str) -> None:
    print(f"\n[{number}] {text}")


def run(bucket: str, *, keep: bool) -> int:
    s3 = _client()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    key = f"{DRILL_PREFIX}restore-drill-{stamp}.txt"

    first = b"v1 - the artefact somebody would be sad to lose\n"
    second = b"v2 - the accidental overwrite\n"

    print(f"Artefact drill against s3://{bucket}/{key}")

    _step(1, "PUT the original and read it back")
    v1 = _put(s3, bucket, key, first)
    if _get(s3, bucket, key) != first:
        raise SystemExit("the object did not read back as written")
    print(f"    v1 = {v1}")

    _step(2, "PUT over it -- the accident")
    v2 = _put(s3, bucket, key, second)
    print(f"    v2 = {v2}")

    _step(3, "Confirm the live read is the overwrite and the original survives")
    live = _get(s3, bucket, key)
    if live != second:
        raise SystemExit("the live read did not return the overwrite; the drill is not valid")
    if _get(s3, bucket, key, version=v1) != first:
        raise SystemExit(
            "THE ORIGINAL IS GONE. Versioning is not protecting this bucket, which is "
            "exactly what this drill exists to find out before it matters."
        )
    held = len(_versions(s3, bucket, key))
    print(f"    live is v2; v1 still readable by version id ({held} versions held)")

    _step(4, "RESTORE by copying the old version forward")
    # Copy-forward, not delete-the-new-one. The restored object becomes a NEW
    # current version and the overwrite is preserved as history -- so a
    # recovery performed on the wrong key is itself recoverable. Deleting to
    # restore is how the second mistake gets made.
    s3.copy_object(
        Bucket=bucket,
        Key=key,
        CopySource={"Bucket": bucket, "Key": key, "VersionId": v1},
    )
    restored = _get(s3, bucket, key)
    if restored != first:
        raise SystemExit(f"restore failed: live object is {restored!r}, expected {first!r}")
    print("    live read now returns the original bytes")

    if keep:
        print(f"\n--keep: leaving s3://{bucket}/{key} in place. Remove it by hand.")
        return 0

    _step(5, "DELETE every version and confirm the key is gone")
    versions = _versions(s3, bucket, key)
    s3.delete_objects(
        Bucket=bucket,
        Delete={
            "Objects": [{"Key": key, "VersionId": v["VersionId"]} for v in versions],
            "Quiet": True,
        },
    )
    remaining = _versions(s3, bucket, key)
    if remaining:
        raise SystemExit(f"{len(remaining)} versions survived the delete: {remaining}")
    print(f"    {len(versions)} versions deleted, listing is empty")

    print("\nDRILL PASSED - overwrite survived, restore worked, deletion is complete.")
    print("Record the date and the bucket in docs/ARCHITECTURE.md, like the alarm drill.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket",
        required=True,
        help="The ArtefactBucket output of Grocery-Obs-dev. No default, deliberately.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Leave the drill object in place instead of deleting it.",
    )
    args = parser.parse_args()
    return run(args.bucket, keep=args.keep)


if __name__ == "__main__":
    raise SystemExit(main())
