"""Section 8 -- the ablation table.

One row per component, each disabling exactly one thing against the full
system, reported as HR@10 / MRR / MTTC / TechnicalScore. This is what makes the
engineering legible: a component that cannot be shown to move a number is a
component that should be cut, not defended.

Run:  python -m tools.ablate                 (full 200 sessions)
      python -m tools.ablate --subset 80     (faster, stratified)
      python -m tools.ablate --output docs/ablations.json --markdown docs/ablations.md
"""

from __future__ import annotations

import argparse
import copy

from shopping_copilot.config import Config
from tools.evalkit import Bench, summarise, write_json


def _no_dense(config: Config) -> Config:
    config.retrieval.fusion_alpha = 1.0  # all lexical; dense contributes nothing
    config.ranking.w_dense = 0.0
    return config


def _no_phrase(config: Config) -> Config:
    config.ranking.w_phrase_title = 0.0
    config.ranking.w_phrase_features = 0.0
    config.ranking.w_phrase_categories = 0.0
    return config


def _no_priors(config: Config) -> Config:
    priors = config.priors
    priors.w_log_rating_number = 0.0
    priors.w_has_price = 0.0
    priors.w_n_features = 0.0
    priors.w_average_rating = 0.0
    priors.w_has_description = 0.0
    return config


def _no_constraints(config: Config) -> Config:
    for dimension in ("gender", "brand", "category", "price", "material", "color"):
        for outcome in ("satisfied", "violated", "unknown"):
            setattr(config.constraints, f"{dimension}_{outcome}", 0.0)
    return config


def _no_clarification(config: Config) -> Config:
    # Never ask: `ask_turn_budget = 1` trips the turn-budget override on turn 1.
    config.dialogue.ask_turn_budget = 1
    return config


def _no_coverage(config: Config) -> Config:
    config.ranking.w_coverage = 0.0
    config.ranking.w_category_focus = 0.0
    return config


def _no_profile(config: Config) -> Config:
    config.ranking.w_profile_affinity = 0.0
    return config


def _single_field_lexical(config: Config) -> Config:
    """Collapse per-field BM25 to one undifferentiated bag of fields."""
    config.retrieval.w_title = 1.0
    config.retrieval.w_features = 1.0
    config.retrieval.w_categories = 1.0
    config.retrieval.w_description = 1.0
    config.retrieval.w_store = 1.0
    config.ranking.w_bm25_title = 0.0
    config.ranking.w_bm25_features = 0.0
    config.ranking.w_bm25_categories = 0.0
    return config


def _shallow_candidates(config: Config) -> Config:
    """Retrieve 20 instead of 200 -- the depth claim, tested."""
    config.retrieval.per_field_depth = 20
    config.retrieval.candidate_depth = 20
    config.retrieval.rerank_depth = 20
    return config


def _with_mmr(config: Config) -> Config:
    """MMR is off by default; this row is what justifies that."""
    config.ranking.enable_mmr = True
    return config


def _no_recommend_hold(config: Config) -> Config:
    """Both recommendation-hold gates off -- answer on every turn.

    Inert under the dataclass defaults, so this row only says anything when
    ablating a tuned config that actually sets them.
    """
    config.dialogue.recommend_min_spans = 0
    config.dialogue.min_recommend_confidence = 0.0
    return config


def _no_span_conjunction(config: Config) -> Config:
    """`span_all` -- does the candidate match EVERY disclosed span, not some.

    Note this only removes the *feature*. The conjunctive candidate injection
    that puts such products in the pool is unconditional in `agent.py` with no
    config flag (deliberate, see CLAUDE.md), so it cannot be ablated from here.
    """
    config.ranking.w_span_all = 0.0
    config.ranking.w_span_coverage = 0.0
    return config


def _no_commonness_penalty(config: Config) -> Config:
    """Constraint-span terms stop being damped by catalog commonness."""
    config.retrieval.constraint_commonness_penalty = 0.0
    return config


def _no_low_coverage_penalties(config: Config) -> Config:
    """Title/popularity confidence unbacked by query coverage stops being cut."""
    config.ranking.w_title_low_coverage = 0.0
    config.ranking.w_popularity_low_coverage = 0.0
    return config


def _reask_disclosed(config: Config) -> Config:
    """Restore asking about attributes the customer already disclosed."""
    config.dialogue.disclosed_ask_decay = 1.0
    return config


ABLATIONS = [
    ("full system", None),
    ("- dense route (Route B)", _no_dense),
    ("- phrase / bigram evidence", _no_phrase),
    ("- popularity priors", _no_priors),
    ("- constraint scoring (Route C)", _no_constraints),
    ("- clarification policy", _no_clarification),
    ("- coverage + category focus", _no_coverage),
    ("- profile personalisation", _no_profile),
    ("- per-field weighting", _single_field_lexical),
    ("candidate depth 200 -> 20", _shallow_candidates),
    ("+ MMR diversity (browsing)", _with_mmr),
    # Rows below are inert under the dataclass defaults and only measure
    # anything when --config points at a tuned configuration that sets them.
    ("- recommendation hold (both gates)", _no_recommend_hold),
    ("- span conjunction (span_all)", _no_span_conjunction),
    ("- constraint commonness penalty", _no_commonness_penalty),
    ("- low-coverage penalties", _no_low_coverage_penalties),
    ("+ re-ask disclosed attributes", _reask_disclosed),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Component ablation table")
    parser.add_argument("--subset", type=int, default=None)
    parser.add_argument("--output", default="docs/ablations.json")
    parser.add_argument("--markdown", default="docs/ablations.md")
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Base config to ablate FROM. Default: the dataclass defaults, which "
            "is what the historic 0.7641 table was measured against. Pass "
            "config/tuned.json to ablate the shipped build instead -- several "
            "rows are inert against the defaults and only measure something "
            "there."
        ),
    )
    args = parser.parse_args()

    bench = Bench()
    samples = bench.subset(args.subset)
    base = Config.load(args.config) if args.config else Config()
    print(f"ablating over {len(samples)} sessions from {args.config or 'dataclass defaults'}\n")

    rows = []
    baseline_score = None
    for name, mutate in ABLATIONS:
        config = copy.deepcopy(base)
        if mutate is not None:
            config = mutate(config)
        result = summarise(bench.score(copy.deepcopy(config), samples))
        if baseline_score is None:
            baseline_score = result["technical_score"]
        result["delta"] = round(result["technical_score"] - baseline_score, 6)
        result["component"] = name
        rows.append(result)
        print(
            f"{name:34} score={result['technical_score']:.4f} "
            f"HR@10={result['hit_rate_at_10']:.3f} MRR={result['mrr']:.4f} "
            f"MTTC={result['mttc']:.2f} delta={result['delta']:+.4f}"
        )

    write_json(args.output, {"sample_count": len(samples), "rows": rows})

    lines = [
        "| component | HR@10 | MRR | MTTC | TechnicalScore | delta |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['component']} | {row['hit_rate_at_10']:.3f} | {row['mrr']:.4f} "
            f"| {row['mttc']:.2f} | {row['technical_score']:.4f} | {row['delta']:+.4f} |"
        )
    from pathlib import Path

    Path(args.markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {args.output} and {args.markdown}")


if __name__ == "__main__":
    main()
