"""Section 8 -- parameter tuning by coordinate ascent, with split-half CV.

Protocol, and why it is this one:

  * Split the 200 public sessions in half, stratified by scenario. Tune on
    fold A, report the untouched fold B score alongside it. Any parameter whose
    gain does not survive the crossing is noise, and the run says so.
  * Coordinate ascent, one parameter at a time, accepting only strict
    improvements. With 100 sessions per fold there is not enough signal for a
    joint search, and a joint search would mostly fit the fold.
  * The objective is the official `TechnicalScore`, computed by the official
    evaluator. No proxy metric.

This is a small-sample tuner and it should be treated as one: it is here to fix
hand-picked weights that the ablation table shows are wrong, not to squeeze the
last basis point out of fold A.

Run:  python -m tools.tune --output config/tuned.json
      python -m tools.tune --quick        (coarser grid, fewer parameters)
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import time
from dataclasses import replace
from pathlib import Path

from shopping_copilot.config import Config
from tools.evalkit import Bench, summarise, write_json

# (section, attribute, candidate values). Ordered by expected leverage, because
# coordinate ascent is greedy and an interrupted run should already have fixed
# the parameters that matter most.
SEARCH_SPACE: list[tuple[str, str, list[float]]] = [
    ("retrieval", "w_features", [0.6, 0.9, 1.0, 1.25, 1.6]),
    ("retrieval", "w_title", [0.5, 0.8, 1.0, 1.3, 1.7]),
    ("retrieval", "w_categories", [0.4, 0.7, 0.9, 1.2, 1.6]),
    ("retrieval", "w_description", [0.0, 0.25, 0.45, 0.8]),
    ("retrieval", "w_store", [0.0, 0.2, 0.35, 0.6]),
    ("retrieval", "fusion_alpha", [0.55, 0.7, 0.78, 0.88, 1.0]),
    ("ranking", "w_coverage", [0.0, 0.15, 0.30, 0.6]),
    ("ranking", "w_phrase_features", [0.0, 0.25, 0.52, 0.9]),
    ("ranking", "w_phrase_title", [0.0, 0.16, 0.32, 0.6]),
    ("ranking", "w_phrase_categories", [0.0, 0.12, 0.24, 0.5]),
    ("ranking", "w_category_focus", [0.0, 0.07, 0.2, 0.4]),
    ("priors", "w_log_rating_number", [0.05, 0.10, 0.15, 0.22, 0.30]),
    ("ranking", "w_bm25_features", [0.0, 0.15, 0.30, 0.55]),
    ("ranking", "w_bm25_title", [0.0, 0.13, 0.26, 0.5]),
    ("ranking", "w_bm25_categories", [0.0, 0.11, 0.22, 0.45]),
    ("ranking", "w_dense", [0.0, 0.09, 0.18, 0.35]),
    ("ranking", "w_profile_affinity", [0.0, 0.03, 0.08]),
    ("priors", "w_has_price", [0.0, 0.04, 0.09]),
    ("priors", "w_n_features", [0.0, 0.03, 0.07]),
    ("constraints", "gender_violated", [0.0, -0.12, -0.23, -0.4]),
]

QUICK = {
    ("retrieval", "w_features"), ("retrieval", "w_title"),
    ("retrieval", "w_categories"), ("retrieval", "fusion_alpha"),
    ("ranking", "w_coverage"), ("priors", "w_log_rating_number"),
    ("ranking", "w_phrase_features"),
}


def stratified_halves(samples: list[dict], seed: int = 7) -> tuple[list[dict], list[dict]]:
    """Split-half, stratified by scenario and grouped by session.

    Sessions are the unit throughout -- a session never contributes turns to
    both folds, because turns inside one session are anything but independent.
    """
    buckets: dict[str, list[dict]] = {}
    for sample in samples:
        buckets.setdefault(sample["scenario_type"], []).append(sample)
    rng = random.Random(seed)
    fold_a: list[dict] = []
    fold_b: list[dict] = []
    for scenario in sorted(buckets):
        rows = sorted(buckets[scenario], key=lambda s: s["sample_id"])
        rng.shuffle(rows)
        midpoint = len(rows) // 2
        fold_a.extend(rows[:midpoint])
        fold_b.extend(rows[midpoint:])
    fold_a.sort(key=lambda s: s["sample_id"])
    fold_b.sort(key=lambda s: s["sample_id"])
    return fold_a, fold_b


def get_value(config: Config, section: str, attribute: str) -> float:
    return getattr(getattr(config, section), attribute)


def set_value(config: Config, section: str, attribute: str, value: float) -> Config:
    clone = copy.deepcopy(config)
    setattr(getattr(clone, section), attribute, value)
    return clone


def main() -> None:
    parser = argparse.ArgumentParser(description="Coordinate-ascent tuner")
    parser.add_argument("--output", default="config/tuned.json")
    parser.add_argument("--report", default="docs/tuning_report.json")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--passes", type=int, default=1)
    args = parser.parse_args()

    bench = Bench()
    train, holdout = stratified_halves(bench.samples)
    space = [item for item in SEARCH_SPACE if not args.quick or (item[0], item[1]) in QUICK]

    config = Config()
    best = bench.score(config, train)["recommended_technical_score"]
    start_train = best
    start_holdout = bench.score(config, holdout)["recommended_technical_score"]
    print(f"baseline   train={start_train:.4f}  holdout={start_holdout:.4f}")
    print(f"tuning {len(space)} parameters over {len(train)} train sessions\n")

    history = []
    started = time.time()
    for pass_index in range(args.passes):
        for section, attribute, values in space:
            current = get_value(config, section, attribute)
            best_value, improved = current, False
            for value in values:
                if value == current:
                    continue
                trial = set_value(config, section, attribute, value)
                score = bench.score(trial, train)["recommended_technical_score"]
                if score > best + 1e-9:
                    best, best_value, improved = score, value, True
            if improved:
                config = set_value(config, section, attribute, best_value)
            marker = "*" if improved else " "
            print(
                f"{marker} pass{pass_index} {section}.{attribute:22} "
                f"{current!s:>7} -> {best_value!s:<7} train={best:.4f}"
            )
            history.append({
                "pass": pass_index,
                "parameter": f"{section}.{attribute}",
                "from": current,
                "to": best_value,
                "train_score": round(best, 6),
                "changed": improved,
            })

    final_holdout = bench.score(config, holdout)
    final_train = bench.score(config, train)
    elapsed = round(time.time() - started, 1)

    print("\n--- result ---")
    print(f"train   {start_train:.4f} -> {final_train['recommended_technical_score']:.4f}")
    print(f"holdout {start_holdout:.4f} -> {final_holdout['recommended_technical_score']:.4f}")
    print(f"{elapsed}s elapsed")
    if final_holdout["recommended_technical_score"] <= start_holdout:
        print(
            "\nWARNING: the holdout did not improve. The gain is fold-specific;\n"
            "         ship the defaults rather than these values."
        )

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    config.save(args.output)
    write_json(args.report, {
        "train_sessions": len(train),
        "holdout_sessions": len(holdout),
        "baseline": {"train": start_train, "holdout": start_holdout},
        "tuned": {
            "train": summarise(final_train),
            "holdout": summarise(final_holdout),
        },
        "history": history,
        "elapsed_seconds": elapsed,
    })
    print(f"wrote {args.output} and {args.report}")


if __name__ == "__main__":
    main()
