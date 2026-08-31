"""PRD Phase 3 -- pairwise learning-to-rank fit, as an alternative to
coordinate ascent.

`tools/tune.py` fits weights one parameter at a time, which cannot express
interactions and, per this project's own measurement discipline, has been
shown to overfit a 100-session train fold even with just two hand-picked
parameters (see CLAUDE.md, "bm25/phrase weight trade-off"). This module
fits the same named weights jointly via pairwise logistic regression with
L2 shrinkage toward the current tuned values, which is the direct
mitigation for that overfitting risk.

Pure stdlib -- no numpy, no external solver. Training pairs are
(target, candidate currently ranked above it) for every train-fold turn
where the target is present but not already ranked #1 -- turns where the
target already ranks first contribute nothing to learn from.

`fused` is deliberately excluded from the fitted weights and held at its
existing per-intent value (0.0 for buying/uncertain, 1.0 for browsing).
That value is under the control of the already-validated intent-conditional
mechanism in ranking.py; refitting it here would fight that mechanism
rather than complement it, and `ScoringModel.score()` has no intent
parameter to make a per-intent fused weight correct in the first place.

Output is a config JSON with the same shape as config/tuned.json --
evaluate it exactly like any other candidate config, never write to
config/tuned.json directly.

Run:
    python -m tools.train_pairwise --output <scratch>/pairwise.json \
        --report <scratch>/pairwise_report.json
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from shopping_copilot.agent import Agent
from shopping_copilot.config import Config
from shopping_copilot.features import FEATURE_NAMES
from shopping_copilot.ranking import build_linear_weights
from tools.evalkit import Bench, summarise, write_json
from tools.offline_eval import ReplayScorer, rank_turn
from tools.tune import stratified_halves

CATALOG = "data/catalog.jsonl"
DATASET = "data/public_set.jsonl"

# fused, bm25_description, bm25_store are excluded from the trainable set.
# `fused` is under the already-validated intent-conditional mechanism (see
# module docstring). bm25_description/bm25_store are not real tunable
# config fields at all -- build_linear_weights() hardcodes both to 0.0 --
# so training a weight for them and then discarding it at config-write
# time would be a real train/eval mismatch, not a modeling choice.
_FROZEN_AT_ZERO = {"bm25_description", "bm25_store"}
FUSED_INDEX = FEATURE_NAMES.index("fused")
REST_NAMES = [name for name in FEATURE_NAMES if name != "fused" and name not in _FROZEN_AT_ZERO]
REST_INDICES = [FEATURE_NAMES.index(name) for name in REST_NAMES]


def _fused_weight_for_intent(ranking_config, intent: str | None) -> float:
    override = getattr(ranking_config, f"w_fused_{intent}", None)
    return ranking_config.w_fused if override is None else override


def generate_trace(base_config: Config, samples: list[dict], trace_path: str) -> None:
    """Runs the real evaluator over exactly `samples`, tracing every scored
    candidate. Used to generate a train-fold-only trace -- the holdout fold
    is never passed through this function during training."""
    Path(trace_path).unlink(missing_ok=True)
    cfg = copy.deepcopy(base_config)
    cfg.trace_path = trace_path
    agent = Agent(CATALOG, config=cfg)
    catalog_ids, categories, products = catalog_index(CATALOG)
    evaluate(agent, samples, catalog_ids, categories, products)
    agent.close()


def load_trace_groups(trace_path: str) -> dict[tuple[str, int], list[dict]]:
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    with Path(trace_path).open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            groups[(row["session_id"], row["turn"])].append(row)
    return groups


def build_pairs(
    groups: dict[tuple[str, int], list[dict]],
    session_to_target: dict[str, str],
    base_config: Config,
) -> list[tuple[float, list[float], list[float]]]:
    """One (fixed_fused_term, target_rest_vector, winner_rest_vector) record
    per (turn, candidate-currently-ranked-above-target) pair.

    Restricting to candidates *currently* above the target -- not every
    non-target candidate -- concentrates the training signal on turns the
    system is actually getting wrong, mirroring exactly what
    `tools.why_lost` already measures.
    """
    scorer = ReplayScorer(base_config)
    pairs: list[tuple[float, list[float], list[float]]] = []
    for (session_id, turn), rows in groups.items():
        target = session_to_target.get(session_id)
        if not target:
            continue
        by_asin = {row["candidate_asin"]: row["features"] for row in rows}
        if target not in by_asin:
            continue  # recall failure this turn, not a ranking example
        intent = rows[0].get("intent")
        ordered, _ = rank_turn(rows, lambda vec, i=intent: scorer(vec, i))
        target_rank = ordered.index(target)
        if target_rank == 0:
            continue  # already ranked first -- nothing to learn from this turn
        target_vec = by_asin[target]
        fused_w = _fused_weight_for_intent(base_config.ranking, intent)
        for winner_asin in ordered[:target_rank]:
            winner_vec = by_asin[winner_asin]
            fixed_term = fused_w * (target_vec[FUSED_INDEX] - winner_vec[FUSED_INDEX])
            target_rest = [target_vec[i] for i in REST_INDICES]
            winner_rest = [winner_vec[i] for i in REST_INDICES]
            pairs.append((fixed_term, target_rest, winner_rest))
    return pairs


def _sigmoid(z: float) -> float:
    if z < -30:
        return 0.0
    if z > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def fit_pairwise(
    pairs: list[tuple[float, list[float], list[float]]],
    init_weights: list[float],
    *,
    epochs: int = 40,
    lr: float = 0.02,
    l2: float = 0.08,
    seed: int = 7,
) -> list[float]:
    """Pairwise logistic SGD with L2 shrinkage toward `init_weights`
    (the current tuned values), not toward zero. This is the direct
    mitigation for the overfitting risk this project has already
    demonstrated empirically with far fewer free parameters -- the fit is
    a small, regularized correction to known-good weights, not a model
    learned from scratch."""
    n = len(init_weights)
    w = list(init_weights)
    w0 = list(init_weights)
    rng = random.Random(seed)
    order = list(range(len(pairs)))
    for _ in range(epochs):
        rng.shuffle(order)
        for idx in order:
            fixed_term, pos, neg = pairs[idx]
            margin = fixed_term + sum(w[j] * (pos[j] - neg[j]) for j in range(n))
            grad_scale = _sigmoid(margin) - 1.0  # d(softplus(-margin))/d(margin)
            for j in range(n):
                diff = pos[j] - neg[j]
                grad = grad_scale * diff + 2.0 * l2 * (w[j] - w0[j])
                w[j] -= lr * grad
    return w


def weights_to_config(base_config: Config, rest_weights: list[float]) -> Config:
    """Writes the fitted REST_NAMES values back into a deep copy of
    base_config's ranking/priors/constraints sections. `fused` and every
    w_fused_{intent} override are left untouched."""
    cfg = copy.deepcopy(base_config)
    by_name = dict(zip(REST_NAMES, rest_weights))

    bm25_map = {
        "bm25_title": "w_bm25_title", "bm25_features": "w_bm25_features",
        "bm25_categories": "w_bm25_categories", "dense": "w_dense",
        "phrase_title": "w_phrase_title", "phrase_features": "w_phrase_features",
        "phrase_categories": "w_phrase_categories", "coverage": "w_coverage",
        "profile_affinity": "w_profile_affinity", "category_focus": "w_category_focus",
    }
    prior_map = {
        "popularity": "w_log_rating_number", "quality": "w_average_rating",
        "has_price": "w_has_price", "has_description": "w_has_description",
        "n_features_norm": "w_n_features",
    }
    for feature_name, attr in bm25_map.items():
        setattr(cfg.ranking, attr, by_name[feature_name])
    for feature_name, attr in prior_map.items():
        setattr(cfg.priors, attr, by_name[feature_name])
    for dimension in ("gender", "brand", "category", "price", "material", "color"):
        for outcome in ("satisfied", "violated", "unknown"):
            setattr(cfg.constraints, f"{dimension}_{outcome}", by_name[f"{dimension}_{outcome}"])
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-config", default="config/tuned.json")
    parser.add_argument("--output", required=True, help="fitted config JSON, scratch path")
    parser.add_argument("--report", required=True, help="train/holdout metrics JSON, scratch path")
    parser.add_argument("--train-trace", required=True, help="scratch path for the train-fold trace")
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--lr", type=float, default=0.02)
    parser.add_argument("--l2", type=float, default=0.08)
    args = parser.parse_args()

    base_config = Config.load(args.base_config)
    bench = Bench()
    train, holdout = stratified_halves(bench.samples, seed=7)

    baseline_train = bench.score(base_config, train)["recommended_technical_score"]
    baseline_holdout = bench.score(base_config, holdout)["recommended_technical_score"]
    print(f"baseline (tuned config): train={baseline_train:.4f} holdout={baseline_holdout:.4f}")

    print(f"generating train-fold-only trace ({len(train)} sessions)...")
    generate_trace(base_config, train, args.train_trace)

    labels = load_jsonl(DATASET)
    session_to_target = {}
    trace_groups = load_trace_groups(args.train_trace)
    # Positional join: evaluate() iterates `train` in order, trace records
    # session_id in that same order -- reuse the same join discipline as
    # tools/offline_eval.py rather than re-deriving it.
    seen_order: list[str] = []
    seen: set[str] = set()
    for (session_id, _turn) in trace_groups:
        if session_id not in seen:
            seen.add(session_id)
            seen_order.append(session_id)
    if len(seen_order) != len(train):
        raise SystemExit(f"trace has {len(seen_order)} sessions, train fold has {len(train)} -- cannot join positionally")
    for session_id, sample in zip(seen_order, train):
        session_to_target[session_id] = sample["ground_truth"]["parent_asin"]

    pairs = build_pairs(trace_groups, session_to_target, base_config)
    print(f"built {len(pairs)} training pairs from {len(train)} train-fold sessions")

    init_weights_dict = build_linear_weights(base_config.ranking, base_config.priors, base_config.constraints)
    init_rest = [init_weights_dict[name] for name in REST_NAMES]

    fitted_rest = fit_pairwise(pairs, init_rest, epochs=args.epochs, lr=args.lr, l2=args.l2)

    fitted_config = weights_to_config(base_config, fitted_rest)
    fitted_train = bench.score(fitted_config, train)["recommended_technical_score"]
    fitted_holdout = bench.score(fitted_config, holdout)["recommended_technical_score"]

    print(f"\nfitted: train={fitted_train:.4f} (was {baseline_train:.4f}, delta {fitted_train-baseline_train:+.4f})")
    print(f"        holdout={fitted_holdout:.4f} (was {baseline_holdout:.4f}, delta {fitted_holdout-baseline_holdout:+.4f})")

    noise_floor = 0.05
    if fitted_holdout - baseline_holdout > noise_floor * 0.3 / 1.0 and fitted_holdout > baseline_holdout:
        verdict = "CANDIDATE WIN -- holdout improved; confirm with sign test before adopting"
    elif fitted_holdout > baseline_holdout:
        verdict = "WITHIN NOISE -- holdout improved but not by enough to trust (see CLAUDE.md noise floor)"
    else:
        verdict = "REJECT -- holdout did not improve; the gain (if any on train) is fold-specific"
    print(f"\nverdict: {verdict}")

    fitted_config.save(args.output)
    write_json(args.report, {
        "baseline": {"train": baseline_train, "holdout": baseline_holdout},
        "fitted": {"train": fitted_train, "holdout": fitted_holdout},
        "pair_count": len(pairs),
        "epochs": args.epochs, "lr": args.lr, "l2": args.l2,
        "verdict": verdict,
        "fitted_rest_weights": dict(zip(REST_NAMES, fitted_rest)),
    })
    print(f"\nwrote {args.output} and {args.report}")


if __name__ == "__main__":
    main()
