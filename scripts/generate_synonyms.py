"""
Generate the product-name half of `config/product-synonyms.json`.

`resolve_product_key` is exact-match with no substring fallback, deliberately:
a loose match invents a price, and a wrong price is worse than no answer. That
design needs a lookup table, and 528 derived Lineage B keys are too many to
type by hand.

WHAT THIS GENERATES, AND WHAT IT REFUSES TO.

It generates the mechanical half: a product's own name, minus its size suffix,
pointing at its key. `"Brown Onions"` -> `brown-onions-kg`. That mapping is a
restatement of the catalogue, so it is safe to derive and safe to regenerate.

It does NOT generate HEAD TERMS -- the bare nouns a shopper actually types, like
"butter" or "milk". Those stay hand-curated in the config, because picking one
automatically is how you ship a wrong answer:

* `"butter"` matches ten products, one of which is `Salted Butter Frozen Dessert`
* `"cheese"` matches seventeen, one of which is `Chunky Cheese Sausages`
* `"milk"` matches twenty, spanning dairy and three plant milks
* `"chicken"` matches thirty-eight

A "pick the cheapest match" rule would answer "cheapest butter" with a frozen
dessert, and "cheapest cheese" with sausages. There is no automatic rule that
gets those right, so the script emits CANDIDATES for a human to choose from and
never writes the choice itself.

Ambiguous names are dropped rather than guessed. Where two products share a name
slug at different sizes, neither is emitted: returning one at random is the
confident-wrong-answer failure this whole design refuses.

Usage:
    python scripts/generate_synonyms.py                 # rewrite the generated block
    python scripts/generate_synonyms.py --candidates butter milk cheese
    python scripts/generate_synonyms.py --check         # CI: is the block current?
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingestion.lineage_b import (  # noqa: E402
    classify,
    derive_product_key,
    is_non_food,
)

DATASET = ROOT / "datasets" / "data" / "dynamodb_products"
CONFIG = ROOT / "config" / "product-synonyms.json"

# A generated entry must survive normalise_term() unchanged, or it can never be
# matched. Terms are stored already-normalised for that reason.
_NOISE_SAFE = re.compile(r"^[a-z0-9 ]+$")


def load_records() -> list[dict]:
    """Every Lineage B record, flattened out of its batch-write envelope."""
    records: list[dict] = []
    for path in sorted(DATASET.glob("*.json")):
        for entry in json.loads(path.read_text(encoding="utf-8"))["SmartGroceryProducts"]:
            item = entry.get("PutRequest", {}).get("Item", entry)
            records.append({k: (v.get("S") if "S" in v else v.get("N")) for k, v in item.items()})
    return records


def name_term(product_name: str) -> str:
    """
    The product's own name as a lookup term: lowercased, punctuation stripped.

    Single characters are dropped to match `normalise_term`, which drops them
    because splitting "what's" leaves a stray "s".
    """
    cleaned = re.sub(r"[^a-z0-9]+", " ", product_name.lower())
    return " ".join(w for w in cleaned.split() if len(w) > 1)


def generate(records: list[dict]) -> tuple[dict[str, str], dict[str, list[str]]]:
    """
    Return `(unambiguous name -> key, ambiguous name -> the keys it could mean)`.

    Ambiguity is reported rather than resolved. A name that means two products
    is a name this table must not answer for.
    """
    by_term: dict[str, set[str]] = collections.defaultdict(set)
    for record in records:
        name = record["product_name"]
        if is_non_food(name):
            continue
        term = name_term(name)
        if not term or not _NOISE_SAFE.match(term):
            continue
        by_term[term].add(derive_product_key(name, record.get("size", "")))

    unambiguous = {t: next(iter(k)) for t, k in sorted(by_term.items()) if len(k) == 1}
    ambiguous = {t: sorted(k) for t, k in sorted(by_term.items()) if len(k) > 1}
    return unambiguous, ambiguous


def candidates(records: list[dict], head_terms: list[str]) -> None:
    """
    Print what a head term could mean, for a human to choose from.

    Shows the resolved category too, because the trap here is a product whose
    name contains the term but whose category says it is something else --
    `Salted Butter Frozen Dessert` for "butter".
    """
    for term in head_terms:
        pattern = re.compile(rf"\b{re.escape(term.lower())}\b")
        seen: dict[str, tuple[str, str]] = {}
        for record in records:
            name = record["product_name"]
            if is_non_food(name) or not pattern.search(name.lower()):
                continue
            key = derive_product_key(name, record.get("size", ""))
            if key not in seen:
                category, _ = classify(name, record["category"])
                seen[key] = (name, category)
        print(f"\n=== {term!r}: {len(seen)} candidate keys")
        for key, (name, category) in sorted(seen.items(), key=lambda kv: kv[1][0]):
            print(f"  {category:8s}  {key:44s}  {name}")


def _render(config: dict) -> str:
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the block is stale")
    parser.add_argument("--candidates", nargs="+", metavar="TERM", help="list what a term matches")
    args = parser.parse_args()

    if not DATASET.exists():
        print(f"dataset not found at {DATASET}", file=sys.stderr)
        return 1
    records = load_records()

    if args.candidates:
        candidates(records, args.candidates)
        return 0

    unambiguous, ambiguous = generate(records)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    before = _render(config)

    # Only the generated block is rewritten. Curated sections are never touched
    # by this script -- that separation is the point.
    config["catalogues"]["lineage_b"]["generated_product_names"] = unambiguous
    after = _render(config)

    if args.check:
        if before != after:
            print(
                "config/product-synonyms.json is stale. Run:\n"
                "  python scripts/generate_synonyms.py",
                file=sys.stderr,
            )
            return 1
        print(f"synonyms current: {len(unambiguous)} generated names")
        return 0

    CONFIG.write_text(after, encoding="utf-8", newline="\n")
    print(f"wrote {len(unambiguous)} generated product names to {CONFIG.name}")
    print(f"dropped {len(ambiguous)} ambiguous names (same name, different sizes)")
    for term, keys in list(ambiguous.items())[:5]:
        print(f"  {term!r} -> {keys}")
    if len(ambiguous) > 5:
        print(f"  ... and {len(ambiguous) - 5} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
