"""Measure how much each `ask_attribute` actually discloses.

The clarification policy needs a prior over attributes. Guessing one is how you
end up spending turns on questions the customer cannot answer. This measures it
instead, on the 200 public sessions, by materialising each session's intent card
and asking every allowed attribute against the documented customer policy.

Reported per attribute:

    yield_rate    fraction of sessions where the question discloses something
    mean_spans    mean number of new constraint spans disclosed
    mean_chars    mean characters of new product text disclosed

`mean_chars` is the one that matters for retrieval: two long feature sentences
are worth far more to a lexical index than two one-word answers.

Run:  python -m tools.measure_attribute_yield
"""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

from evaluator.local_evaluator import (
    ALLOWED_ATTRIBUTES,
    behavior_for,
    customer_reply,
    intent_card,
)


def main() -> None:
    catalog = {}
    with Path("data/catalog.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            catalog[str(row["parent_asin"])] = row
    sessions = [
        json.loads(line)
        for line in Path("data/public_set.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    stats: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for sample in sessions:
        product = catalog[str(sample["ground_truth"]["parent_asin"])]
        card = intent_card(product)
        rng = random.Random(f"{sample.get('sample_id', '')}\0{sample.get('scenario_type', '')}")
        effective = {
            **sample,
            "intent_card": card,
            "behavior": behavior_for(str(sample["scenario_type"]), card, rng),
        }
        for attribute in sorted(ALLOWED_ATTRIBUTES):
            # Fresh disclosure set per attribute: this measures the value of
            # asking it first, independent of ask order.
            reply, _ = customer_reply(effective, attribute, set(), False)
            informative = "don't have" not in reply and "do not have" not in reply
            if informative:
                body = reply.split("what matters is:", 1)[-1]
                spans = len([s for s in body.split(";") if s.strip()])
                stats[attribute].append((spans, len(body.strip())))
            else:
                stats[attribute].append((0, 0))

    rows = []
    for attribute, samples in stats.items():
        spans = [s for s, _ in samples]
        chars = [c for _, c in samples]
        rows.append({
            "attribute": attribute,
            "yield_rate": round(sum(1 for s in spans if s) / len(spans), 4),
            "mean_spans": round(statistics.fmean(spans), 3),
            "mean_chars": round(statistics.fmean(chars), 1),
        })
    rows.sort(key=lambda r: -r["mean_chars"])

    width = max(len(r["attribute"]) for r in rows)
    print(f"{'attribute'.ljust(width)}  yield_rate  mean_spans  mean_chars")
    for row in rows:
        print(
            f"{row['attribute'].ljust(width)}  "
            f"{row['yield_rate']:>10.3f}  {row['mean_spans']:>10.3f}  {row['mean_chars']:>10.1f}"
        )

    # Normalised to the best attribute, ready to paste into DialogueConfig.
    best = max(r["mean_chars"] for r in rows) or 1.0
    prior = {r["attribute"]: round(r["mean_chars"] / best, 3) for r in rows}
    print("\nattribute_prior (normalised mean_chars):")
    print(json.dumps(prior, indent=4))


if __name__ == "__main__":
    main()
