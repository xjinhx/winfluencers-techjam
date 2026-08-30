"""Offline replay of a feature trace, without the simulator.

`Agent._log()` writes one row per scored candidate per turn. This module joins
those rows to the public labels and replays the evaluator's ranking decision,
so a scoring change can be measured in seconds instead of a 3m08s live run.

Reproducing the live ranker takes two details that are easy to miss:

  * MMR never fires (`enable_mmr = False`), so no diversification to replay.
  * `best_rank` is not the rank in the full pool. The evaluator takes the first
    turn where the target appears in the returned top-10, and for
    `intent_override` sessions it ignores any hit before the override turn.

Validated against the live evaluator by `python -m tools.offline_eval`, which
fails loudly if the replayed MRR drifts from the recorded run.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

from shopping_copilot.config import Config
from shopping_copilot.ranking import INTENT_OVERRIDABLE, LinearModel, build_linear_weights

TOP_K = 10
MAX_TURNS = 10
DEFAULT_OVERRIDE_TURN = 3


def load_labels(public_set: str | Path) -> list[dict]:
    """Samples in file order -- the order the evaluator processes them in.

    Returned as a list, not a dict, because the trace cannot be joined by id:
    the evaluator hands the agent a fresh `uuid4` per session, so the only
    stable correspondence between a traced session and its label is position.
    """
    labels = []
    with Path(public_set).open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            labels.append({
                "sample_id": row["sample_id"],
                "target": row["ground_truth"]["parent_asin"],
                "scenario_type": row["scenario_type"],
                "difficulty_bucket": row["difficulty_bucket"],
                "category_bucket": row.get("category_bucket"),
                "override_turn": override_turn_for(row["sample_id"], row["scenario_type"]),
            })
    return labels


def override_turn_for(sample_id: str, scenario_type: str) -> int:
    """Reproduces the evaluator's per-sample override turn.

    `behavior_for` seeds a fresh `random.Random` on
    `f"{sample_id}\\0{scenario_type}"` and its only draw is `choice([3, 4])`,
    so the turn is recoverable exactly without touching evaluator state.
    """
    if scenario_type != "intent_override":
        return DEFAULT_OVERRIDE_TURN
    return random.Random(f"{sample_id}\0{scenario_type}").choice([3, 4])


def load_trace(
    trace_path: str | Path,
) -> tuple[dict[tuple[str, int], list[dict]], list[str]]:
    """Returns the grouped rows plus session ids in first-appearance order."""
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    order: list[str] = []
    seen: set[str] = set()
    with Path(trace_path).open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            session_id = row["session_id"]
            if session_id not in seen:
                seen.add(session_id)
                order.append(session_id)
            groups[(session_id, row["turn"])].append(row)
    return groups, order


def join_by_order(order: list[str], labels: list[dict]) -> dict[str, dict]:
    """traced session_id -> label, matched positionally.

    `evaluate()` iterates `for sample in samples` on a single thread and the
    trace is appended in that same order, so the Nth distinct session id in the
    trace is the Nth sample in the public set. Validated downstream by checking
    every replayed `best_rank` against the recorded run.
    """
    if len(order) != len(labels):
        raise SystemExit(
            f"cannot join: trace has {len(order)} sessions, public set has {len(labels)}. "
            "A partial or concatenated trace cannot be positionally joined."
        )
    return {session_id: label for session_id, label in zip(order, labels)}


class ReplayScorer:
    """The live scoring path, reconstructed from a config alone.

    Mirrors `Ranker.score_candidate`. The unknown-penalty is folded into
    `build_linear_weights()` as ordinary `{dimension}_unknown` columns
    (see `features.py`/`ranking.py`), so `LinearModel.score()` alone
    reproduces the live score -- no separate post-hoc adjustment here.
    """

    def __init__(self, config: Config) -> None:
        base = build_linear_weights(config.ranking, config.priors, config.constraints)
        self.model = LinearModel(base)
        # Mirrors `Ranker.intent_models`: any of INTENT_OVERRIDABLE may carry
        # a per-intent weight, so a replay of an intent-conditional config
        # ranks the way the live agent does.
        self.intent_models: dict[str, LinearModel] = {}
        for intent in ("buying", "browsing", "uncertain"):
            weights: dict[str, float] | None = None
            for feature in INTENT_OVERRIDABLE:
                default = getattr(config.ranking, f"w_{feature}")
                override = getattr(config.ranking, f"w_{feature}_{intent}", None)
                if override is None or override == default:
                    continue
                if weights is None:
                    weights = dict(base)
                weights[feature] = override
            if weights is not None:
                self.intent_models[intent] = LinearModel(weights)

    def __call__(self, vector: list[float], intent: str | None = None) -> float:
        return self.intent_models.get(intent, self.model).score(vector)


def rank_turn(rows: list[dict], score_fn) -> list[str]:
    """Full ordered pool for one turn. Ties break on parent_asin, as live."""
    scored = [
        (score_fn(row["features"], row.get("intent")), row["candidate_asin"])
        for row in rows
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [asin for _, asin in scored]


def replay_session(
    session_id: str,
    turns: dict[int, list[dict]],
    label: dict,
    score_fn,
) -> dict:
    """Replays the evaluator's per-turn hit test for one session."""
    override_turn = label["override_turn"]
    target = label["target"]
    override_applied = label["scenario_type"] != "intent_override"
    best_rank: int | None = None
    hit_turn: int | None = None

    for turn in sorted(turns):
        ordered = rank_turn(turns[turn], score_fn)[:TOP_K]
        if override_applied and target in ordered:
            best_rank = ordered.index(target) + 1
            hit_turn = turn
            break
        if turn == MAX_TURNS:
            break
        if not override_applied and turn + 1 == override_turn:
            override_applied = True

    return {
        "sample_id": label["sample_id"],
        "scenario_type": label["scenario_type"],
        "difficulty_bucket": label["difficulty_bucket"],
        "hit": hit_turn is not None,
        "first_hit_turn": hit_turn,
        "best_rank": best_rank,
        "reciprocal_rank": 0.0 if best_rank is None else 1.0 / best_rank,
    }


