"""Step 3 of `PRD_pairwise_rerank.md` -- the separability gate.

The question this answers, before any model is fitted:

    For the sessions where the target lands below rank 1, does ANY linear
    function of the feature vector rank the target above the item(s) that beat
    it?

For each such session take the target's feature vector `t` and a winner's `x`,
and form `d = t - x`. A linear scorer with weights `w` ranks the target first
exactly when `w . d > 0`. So the whole question is whether the set {d_i} can be
strictly separated from the origin -- a feasibility problem over 39 short
vectors, not a training run.

If it is feasible, a learned reranker has something real to find. If only a
small subset is satisfiable, that subset size is a hard ceiling on what ANY
linear reranker can recover here, learned or hand-tuned, and the answer is a
new feature rather than better weights.

Two vector spaces are tested:

  * the raw 30 `FEATURE_NAMES` columns, and
  * those plus six derived `{dim}_unknown` indicators, which is what the
    trainer would actually see (PRD Step 4) -- the live scorer applies the
    unknown-penalty outside the model, so a model without these columns cannot
    express what the incumbent expresses.

Stdlib only, consistent with the rest of `shopping_copilot/`.

Run:
    python -m tools.separability --trace <scratch>/features.jsonl
    python -m tools.separability --trace <trace> --ranks 2,3,4,5
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from shopping_copilot.config import Config
from shopping_copilot.features import FEATURE_INDEX, FEATURE_NAMES
from tools.offline_eval import (
    DIMENSIONS,
    MAX_TURNS,
    TOP_K,
    ReplayScorer,
    join_by_order,
    load_labels,
    load_trace,
    rank_turn,
)

UNKNOWN_NAMES = tuple(f"{d}_unknown" for d in DIMENSIONS)


# -- feature space ---------------------------------------------------------


def augment(vector: list[float]) -> list[float]:
    """The 30 live columns plus six `{dim}_unknown` indicators.

    Mirrors `Ranker.score_candidate`'s post-hoc penalty condition exactly:
    a dimension is 'unknown' when the candidate neither satisfies nor violates
    it. Deriving it here rather than in `features.py` keeps `FEATURE_NAMES`
    untouched -- see PRD Step 4.
    """
    extra = []
    for dimension in DIMENSIONS:
        satisfied = vector[FEATURE_INDEX[f"{dimension}_satisfied"]]
        violated = vector[FEATURE_INDEX[f"{dimension}_violated"]]
        extra.append(1.0 if satisfied == 0.0 and violated == 0.0 else 0.0)
    return list(vector) + extra


# -- pair extraction -------------------------------------------------------


def losing_pairs(
    groups: dict[tuple[str, int], list[dict]],
    joined: dict[str, dict],
    score_fn,
    ranks: set[int],
) -> tuple[list[dict], dict[int, int]]:
    """One record per (session, winner) for every session whose best_rank is
    in `ranks`.

    Replays the evaluator's per-turn hit test the same way
    `offline_eval.replay_session` does -- including the per-sample override
    turn -- then, at the turn that produced the hit, reads the ordered pool and
    pairs the target against everything ranked above it.
    """
    by_session: dict[str, dict[int, list[dict]]] = defaultdict(dict)
    for (session_id, turn), rows in groups.items():
        by_session[session_id][turn] = rows

    pairs: list[dict] = []
    rank_counts: dict[int, int] = defaultdict(int)

    for session_id, turns in by_session.items():
        label = joined[session_id]
        target = label["target"]
        override_turn = label["override_turn"]
        override_applied = label["scenario_type"] != "intent_override"

        for turn in sorted(turns):
            ordered = rank_turn(turns[turn], score_fn)[:TOP_K]
            if override_applied and target in ordered:
                rank = ordered.index(target) + 1
                rank_counts[rank] += 1
                if rank in ranks:
                    vectors = {
                        row["candidate_asin"]: row["features"] for row in turns[turn]
                    }
                    for winner in ordered[: rank - 1]:
                        pairs.append({
                            "session_id": session_id,
                            "sample_id": label["sample_id"],
                            "scenario_type": label["scenario_type"],
                            "intent": turns[turn][0].get("intent"),
                            "rank": rank,
                            "target": target,
                            "winner": winner,
                            "d30": [
                                a - b
                                for a, b in zip(vectors[target], vectors[winner])
                            ],
                            "d36": [
                                a - b
                                for a, b in zip(
                                    augment(vectors[target]), augment(vectors[winner])
                                )
                            ],
                        })
                break
            if turn == MAX_TURNS:
                break
            if not override_applied and turn + 1 == override_turn:
                override_applied = True

    return pairs, dict(rank_counts)


# -- separability ----------------------------------------------------------


def normalise(vectors: list[list[float]]) -> list[list[float]]:
    """Unit-length rows. Scaling a row by a positive constant cannot change the
    sign of `w . d`, so this is free, and it keeps the perceptron well behaved
    when columns differ in scale by orders of magnitude."""
    out = []
    for v in vectors:
        norm = sum(x * x for x in v) ** 0.5
        out.append([x / norm for x in v] if norm else list(v))
    return out


def perceptron(vectors: list[list[float]], epochs: int = 20000, seed: int = 7):
    """Find `w` with `w . d > 0` for every d, or fail.

    Convergence is a *proof* of separability. Failure within the budget is
    strong evidence against it but not a proof, which is why the caller also
    reports the largest subset it could satisfy.
    """
    if not vectors:
        return None, 0
    dim = len(vectors[0])
    w = [0.0] * dim
    order = list(range(len(vectors)))
    rng = random.Random(seed)
    for epoch in range(epochs):
        mistakes = 0
        rng.shuffle(order)
        for i in order:
            d = vectors[i]
            if sum(wi * di for wi, di in zip(w, d)) <= 0.0:
                w = [wi + di for wi, di in zip(w, d)]
                mistakes += 1
        if mistakes == 0:
            return w, epoch + 1
    return None, epochs


def satisfied_count(w: list[float], vectors: list[list[float]]) -> int:
    return sum(
        1 for d in vectors if sum(wi * di for wi, di in zip(w, d)) > 0.0
    )


def logistic_fit(
    vectors: list[list[float]],
    steps: int = 4000,
    lr: float = 0.5,
    l2: float = 1e-4,
) -> list[float]:
    """Minimise mean log(1 + exp(-w.d)) with L2. Used only to find a good
    weight vector when the set is not separable, so its satisfied-count is a
    *lower bound* on the largest satisfiable subset."""
    if not vectors:
        return []
    dim = len(vectors[0])
    w = [0.0] * dim
    n = len(vectors)
    for _ in range(steps):
        grad = [0.0] * dim
        for d in vectors:
            z = sum(wi * di for wi, di in zip(w, d))
            # derivative of log(1+exp(-z)) wrt z is -sigmoid(-z)
            if z > 30:
                s = 0.0
            elif z < -30:
                s = 1.0
            else:
                e = 2.718281828459045 ** (-z)
                s = e / (1.0 + e)
            for j, dj in enumerate(d):
                grad[j] -= s * dj
        w = [
            wi - lr * (gj / n + l2 * wi)
            for wi, gj in zip(w, grad)
        ]
    return w


def greedy_max_subset(vectors: list[list[float]], epochs: int = 3000):
    """Largest subset this could actually satisfy, by dropping the pair the
    logistic fit violates worst and retrying until the remainder separates.

    Exact maximum-feasible-subset is NP-hard; this is a constructive lower
    bound, which is the useful direction -- it says 'at least this many are
    reachable', so a small number is a genuine ceiling signal."""
    keep = list(range(len(vectors)))
    while keep:
        subset = [vectors[i] for i in keep]
        w, _ = perceptron(subset, epochs=epochs)
        if w is not None:
            return keep, w
        w = logistic_fit(subset)
        margins = [
            (sum(wi * di for wi, di in zip(w, vectors[i])), i) for i in keep
        ]
        margins.sort()
        keep.remove(margins[0][1])
    return [], []


