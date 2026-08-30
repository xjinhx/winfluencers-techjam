"""Every tunable number in the system, in one place.

Two rules for this file:
  1. No module hard-codes a weight. If it is tunable, it lives here.
  2. The whole config round-trips through JSON, so `tools/tune.py` can search it
     and a submission can ship the winning values as an asset.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class RetrievalConfig:
    """Route A (lexical) + Route B (dense) + fusion. See architecture doc S3."""

    # BM25. b is low on `title` because titles are short and near-uniform in
    # length, so length normalisation mostly adds noise there.
    k1: float = 1.2
    b_title: float = 0.45
    b_features: float = 0.75
    b_categories: float = 0.30
    b_description: float = 0.75
    b_store: float = 0.20

    # Per-field weights in the lexical mixture. `features` leads because the
    # simulator quotes feature bullets back at us near-verbatim.
    w_title: float = 1.00
    w_features: float = 1.25
    w_categories: float = 0.90
    w_description: float = 0.45
    w_store: float = 0.35

    # Terms above this document-frequency ratio are dropped from the query.
    # Their IDF is near zero and their posting lists are the expensive ones.
    max_df_ratio: float = 0.35
    max_query_terms: int = 48

    # Soft down-weighting of constraint-span query terms by catalog
    # commonness (state.py's query()), separate from BM25Field's own hard
    # max_df_ratio cutoff above. Ramps weight down continuously as a term's
    # document-frequency ratio approaches max_df_ratio, rather than
    # all-or-nothing at the cutoff -- a few medium-frequency boilerplate
    # terms (e.g. "manmade sole") compounding together currently each earn
    # full query weight right up to that point. 0.0 = disabled (default,
    # preserves prior behaviour exactly).
    constraint_commonness_penalty: float = 0.0

    # Convex combination, not RRF (Bruch et al. 2210.11934). One parameter,
    # tunable on 200 sessions.
    fusion_alpha: float = 0.78  # weight on lexical; (1 - alpha) on dense

    # Candidate depth. Recall lost here cannot be recovered by the reranker.
    per_field_depth: int = 220
    candidate_depth: int = 200
    rerank_depth: int = 200

    enable_dense: bool = True
    dense_ngram: int = 4
    dense_depth: int = 150


@dataclass
class PriorConfig:
    """Popularity / listing-quality priors. See architecture doc S8.

    These are a *soft* prior. Nothing here may exclude a candidate: ~5% of
    targets sit below the popular tail and one has a single review.
    """

    w_log_rating_number: float = 0.150
    w_has_price: float = 0.040
    w_n_features: float = 0.030
    w_average_rating: float = 0.015
    w_has_description: float = 0.010

    # Bayesian shrinkage for average_rating.
    rating_prior_weight: float = 50.0


@dataclass
class ConstraintConfig:
    """Three-way constraint scoring. `unknown` is a mild penalty, never a filter
    -- the catalog is too sparse for hard elimination (architecture doc P2)."""

    gender_satisfied: float = 0.055
    gender_violated: float = -0.230
    gender_unknown: float = -0.010

    brand_satisfied: float = 0.180
    brand_violated: float = -0.060
    brand_unknown: float = 0.0

    category_satisfied: float = 0.120
    category_violated: float = -0.070
    category_unknown: float = -0.005

    price_satisfied: float = 0.030
    price_violated: float = -0.055
    price_unknown: float = -0.005

    material_satisfied: float = 0.070
    material_violated: float = -0.020
    material_unknown: float = 0.0

    color_satisfied: float = 0.045
    color_violated: float = -0.015
    color_unknown: float = 0.0


@dataclass
class RankingConfig:
    """Reranker weights (architecture doc S4b) plus diversity (S6)."""

    w_fused: float = 1.000

    # Fusion weight per routed intent; `None` falls back to `w_fused`.
    #
    # `fused` is a convex combination of the same lexical and dense signals that
    # also enter the vector separately, so it counts text evidence twice. On
    # constraint-bearing turns that double count drowns the structured features:
    # across the rank 3-5 band it carries 68% of the score gap while every
    # constraint feature carries 0.0 (see CLAUDE.md). A browsing turn
    # has no disclosed constraints to drown and `fused` is the best evidence
    # available there, which is why this is conditional rather than a global cut.
    w_fused_buying: float | None = None
    w_fused_browsing: float | None = None
    w_fused_uncertain: float | None = None

    w_bm25_title: float = 0.260
    w_bm25_title_buying: float | None = None
    w_bm25_title_browsing: float | None = None
    w_bm25_title_uncertain: float | None = None
    w_bm25_features: float = 0.300
    w_bm25_categories: float = 0.220
    w_dense: float = 0.180

    # Phrase evidence: the customer message reuses whole spans of the target's
    # own copy, so ordered-bigram overlap separates the true item from
    # unigram-similar neighbours.
    w_phrase_title: float = 0.320
    w_phrase_features: float = 0.520
    w_phrase_categories: float = 0.240
    w_coverage: float = 0.300

    # Penalise lexical confidence that is not backed by whole-query coverage.
    # These interaction weights default to zero so existing configurations are
    # byte-for-byte equivalent until an evaluated setting opts in.
    w_title_low_coverage: float = 0.0
    w_popularity_low_coverage: float = 0.0

    w_profile_affinity: float = 0.030
    w_category_focus: float = 0.070

    # Same per-intent override pattern as w_fused_*, `None` falls back to the
    # shared default above. Both are "soft, no-constraint-needed" signals --
    # the same rationale that justifies dropping `fused` for buying/uncertain
    # (real stated facts are better evidence) argues these should matter MORE
    # for browsing specifically, where no such facts exist to lean on instead.
    # Untested until given their own knob; a blanket global raise was already
    # shown to do effectively nothing, which is a different question from
    # "does it help browsing specifically while leaving buying/uncertain alone".
    w_profile_affinity_buying: float | None = None
    w_profile_affinity_browsing: float | None = None
    w_profile_affinity_uncertain: float | None = None
    w_category_focus_buying: float | None = None
    w_category_focus_browsing: float | None = None
    w_category_focus_uncertain: float | None = None

    # Diversity: MMR on positions 2-10, browsing only. Position 1 is never
    # diversified -- every demotion of the true target costs MRR directly.
    enable_mmr: bool = False
    mmr_lambda: float = 0.85
    mmr_start_position: int = 2


@dataclass
class DialogueConfig:
    """Intent routing (S1), state (S2), clarification (S5)."""

    intent_buying_threshold: float = 0.65
    intent_browsing_threshold: float = 0.35

    # EAR gate (Lei et al. 2020): ask only while asking is still worth a turn.
    ask_min_candidates: int = 12
    ask_max_confidence: float = 0.82
    ask_turn_budget: int = 8          # at turn >= this, stop asking and answer
    ask_min_info_gain: float = 0.05

    # D2 in the architecture doc: nothing in the schema makes `ask_attribute`
    # and `recommendations` exclusive, and every silent turn is a discarded
    # chance at a hit that MTTC would have rewarded.
    recommend_on_ask_turns: bool = True

    # Expected disclosure per attribute -- *measured*, not guessed. Produced by
    # `python -m tools.measure_attribute_yield` over the 200 public sessions:
    # normalised mean characters of new product text a question elicits.
    #
    # The zeros are the finding. Asking about brand, budget, or category
    # discloses nothing at all (yield rate 0.000 across all 200 sessions), so
    # entropy over those catalog fields is a mirage: they would split the
    # candidate pool beautifully if only the customer would answer them.
    # Information gain has to be multiplied by the probability of an answer,
    # which is what this dict supplies.
    attribute_prior: dict = field(default_factory=lambda: {
        "feature": 1.000,
        "other": 0.949,
        "material": 0.556,
        "color": 0.295,
        "style": 0.250,
        "size": 0.147,
        "use_case": 0.039,
        "brand": 0.000,
        "budget": 0.000,
        "category": 0.000,
    })

    # Floor under the measured prior, so an attribute that scored zero on the
    # public set can still be asked once everything else is exhausted. Insurance
    # against the private set's disclosure policy differing at the margins.
    attribute_prior_floor: float = 0.05

    # Re-asking an attribute that has not been refused can still disclose more
    # (the customer reveals at most two spans per turn), but with diminishing
    # returns. Each prior ask multiplies the expected yield by this.
    repeat_ask_decay: float = 0.45

    # How much an overridden constraint span's weight decays once superseded
    # (state.observe's override_decay). It stays in the query, just quieter.
    override_decay: float = 0.25

    # Per-turn boost applied to later constraint disclosures over earlier ones
    # (state.query's recency_bonus) -- a customer's most recent statement is
    # more informative than their opening line.
    recency_bonus: float = 0.15


@dataclass
class Config:
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    priors: PriorConfig = field(default_factory=PriorConfig)
    constraints: ConstraintConfig = field(default_factory=ConstraintConfig)
    ranking: RankingConfig = field(default_factory=RankingConfig)
    dialogue: DialogueConfig = field(default_factory=DialogueConfig)

    # Offline training hook (architecture doc S7). When set, every scored
    # candidate is appended as a feature row for later LambdaRank fitting.
    trace_path: str | None = None
    cache_dir: str | None = ".cache"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "Config":
        kwargs = {}
        for f in fields(cls):
            value = payload.get(f.name)
            if value is None:
                continue
            if f.name in {"trace_path", "cache_dir"}:
                kwargs[f.name] = value
            else:
                kwargs[f.name] = _build(f.default_factory(), value)
        return cls(**kwargs)

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")


def _build(prototype, payload: dict):
    """Overlay a JSON dict onto a dataclass instance, ignoring unknown keys."""
    for key, value in payload.items():
        if hasattr(prototype, key):
            setattr(prototype, key, value)
    return prototype


DEFAULT_CONFIG = Config()