def offline_metrics(
    groups: dict[tuple[str, int], list[dict]],
    joined: dict[str, dict],
    score_fn,
) -> dict:
    """Replays every traced session and aggregates the official metrics."""
    by_session: dict[str, dict[int, list[dict]]] = defaultdict(dict)
    for (session_id, turn), rows in groups.items():
        by_session[session_id][turn] = rows

    sessions = [
        replay_session(session_id, turns, joined[session_id], score_fn)
        for session_id, turns in by_session.items()
        if session_id in joined
    ]
    sessions.sort(key=lambda s: s["sample_id"])
    if not sessions:
        return {"sample_count": 0, "hit_rate_at_10": 0.0, "mrr": 0.0, "sessions": []}

    hits = sum(int(s["hit"]) for s in sessions)
    return {
        "sample_count": len(sessions),
        "hit_rate_at_10": round(hits / len(sessions), 6),
        "mrr": round(sum(s["reciprocal_rank"] for s in sessions) / len(sessions), 6),
        "sessions": sessions,
    }


def target_in_pool(groups, joined) -> dict[str, bool]:
    """Sessions whose target never entered the candidate pool on any turn.

    These are recall failures, not ranking failures. They cannot contribute a
    positive example and must be reported separately rather than silently
    counted as ranking losses.
    """
    seen: dict[str, bool] = {}
    for (session_id, _turn), rows in groups.items():
        if session_id not in joined:
            continue
        target = joined[session_id]["target"]
        found = any(row["candidate_asin"] == target for row in rows)
        seen[session_id] = seen.get(session_id, False) or found
    return seen


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", required=True, help="features.jsonl from a traced run")
    parser.add_argument("--public-set", default="data/public_set.jsonl")
    parser.add_argument("--config", default="config/tuned.json")
    parser.add_argument("--expect-mrr", type=float, default=None,
                        help="fail if replayed MRR drifts from this by >0.001")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--against", default=None,
                        help="results.json from the traced run; checks every session")
    args = parser.parse_args()

    labels = load_labels(args.public_set)
    groups, order = load_trace(args.trace)
    joined = join_by_order(order, labels)
    scorer = ReplayScorer(Config.load(args.config))
    result = offline_metrics(groups, joined, scorer)

    pool = target_in_pool(groups, joined)
    missing = sorted(s for s, found in pool.items() if not found)

    print(json.dumps({
        "sample_count": result["sample_count"],
        "hit_rate_at_10": result["hit_rate_at_10"],
        "mrr": result["mrr"],
        "turn_groups": len(groups),
        "trace_rows": sum(len(rows) for rows in groups.values()),
        "target_never_in_pool": len(missing),
    }, indent=2))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    if args.against:
        recorded = {s["sample_id"]: s for s in json.loads(
            Path(args.against).read_text(encoding="utf-8"))["sessions"]}
        agree = disagree = 0
        examples = []
        for session in result["sessions"]:
            live = recorded.get(session["sample_id"])
            if live is None:
                continue
            if live["best_rank"] == session["best_rank"]:
                agree += 1
            else:
                disagree += 1
                if len(examples) < 5:
                    examples.append(
                        f"  {session['sample_id']}: live rank {live['best_rank']} "
                        f"vs replay {session['best_rank']}")
        print(f"\nper-session best_rank: {agree} agree, {disagree} disagree")
        for line in examples:
            print(line)

    if args.expect_mrr is not None:
        drift = abs(result["mrr"] - args.expect_mrr)
        status = "PASS" if drift <= 0.001 else "FAIL"
        print(f"\ngate: replayed {result['mrr']:.6f} vs expected {args.expect_mrr:.6f} "
              f"(drift {drift:.6f}) -> {status}")
        if status == "FAIL":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
