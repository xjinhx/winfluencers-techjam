"""Measure how much each `ask_attribute` actually NARROWS the catalog.

`tools.measure_attribute_yield` answers "will the customer answer, and with how
much text?", and the shipped `attribute_prior` is its normalised `mean_chars`.
That was the right question while spans were only a ranking feature. It is the
wrong question now that `Agent._span_survivors` injects candidates by verbatim
conjunction: the injection's power comes from spans the catalog rarely
contains, and text volume is not selectivity.

    "Imported"                    8 chars,  ~11k products contain it
    "95% Polyester, 5% Spandex"  25 chars,  a few dozen do

Both look like a productive answer to `mean_chars`. Only one lets the injection
fire, and only one moves the target toward rank 1.

So measure the thing that matters: for the spans an attribute discloses, how
many products survive the conjunction? Reported per attribute:

    yield_rate      fraction of sessions where the question discloses anything
    mean_survivors  mean products containing a disclosed span, verbatim
    median_bits     information a disclosed span carries, -log2(survivors/N)
    expected_bits   yield_rate x median_bits -- the value of spending a turn

The last column is the one to rank on, and the tail of this report is a drop-in
`attribute_prior` normalised from it.

Run:  python -m tools.measure_span_selectivity
      python -m tools.measure_span_selectivity --json-out <path>
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict

from evaluator.local_evaluator import (
    ALLOWED_ATTRIBUTES,
    catalog_index,
    classify_constraint,
    load_jsonl,
    materialize_hidden_fields,
)
from shopping_copilot.catalog import Catalog
# Imported rather than re-declared so this tool cannot drift out of sync with
# the matcher it is measuring. If `active_spans()` changes how it normalises,
# these numbers change with it.
from shopping_copilot.state import _SYNTHETIC_SPAN_RE


def normalise(text: str) -> str | None:
    """Reproduce `ShoppingState.active_spans()` exactly.

    This is the whole correctness of the tool. The spans the injection matches
    on are not the raw card strings: they are lowercased, whitespace-collapsed,
    dropped below four characters, and -- since 2026-08-31 -- unwrapped from the
    `color: X` template the simulator manufactures. Measuring the raw strings
    instead counts every synthesised colour span as matching zero products,
    which silently inflates colour's apparent selectivity.
    """
    text = " ".join(str(text).lower().split())
    if len(text) <= 3:
        return None
    synthetic = _SYNTHETIC_SPAN_RE.match(text)
    if synthetic:
        text = synthetic.group(1).strip()
        if not text:
            return None
    return text


def disclosed_spans(card: dict, attribute: str) -> list[str]:
    """What `customer_reply` would hand back for this attribute, normalised the
    way the retriever will actually see it.

    Mirrors the evaluator: every hard constraint then every soft preference,
    filtered by `classify_constraint`, capped at two per turn. 'other' matches
    anything, which is why it scores so well on volume.
    """
    constraints = [
        *[str(v) for v in card.get("hard_constraints", [])],
        *[str(v) for v in card.get("soft_preferences", [])],
    ]
    picked = [
        value for value in constraints
        if attribute == "other" or classify_constraint(value) == attribute
    ][:2]
    return [span for span in (normalise(v) for v in picked) if span]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    samples = load_jsonl(args.dataset)
    _, _, products = catalog_index(args.catalog)
    catalog = Catalog(args.catalog)
    blobs = [p.search_blob for p in catalog.products]
    total = len(blobs)

    # Which spans each attribute would disclose, across every session.
    per_attribute: dict[str, list[list[str]]] = defaultdict(list)
    for sample in samples:
        card, _ = materialize_hidden_fields(sample, products)
        for attribute in ALLOWED_ATTRIBUTES:
            per_attribute[attribute].append(disclosed_spans(card, attribute))

    # One catalog pass per distinct span. The injection tests `span in
    # search_blob`, so measure exactly that rather than a token approximation.
    unique = {s for rows in per_attribute.values() for r in rows for s in r}
    print(f"scanning {len(unique)} distinct spans over {total} products...")
    survivors: dict[str, int] = {}
    for span in sorted(unique):
        survivors[span] = sum(1 for blob in blobs if span in blob)

    def bits(span: str) -> float:
        n = survivors.get(span, 0)
        # A span nothing contains cannot narrow anything the ranker can use.
        return math.log2(total / n) if n > 0 else 0.0

    report: dict[str, dict] = {}
    for attribute in ALLOWED_ATTRIBUTES:
        rows = per_attribute[attribute]
        answered = [r for r in rows if r]
        span_bits = [bits(s) for r in answered for s in r]
        span_survivors = [survivors.get(s, 0) for r in answered for s in r]
        yield_rate = len(answered) / len(rows) if rows else 0.0
        median_bits = statistics.median(span_bits) if span_bits else 0.0
        report[attribute] = {
            "yield_rate": round(yield_rate, 3),
            "mean_survivors": round(statistics.mean(span_survivors), 1) if span_survivors else 0.0,
            "median_bits": round(median_bits, 2),
            "expected_bits": round(yield_rate * median_bits, 3),
        }

    print()
    print("%-10s %11s %15s %12s %14s" % (
        "attribute", "yield_rate", "mean_survivors", "median_bits", "expected_bits"))
    for attribute, row in sorted(report.items(), key=lambda kv: -kv[1]["expected_bits"]):
        print("%-10s %11.3f %15.1f %12.2f %14.3f" % (
            attribute, row["yield_rate"], row["mean_survivors"],
            row["median_bits"], row["expected_bits"]))

    top = max((r["expected_bits"] for r in report.values()), default=0.0) or 1.0
    prior = {a: round(report[a]["expected_bits"] / top, 3) for a in ALLOWED_ATTRIBUTES}
    print("\nattribute_prior (normalised expected_bits):")
    print(json.dumps(prior, indent=4))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump({"report": report, "attribute_prior": prior}, handle, indent=2)
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
