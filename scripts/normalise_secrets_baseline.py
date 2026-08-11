"""
Canonicalise .secrets.baseline so it does not churn by platform (Task 9.1).

THE PROBLEM. `detect-secrets-hook` rewrites the baseline in place whenever a
recorded secret's line number moves — which any edit above it does. It writes
paths using the local `os.sep`, so the same commit produces

    "tests/test_handler.py"     from Linux
    "tests\\test_handler.py"    from Windows

WHAT THIS IS NOT. It is not a broken gate. detect-secrets puts both sides
through `convert_local_os_path()` on load — `load_from_baseline()` for the
results keys, `load_secret_from_dict()` for each entry's own filename — so
separators are rewritten to the local `os.sep` before anything is compared,
and `should_update_baseline()` only ever sees converted values. A
Windows-written baseline is read correctly on Linux and vice versa. Neither
platform's scan fails because of the other's separators.

What it costs is churn. The file flips between the two forms as commits come
from different machines: a diff on a security artefact that means nothing,
review attention spent confirming it means nothing, and needless conflicts
when two branches touch it from two platforms. A file nobody can skim is a
file whose real changes stop being noticed — which matters more here than for
most, because the whole point of the baseline is that a human audited every
entry in it.

THE CANONICAL FORM is forward slashes everywhere, in the `results` keys and in
each entry's own `filename`. It is what Linux produces natively, and it is
verified to be accepted on Windows by both invocation forms (the hook's staged
-file form and CI's `git ls-files -z | xargs -0`) without triggering a rewrite.
Entries are sorted so that two runs cannot disagree about order either.

Formatting matches what detect-secrets itself writes — `indent=2` and a
trailing newline — so normalising a file that is already canonical produces
zero bytes of diff rather than trading one kind of churn for another.

Exit codes, which the hook branches on:

    0   already canonical, nothing written
    1   normalised and rewrote the file — the caller must `git add` it
    2   the baseline is missing or is not readable JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASELINE = Path(".secrets.baseline")


def canonicalise(data: dict) -> dict:
    """Forward-slash every recorded path, and order the results stably."""
    results = data.get("results", {})

    normalised: dict[str, list[dict]] = {}
    for filename, secrets in results.items():
        key = filename.replace("\\", "/")
        entries = [
            {
                **secret,
                # The entry carries its own copy of the path, and it is the one
                # detect-secrets compares when deciding whether the baseline
                # changed. Normalising only the key would leave the file
                # rewriting itself on every run.
                "filename": str(secret.get("filename", key)).replace("\\", "/"),
            }
            for secret in secrets
        ]
        # Two entries in one file are ordered by where they are, then by hash
        # so that identical line numbers still sort deterministically.
        entries.sort(key=lambda s: (s.get("line_number", 0), s.get("hashed_secret", "")))
        normalised[key] = entries

    return {**data, "results": dict(sorted(normalised.items()))}


def render(data: dict) -> str:
    """The exact serialisation detect-secrets uses, so no-op runs are no-ops."""
    return json.dumps(data, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the baseline is canonical without writing to it",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE,
        help=f"path to the baseline (default: {BASELINE})",
    )
    args = parser.parse_args()

    try:
        original = args.baseline.read_text(encoding="utf-8")
        data = json.loads(original)
    except FileNotFoundError:
        print(f"{args.baseline} not found", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{args.baseline} is not readable JSON: {exc}", file=sys.stderr)
        return 2

    canonical = render(canonicalise(data))
    if canonical == original:
        return 0

    if args.check:
        print(f"{args.baseline} is not canonical (run without --check to fix)")
        return 1

    args.baseline.write_text(canonical, encoding="utf-8", newline="\n")
    # ASCII only: this is printed by a shell hook into whatever console the
    # author has, and a Windows terminal in cp1252 turns an em-dash into a
    # replacement character in the middle of an instruction.
    print(f"{args.baseline} normalised - 'git add' it and commit again")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
