"""
Config files must not hardcode an AWS account id.

This repository is public. An account id is not a credential, but hardcoding
one pins a config to a single account -- contradicting the "reproducible in
another account" claim every config header makes -- and hands a reader a
concrete target for enumeration in exchange for nothing.

The rule is enforced here rather than in review because it is exactly the kind
of thing that returns: the next person to add a resource writes the ARN they
just copied out of the console, and it reads as correct because it is correct,
for one account.

Note what this does NOT undo: ids already committed are in git history, and
history is not meaningfully rewritable on a public repo with forks. This keeps
the working tree clean going forward; it is not redaction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from aws_placeholders import (
    ACCOUNT_ID_PATTERN,
    assert_resolved,
    substitute,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
CONFIGS = sorted(CONFIG_DIR.glob("*.json"))


def test_there_are_config_files_to_check():
    """A glob that matches nothing passes every assertion below it."""
    assert CONFIGS, f"no config files found under {CONFIG_DIR}"


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.name)
def test_config_carries_no_literal_account_id(path: Path):
    text = path.read_text(encoding="utf-8")
    found = ACCOUNT_ID_PATTERN.findall(text)
    assert not found, (
        f"{path.name} hardcodes what looks like an AWS account id ({found[0]}). "
        f"Use ${{AWS_ACCOUNT_ID}}; scripts/apply_*.py resolve it from STS."
    )


@pytest.mark.parametrize("path", CONFIGS, ids=lambda p: p.name)
def test_config_parses(path: Path):
    json.loads(path.read_text(encoding="utf-8"))


def test_substitution_reaches_nested_arns():
    cfg = {
        "region": "ap-southeast-2",
        "Statement": [{"Resource": ["arn:aws:dynamodb:${AWS_REGION}:${AWS_ACCOUNT_ID}:table/x"]}],
    }
    out = substitute(cfg, account_id="123456789012", region="ap-southeast-2")
    assert out["Statement"][0]["Resource"][0] == (
        "arn:aws:dynamodb:ap-southeast-2:123456789012:table/x"
    )


def test_substitution_reaches_dict_keys():
    """A placeholder can appear in a key; substituting only values would miss it."""
    out = substitute({"${AWS_ACCOUNT_ID}": "v"}, account_id="123456789012", region="r")
    assert out == {"123456789012": "v"}


def test_assert_resolved_rejects_a_surviving_placeholder():
    """
    Applying a half-substituted document is the failure worth catching early.

    Some AWS APIs accept `${AWS_ACCOUNT_ID}` as a literal string in an ARN and
    fail later at use; others reject it at apply. Checking here makes the
    failure uniform and immediate rather than service-dependent.
    """
    with pytest.raises(SystemExit, match="was not resolved"):
        assert_resolved({"arn": "arn:aws:s3:::${AWS_ACCOUNT_ID}-bucket"}, "test")


def test_assert_resolved_passes_a_fully_substituted_document():
    assert_resolved({"arn": "arn:aws:s3:::123456789012-bucket"}, "test")
