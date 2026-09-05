"""
Placeholder resolution for config-as-data.

WHY: an AWS account id is not a credential, but hardcoding it into a public
repository is still the wrong default. It pins the file to one account --
contradicting the "reproducible in another account" claim every config header
in this repo makes -- and it hands a reader a concrete target for
enumeration, which is free reconnaissance for nothing in return.

So config files carry `${AWS_ACCOUNT_ID}` and `${AWS_REGION}`, and the applier
resolves them at apply time. The account comes from STS: whoever is
authenticated IS the account being deployed to, so it cannot drift from the
file the way a hardcoded value can. The region comes from the config's own
`region` field, which is already the declared deployment region.

Note what this does NOT do: the id is already in this repository's git
history, and history is not rewritable on a public repo with forks in any
meaningful sense. This is hygiene going forward, not redaction. Treat the
existing value as public, because it is.

Foundation-model ARNs (`arn:aws:bedrock:*::foundation-model/...`) carry no
account and no region by design -- they are AWS-owned -- so they contain no
placeholders and need none.
"""

from __future__ import annotations

import re
from typing import Any

ACCOUNT_PLACEHOLDER = "${AWS_ACCOUNT_ID}"
REGION_PLACEHOLDER = "${AWS_REGION}"

# A 12-digit run is an AWS account id. Used by the guard test to fail the build
# if one is reintroduced into a config file.
ACCOUNT_ID_PATTERN = re.compile(r"\b\d{12}\b")


def current_account_id() -> str:
    """The account the caller is authenticated to, per STS."""
    import boto3

    return boto3.client("sts").get_caller_identity()["Account"]


def substitute(obj: Any, *, account_id: str, region: str) -> Any:
    """
    Replace placeholders throughout a parsed config.

    Walks keys as well as values: a placeholder can legitimately appear in a
    map key (an ARN used as a lookup), and substituting only values would
    leave those silently unresolved -- which IAM would then reject with an
    error naming the literal `${AWS_ACCOUNT_ID}`, at apply time, which is late
    but at least loud.
    """
    if isinstance(obj, dict):
        return {
            substitute(k, account_id=account_id, region=region): substitute(
                v, account_id=account_id, region=region
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [substitute(i, account_id=account_id, region=region) for i in obj]
    if isinstance(obj, str):
        return obj.replace(ACCOUNT_PLACEHOLDER, account_id).replace(REGION_PLACEHOLDER, region)
    return obj


def assert_resolved(obj: Any, where: str) -> None:
    """
    Fail before an apply if any placeholder survived substitution.

    A leftover `${AWS_ACCOUNT_ID}` in an ARN is accepted by some APIs as a
    literal string and rejected by others, so the failure mode varies by
    service. Checking here makes it uniform and immediate.
    """
    rendered = repr(obj)
    for placeholder in (ACCOUNT_PLACEHOLDER, REGION_PLACEHOLDER):
        if placeholder in rendered:
            raise SystemExit(
                f"{where}: {placeholder} was not resolved. "
                "Every placeholder must be substituted before apply."
            )
