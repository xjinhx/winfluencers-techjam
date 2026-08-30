"""Section 4b and 6 -- the scoring model and diversity.

The model is a weighted linear sum over the section-4a feature vector, with the
weights living in `config.py`. That is the shippable choice at 200 sessions:
Grinsztajn et al. (arXiv 2207.08815) and the surrounding tabular benchmarks find
that feature engineering and dataset characteristics -- not model class -- set
the ceiling, and that deep models are held back precisely by the data-size and
feature-quality constraints this problem has. The features are the contribution;
the model is a way of adding them up.

`ScoringModel` is the seam for the LambdaRank upgrade. A fitted GBDT drops in
behind the same interface without the retriever or the feature function
changing. Two things gate that upgrade, and neither is met by wishing:

  1. Sessions must be logged first (section 7) -- negatives are mined from this
     retriever's own output, so the retriever must be frozen before fitting.
  2. It has to beat tuned-linear on session-grouped held-out folds. If it does
     not, ship linear and say so in the writeup.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .catalog import Product
from .config import ConstraintConfig, PriorConfig, RankingConfig
from .features import FEATURE_NAMES, ScoringContext, extract


class ScoringModel(Protocol):
    """Maps a feature vector to a scalar. The one seam a GBDT would replace."""

    def score(self, vector: list[float]) -> float:
        ...


class LinearModel:
    """Weighted sum. Weights are named, not positional, so reordering the
    feature vector cannot silently mis-assign them."""

    __slots__ = ("weights", "_vector")

    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights
        self._vector = [weights.get(name, 0.0) for name in FEATURE_NAMES]

    def score(self, vector: list[float]) -> float:
        return sum(w * v for w, v in zip(self._vector, vector) if w)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.weights, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "LinearModel":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))


def build_linear_weights(
    ranking: RankingConfig, priors: PriorConfig, constraints: ConstraintConfig
) -> dict[str, float]:
    """Assemble the named weight map from config.

    `*_unknown` weights are ordinary columns here, same as `*_satisfied` /
    `*_violated` -- folded in so a learned model sees the penalty as a
    feature instead of a hand-tuned constant applied outside the model.
    """
    weights: dict[str, float] = {
        "fused": ranking.w_fused,
        "bm25_title": ranking.w_bm25_title,
        "bm25_features": ranking.w_bm25_features,
        "bm25_categories": ranking.w_bm25_categories,
        "bm25_description": 0.0,
        "bm25_store": 0.0,
        "dense": ranking.w_dense,
        "phrase_title": ranking.w_phrase_title,
        "phrase_features": ranking.w_phrase_features,
        "phrase_categories": ranking.w_phrase_categories,
        "coverage": ranking.w_coverage,
        "popularity": priors.w_log_rating_number,
        "quality": priors.w_average_rating,
        "has_price": priors.w_has_price,
        "has_description": priors.w_has_description,
        "n_features_norm": priors.w_n_features,
        "profile_affinity": ranking.w_profile_affinity,
        "category_focus": ranking.w_category_focus,
    }
    for dimension in ("gender", "brand", "category", "price", "material", "color"):
        weights[f"{dimension}_satisfied"] = getattr(constraints, f"{dimension}_satisfied")
        weights[f"{dimension}_violated"] = getattr(constraints, f"{dimension}_violated")
        weights[f"{dimension}_unknown"] = getattr(constraints, f"{dimension}_unknown")
    return weights


class Ranker:
    """Turns a candidate pool into an ordered list of `parent_asin`."""

    def __init__(
        self,
        ranking: RankingConfig,
        priors: PriorConfig,
        constraints: ConstraintConfig,
        model: ScoringModel | None = None,
    ) -> None:
        self.config = ranking
        self.priors = priors
        self.constraint_config = constraints
        self.model = model or LinearModel(
            build_linear_weights(ranking, priors, constraints)
        )
        # One model per routed intent, built only when the caller did not supply
        # its own scorer -- a GBDT dropped into `model` keeps full control of the
        # vector rather than having `fused` rewritten underneath it.
        self.intent_models: dict[str, ScoringModel] = {}
        if model is None:
            for intent in ("buying", "browsing", "uncertain"):
                override = getattr(ranking, f"w_fused_{intent}", None)
                if override is None or override == ranking.w_fused:
                    continue
                weights = build_linear_weights(ranking, priors, constraints)
                weights["fused"] = override
                self.intent_models[intent] = LinearModel(weights)

    def score_candidate(self, product: Product, ctx: ScoringContext) -> tuple[float, list[float]]:
        vector = extract(product, ctx)
        score = self.intent_models.get(ctx.intent, self.model).score(vector)
        return score, vector

    def rank(
        self,
        candidates: list[Product],
        ctx: ScoringContext,
        top_k: int,
        *,
        diversify: bool = False,
    ) -> list[tuple[Product, float]]:
        """The *whole* pool, ordered. The caller slices the top_k it will
        return; the clarification policy needs the rest to estimate how much
        uncertainty is actually left."""
        scored = []
        for product in candidates:
            score, _ = self.score_candidate(product, ctx)
            scored.append((product, score))
        # Ties break on parent_asin so two runs of the same input produce
        # byte-identical output.
        scored.sort(key=lambda item: (-item[1], item[0].parent_asin))
        if diversify and self.config.enable_mmr:
            scored = self._mmr(scored, ctx, top_k)
        return scored

    def _mmr(
        self,
        scored: list[tuple[Product, float]],
        ctx: ScoringContext,
        top_k: int,
    ) -> list[tuple[Product, float]]:
        """Maximal marginal relevance over positions 2..10, browsing only.

        Position 1 is never touched. Every diversity swap that demotes the true
        target costs MRR directly, and MRR is 0.30 of the score.

        Worth measuring rather than assuming: only 4.8% of catalog rows fall in
        a multi-row near-duplicate cluster, 90% of those clusters are pairs, and
        the largest is six. The top 10 does not naturally fill with duplicates
        here, which makes MMR less necessary than in a typical storefront.
        """
        head_size = max(top_k * 3, top_k)
        pool, tail = scored[:head_size], scored[head_size:]
        if len(pool) <= self.config.mmr_start_position:
            return scored
        selected = pool[: self.config.mmr_start_position - 1]
        remaining = pool[self.config.mmr_start_position - 1:]
        lam = self.config.mmr_lambda
        catalog = ctx.catalog
        chosen_tokens = [
            frozenset(catalog.tokens(product, "title")) for product, _ in selected
        ]
        while remaining and len(selected) < top_k:
            best_index, best_value = 0, float("-inf")
            for index, (product, score) in enumerate(remaining):
                tokens = frozenset(catalog.tokens(product, "title"))
                similarity = 0.0
                for other in chosen_tokens:
                    union = len(tokens | other)
                    if union:
                        similarity = max(similarity, len(tokens & other) / union)
                value = lam * score - (1.0 - lam) * similarity
                if value > best_value or (
                    value == best_value and product.parent_asin < remaining[best_index][0].parent_asin
                ):
                    best_index, best_value = index, value
            product, score = remaining.pop(best_index)
            selected.append((product, score))
            chosen_tokens.append(frozenset(catalog.tokens(product, "title")))
        return selected + remaining + tail
