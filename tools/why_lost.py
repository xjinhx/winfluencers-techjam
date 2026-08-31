"""Why did the target lose to the candidates above it?

For every session whose target landed at a given rank band, this compares the
target's feature vector against the vectors of the candidates that outranked it
and decomposes the score gap feature by feature.

The decomposition is the point. A raw feature delta says the winner had more of
something; it does not say that something mattered. Multiplying the delta by the
feature's weight gives its actual contribution to the score gap, and those
contributions sum exactly to the gap. Features are ranked by mean contribution,
so the output answers "which feature is costing us rank 1" rather than "which
features happen to differ".

    python -m tools.why_lost --trace <path> --ranks 3,4,5

Read the `share` column alongside `mean_gap`: a feature that dominates the gap
in a handful of sessions is a different finding from one that costs a little
everywhere, and only the latter is worth reweighting globally.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

from shopping_copilot.config import Config
from shopping_copilot.features import FEATURE_NAMES
from shopping_copilot.ranking import INTENT_OVERRIDABLE, build_linear_weights

from tools.offline_eval import (
    join_by_order,
    ReplayScorer,
    TOP_K,
    load_labels,
    load_trace,
    offline_metrics,
    rank_turn,
)


def gap_rows(groups, joined, scorer, weights, ranks, max_winners, config_ranking, config=None):
    """One record per (session, winning candidate) pair in the rank band."""
    replay = offline_metrics(groups, joined, scorer, config)
    by_sample = {label["sample_id"]: (sid, label) for sid, label in joined.items()}
    selected = [
        s for s in replay["sessions"]
        if s["best_rank"] is not None and s["best_rank"] in ranks
    ]

    by_session = defaultdict(dict)
    for (session_id, turn), rows in groups.items():
        by_session[session_id][turn] = rows

    records = []
    for session in selected:
        sid, label = by_sample[session["sample_id"]]
        turn = session["first_hit_turn"]
        rows = by_session[sid][turn]
        vectors = {r["candidate_asin"]: r["features"] for r in rows}
        ordered, _ = rank_turn(rows, scorer)
        ordered = ordered[:TOP_K]

        turn_intent = rows[0].get("intent")
        turn_weights = dict(weights)
        for feature in INTENT_OVERRIDABLE:
            override = getattr(config_ranking, f"w_{feature}_{turn_intent}", None)
            if override is not None:
                turn_weights[feature] = override

        target = label["target"]
        target_vec = vectors[target]
        winners = ordered[: ordered.index(target)][:max_winners]

        for winner in winners:
            win_vec = vectors[winner]
            contributions = {
                name: (win_vec[i] - target_vec[i]) * turn_weights.get(name, 0.0)
                for i, name in enumerate(FEATURE_NAMES)
            }
            records.append({
                "sample_id": sid,
                "scenario_type": session["scenario_type"],
                "difficulty_bucket": session["difficulty_bucket"],
                "best_rank": session["best_rank"],
                "winner": winner,
                "score_gap": scorer(win_vec, turn_intent) - scorer(target_vec, turn_intent),
                "contributions": contributions,
                "raw_delta": {
                    name: win_vec[i] - target_vec[i]
                    for i, name in enumerate(FEATURE_NAMES)
                },
            })
    return selected, records


def summarise(records) -> list[dict]:
    """Aggregate per-feature contribution across all (session, winner) pairs."""
    if not records:
        return []
    per_feature = defaultdict(list)
    for record in records:
        for name, value in record["contributions"].items():
            per_feature[name].append(value)

    total_gap = sum(r["score_gap"] for r in records)
    summary = []
    for name, values in per_feature.items():
        mean = statistics.fmean(values)
        against = sum(1 for v in values if v > 1e-12)
        summary.append({
            "feature": name,
            "mean_gap": mean,
            "share": (mean * len(values) / total_gap) if total_gap else 0.0,
            "against_rate": against / len(values),
            "max_single": max(values),
        })
    summary.sort(key=lambda item: -item["mean_gap"])
    return summary


def print_table(summary, records, top) -> None:
    print(f"\n{'feature':<24}{'mean_gap':>12}{'share':>10}{'against':>10}{'worst':>12}")
    print("-" * 68)
    for row in summary[:top]:
        print(f"{row['feature']:<24}{row['mean_gap']:>12.5f}{row['share']:>9.1%}"
              f"{row['against_rate']:>10.0%}{row['max_single']:>12.5f}")

    print(f"\n{'':<24}{'':>12}{'':>10}{'':>10}")
    gaps = [r["score_gap"] for r in records]
    print(f"pairs analysed: {len(records)}   "
          f"mean score gap: {statistics.fmean(gaps):.5f}   "
          f"median: {statistics.median(gaps):.5f}")


def print_slices(records) -> None:
    """A pattern confined to one slice is more actionable than a global one."""
    for key in ("scenario_type", "difficulty_bucket"):
        buckets = defaultdict(list)
        for record in records:
            buckets[record[key]].append(record)
        print(f"\nby {key}:")
        for name, rows in sorted(buckets.items()):
            top = summarise(rows)[:3]
            leaders = ", ".join(f"{r['feature']} {r['mean_gap']:+.4f}" for r in top)
            print(f"  {name:<18} n={len(rows):<4} {leaders}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--public-set", default="data/public_set.jsonl")
    parser.add_argument("--config", default="config/tuned.json")
    parser.add_argument("--ranks", default="3,4,5")
    parser.add_argument("--max-winners", type=int, default=2,
                        help="how many candidates above the target to compare against")
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    ranks = {int(value) for value in args.ranks.split(",")}
    config = Config.load(args.config)
    weights = build_linear_weights(config.ranking, config.priors, config.constraints)

    labels = load_labels(args.public_set)
    groups, order = load_trace(args.trace)
    joined = join_by_order(order, labels)
    scorer = ReplayScorer(config)

    selected, records = gap_rows(groups, joined, scorer, weights, ranks, args.max_winners, config.ranking, config)
    print(f"sessions at ranks {sorted(ranks)}: {len(selected)}")
    if not records:
        print("no comparable pairs found")
        return

    summary = summarise(records)
    print_table(summary, records, args.top)
    print_slices(records)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"summary": summary, "sessions": selected}, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
