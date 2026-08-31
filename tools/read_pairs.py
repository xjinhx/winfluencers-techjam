"""Read the actual listing text of near-miss targets against the candidates that
beat them, alongside what the customer had *actually said* when the rank was
locked in.

The roadmap carried "read the text of the rank-2 targets vs their winners" for
days as the named next step, on the theory that a new feature was needed. This
tool is what answered it, and the answer was the opposite: nothing was missing
from the feature vector, the information simply had not been disclosed yet.

Why an instrumented run rather than the feature trace: `features_*.jsonl` records
vectors, not dialogue, so it cannot show what the customer had said. This wraps
the real `Agent`, records per turn the message, the spans live at that moment and
the ranking returned, then joins positionally to the labels -- the evaluator hands
the agent a fresh uuid4, so an id join is impossible (see `tools/offline_eval.py`).

Each pair gets one of three verdicts, which is the part worth reusing on any band:

    A  what was disclosed already separated them  -> a RANKING defect
    B  separable only once more is disclosed      -> a TIMING defect
    C  not separable even with the full card      -> a structural tie

A ceiling is only real if C is large. On the 2026-08-31 build, rank 2 read
A=0, B=27, C=3 -- so the near misses were not the ranker's fault, and the
structural floor under them was small.

    python -m tools.read_pairs --ranks 2 --pack <scratch>/pack.txt

Writes nothing unless asked, and never touches results.json or config/tuned.json.
"""

from __future__ import annotations

import argparse
import json
import statistics
import textwrap
from collections import Counter
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, intent_card, load_jsonl
from shopping_copilot.agent import Agent
from shopping_copilot.catalog import Catalog
from shopping_copilot.config import Config

CATALOG = "data/catalog.jsonl"
DATASET = "data/public_set.jsonl"


def capture(catalog_path: str, dataset_path: str, config: Config):
    """Run the official evaluator against an agent that records its dialogue."""
    log: list[dict] = []
    order: list[str] = []
    seen: set[str] = set()

    class Recording(Agent):
        def respond(self, session_id, user_message, turn, top_k):
            out = super().respond(session_id, user_message, turn, top_k)
            if session_id not in seen:
                seen.add(session_id)
                order.append(session_id)
            state = self.sessions.get(session_id)
            log.append({
                "session_id": session_id,
                "turn": turn,
                "message": user_message,
                "spans": list(state.active_spans()) if state else [],
                "recs": [r["parent_asin"] for r in out.get("recommendations", [])][:10],
            })
            return out

    samples = load_jsonl(dataset_path)
    ids, categories, products = catalog_index(catalog_path)
    result = evaluate(Recording(catalog_path, config=config), samples,
                      ids, categories, products)
    return log, order, result, samples, products


def card_spans(product: dict) -> list[str]:
    """The full constraint card, normalised the way `active_spans` normalises it."""
    card = intent_card(product)
    spans = [
        " ".join(str(value).lower().split())
        for value in card["hard_constraints"] + card["soft_preferences"]
    ]
    return [span for span in dict.fromkeys(spans) if len(span) > 3]


def verdict(said: int, total: int, target_hits: int, winner_hits: int) -> str:
    """A / B / C -- see the module docstring."""
    if target_hits <= winner_hits:
        return "C"                      # the card cannot separate them at all
    return "B" if said < total else "A"


def _wrap(text: str, indent: str = "        ", width: int = 96) -> str:
    body = " ".join(str(text or "").split())
    return textwrap.fill(body, width, initial_indent=indent,
                         subsequent_indent=indent) or indent + "(none)"