# -- reporting -------------------------------------------------------------


def report(name: str, pairs: list[dict], key: str, names: tuple[str, ...]) -> dict:
    vectors = normalise([p[key] for p in pairs])
    total = len(vectors)
    print(f"\n--- {name} ({len(names)} columns, {total} pairs) ---")
    if not total:
        print("  no pairs")
        return {}

    w, epochs = perceptron(vectors)
    if w is not None:
        print(f"  SEPARABLE -- perceptron converged in {epochs} epochs.")
        print("  A linear reranker can in principle win every one of these pairs.")
        best_n, best_w = total, w
    else:
        print(f"  NOT separable within {epochs} epochs.")
        soft = logistic_fit(vectors)
        print(f"  logistic fit satisfies {satisfied_count(soft, vectors)}/{total}")
        keep, best_w = greedy_max_subset(vectors)
        best_n = len(keep)
        print(f"  largest subset found separable: {best_n}/{total} "
              f"({best_n / total:.0%})  <- lower bound on the true maximum")
        print(f"  ceiling: at most {total - best_n} of these {total} pairs are "
              f"provably out of reach of any linear reranker on this vector "
              f"(at least, by this construction)")

    if best_w:
        ordered = sorted(
            zip(names, best_w), key=lambda kv: -abs(kv[1])
        )[:8]
        print("  weights it leans on (|largest| first):")
        for feature_name, weight in ordered:
            print(f"      {feature_name:24s} {weight:+.4f}")
    return {"pairs": total, "separable_subset": best_n}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", required=True)
    parser.add_argument("--public-set", default="data/public_set.jsonl")
    parser.add_argument("--config", default="config/tuned.json")
    parser.add_argument("--ranks", default="2",
                        help="comma-separated best_rank values to analyse")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    ranks = {int(r) for r in args.ranks.split(",") if r.strip()}
    config = Config.load(args.config)
    score_fn = ReplayScorer(config)

    groups, order = load_trace(args.trace)
    labels = load_labels(args.public_set)
    joined = join_by_order(order, labels)

    pairs, rank_counts = losing_pairs(groups, joined, score_fn, ranks)

    print(f"replayed {len(order)} sessions from {args.trace}")
    print("rank distribution (replayed):",
          {r: rank_counts[r] for r in sorted(rank_counts)})
    print(f"analysing ranks {sorted(ranks)} -> {len(pairs)} (target, winner) pairs "
          f"across {len({p['session_id'] for p in pairs})} sessions")

    by_scenario: dict[str, int] = defaultdict(int)
    for pair in pairs:
        by_scenario[pair["scenario_type"]] += 1
    print("pairs by scenario:", dict(by_scenario))

    raw = report("raw feature vector", pairs, "d30", FEATURE_NAMES)
    aug = report("with derived {dim}_unknown columns", pairs, "d36",
                 FEATURE_NAMES + UNKNOWN_NAMES)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({
                "ranks": sorted(ranks),
                "pairs": len(pairs),
                "sessions": len({p["session_id"] for p in pairs}),
                "rank_counts": rank_counts,
                "raw": raw,
                "augmented": aug,
            }, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