def build(result, samples, products, catalog, log, order, ranks):
    """One row + one readable block per (session, winner) pair in the band."""
    turns: dict[str, dict[int, dict]] = {}
    for row in log:
        turns.setdefault(row["session_id"], {})[row["turn"]] = row
    sid_of = {samples[i]["sample_id"]: order[i] for i in range(len(samples))}

    lines: list[str] = []
    rows: list[dict] = []
    for session in result["sessions"]:
        if session["best_rank"] not in ranks:
            continue
        sample = next(s for s in samples if s["sample_id"] == session["sample_id"])
        sid = sid_of[session["sample_id"]]
        hit = session["first_hit_turn"]
        record = turns[sid][hit]
        target = sample["ground_truth"]["parent_asin"]
        winner = record["recs"][session["best_rank"] - 2]

        spans = card_spans(products[target])
        said = record["spans"]
        unsaid = [s for s in spans if not any(s.startswith(t) or t in s for t in said)]
        target_product = catalog.by_asin[target]
        winner_product = catalog.by_asin[winner]

        def matched(product) -> int:
            return sum(1 for span in spans if span in product.search_blob)

        row = {
            "sample_id": session["sample_id"],
            "scenario": session["scenario_type"],
            "hit_turn": hit,
            "said": len(said),
            "card": len(spans),
            "target_hits": matched(target_product),
            "winner_hits": matched(winner_product),
            "target": target,
            "winner": winner,
            "target_ratings": target_product.rating_number,
            "winner_ratings": winner_product.rating_number,
        }
        row["verdict"] = verdict(row["said"], row["card"],
                                 row["target_hits"], row["winner_hits"])
        rows.append(row)

        lines.append("=" * 104)
        lines.append(f"{session['sample_id']}  [{session['scenario_type']}]  "
                     f"locked in at TURN {hit}  -> verdict {row['verdict']}")
        lines.append("")
        lines.append("  WHAT THE CUSTOMER HAD SAID BY THEN:")
        for turn in sorted(turns[sid]):
            if turn <= hit:
                lines.append(_wrap(f"turn {turn}: {turns[sid][turn]['message']}", "      "))
        lines.append(f"      -> live spans ({len(said)}): {said}")
        if unsaid:
            lines.append(f"      -> NOT yet said ({len(unsaid)}): {[u[:60] for u in unsaid]}")
        for tag, product in (("TARGET (should win)", target_product),
                             ("WINNER (actually won)", winner_product)):
            lines.append("")
            lines.append(f"  {tag}  {product.parent_asin}  {product.store!r}  "
                         f"${product.price}  {product.rating_number} ratings "
                         f"({product.average_rating})  "
                         f"matches {matched(product)}/{len(spans)} of full card")
            lines.append("      title:")
            lines.append(_wrap(product.title))
            lines.append("      features:")
            lines.append(_wrap(product.features_text[:620]))
        lines.append("")
    return rows, lines


def report(rows) -> None:
    counts = Counter(row["verdict"] for row in rows)
    print(f"\npairs: {len(rows)}    verdicts: {dict(counts)}")
    print(f"  A (ranking defect) {counts['A']:>3}    "
          f"B (timing) {counts['B']:>3}    C (structural tie) {counts['C']:>3}")
    if not rows:
        return
    print(f"median spans live at lock-in : "
          f"{statistics.median([r['said'] for r in rows])} of "
          f"{statistics.median([r['card'] for r in rows])} in the card")
    print(f"zero spans live at lock-in   : "
          f"{sum(1 for r in rows if r['said'] == 0)}/{len(rows)}")
    print(f"winner more popular          : "
          f"{sum(1 for r in rows if r['winner_ratings'] > r['target_ratings'])}/{len(rows)}")
    ties = [r for r in rows if r["verdict"] == "C"]
    if ties:
        print("\nstructural ties (these, and only these, bound the ceiling):")
        for row in ties:
            print(f"  {row['sample_id']}  {row['target']} vs {row['winner']}  "
                  f"{row['target_hits']}/{row['card']} vs {row['winner_hits']}/{row['card']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ranks", default="2", help="rank band, e.g. 2 or 3,4,5")
    parser.add_argument("--config", default="config/tuned.json")
    parser.add_argument("--catalog", default=CATALOG)
    parser.add_argument("--dataset", default=DATASET)
    parser.add_argument("--pack", default=None, help="write the readable pack here")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    ranks = {int(value) for value in args.ranks.split(",")}
    log, order, result, samples, products = capture(
        args.catalog, args.dataset, Config.load(args.config))
    print(f"run reproduces TechnicalScore={result['recommended_technical_score']} "
          f"HR@10={result['hit_rate_at_10']}")

    catalog = Catalog(path=args.catalog)
    rows, lines = build(result, samples, products, catalog, log, order, ranks)
    report(rows)

    if args.pack:
        Path(args.pack).parent.mkdir(parents=True, exist_ok=True)
        Path(args.pack).write_text("\n".join(lines), encoding="utf-8")
        print(f"\npack written: {args.pack}")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"rows written: {args.json_out}")


if __name__ == "__main__":
    main()
