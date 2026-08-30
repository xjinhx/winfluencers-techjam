"""The Agent -- orchestration only.

Per-turn flow:

    parse -> route intent -> retrieve (A + B, fused) -> rerank -> clarify

Two decisions from the architecture doc's open list are settled here.

**D1 (retrieval boundary).** The agent owns retrieval and hands the ranker a
candidate pool. The ranker never calls the index. That keeps the reranker a pure
function of (candidates, context), which is what makes offline replay in
section 7 possible at all -- and it puts the popularity prior in the reranker,
where it can be ablated, rather than in candidate selection, where it would
silently delete the ~5% of targets that sit below the popular tail.

**D2 (recommend on ask-turns).** Yes. Nothing in the response schema makes
`ask_attribute` and `recommendations` mutually exclusive, first-hit turn drives
MTTC, and a turn that returns nothing is a discarded chance at the hit. The
agent asks *and* answers on the same turn.

No LLM call anywhere on the turn path. The system is fully offline and
deterministic; `usage` is reported as zero because no model is invoked.
"""

from __future__ import annotations

import json
from pathlib import Path

from .catalog import Catalog
from .clarify import ClarificationPolicy
from .config import Config, DEFAULT_CONFIG
from .dense import build_dense_route
from .features import ScoringContext
from .fusion import convex_combine, theoretical_minmax, top_n
from .index import LexicalIndex
from .intent import BROWSING, route
from .profile import ShopperProfile
from .ranking import Ranker
from .state import ShoppingState
from .structured import ConstraintExtractor


class Agent:
    """Conversational shopping agent. Implements the TechJam contract."""

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        config: Config | None = None,
    ) -> None:
        self.config = config or DEFAULT_CONFIG
        self.catalog = Catalog(catalog_path)
        self.lexical = LexicalIndex(self.catalog, self.config.retrieval)
        self.dense = build_dense_route(self.catalog, self.config.retrieval)
        self.extractor = ConstraintExtractor(self.catalog)
        self.ranker = Ranker(
            self.config.ranking, self.config.priors, self.config.constraints
        )
        self.clarifier = ClarificationPolicy(self.config.dialogue)
        self.sessions: dict[str, ShoppingState] = {}
        # Survivor lists for the conjunctive injection, keyed by the exact
        # active_spans() tuple. Catalog-dependent only, so entries stay valid
        # across sessions; cleared on apply_config to bound memory over long
        # tuning sweeps.
        self._span_cache: dict[tuple[str, ...], list[int]] = {}
        self._trace = (
            Path(self.config.trace_path).open("a", encoding="utf-8")
            if self.config.trace_path
            else None
        )

    def apply_config(self, config: Config) -> None:
        """Swap in a new configuration without rebuilding the indexes.

        Every retrieval parameter except the dense n-gram size is applied at
        query time, so a tuning trial or an ablation costs one evaluation pass
        rather than a 20-second index rebuild. Changing `dense_ngram` or
        `enable_dense` from off to on does require a new Agent.
        """
        self.config = config
        for field, index in self.lexical.fields.items():
            index.params.k1 = config.retrieval.k1
            index.params.b = getattr(config.retrieval, f"b_{field}")
        self.lexical.config = config.retrieval
        self.lexical.weights = {
            field: getattr(config.retrieval, f"w_{field}")
            for field in self.lexical.fields
        }
        self.ranker = Ranker(config.ranking, config.priors, config.constraints)
        self.clarifier = ClarificationPolicy(config.dialogue)
        self.sessions.clear()
        self._span_cache.clear()

    def _span_survivors(self, spans: tuple[str, ...]) -> list[int]:
        """Every product whose search_blob contains every span, verbatim.

        A full scan is ~0.2s over 50k products; the span set only changes on
        turns that disclose something new (refusals and nudges leave it
        untouched), so the cache collapses a session's ten turns to two or
        three scans.
        """
        cached = self._span_cache.get(spans)
        if cached is None:
            cached = [
                p.idx for p in self.catalog.products
                if all(s in p.search_blob for s in spans)
            ]
            self._span_cache[spans] = cached
        return cached

    # -- contract --------------------------------------------------------
    def reset(self, session_id: str, user_profile: dict) -> None:
        # The extractor is shared, not rebuilt: its brand and category
        # vocabularies are derived from the whole catalog and are session-
        # independent. Only the accumulated slots live on the state.
        try:
            profile = ShopperProfile.parse(user_profile)
        except Exception:  # pragma: no cover - last-resort guard, mirrors respond()
            profile = ShopperProfile.parse(None)
        self.sessions[session_id] = ShoppingState(
            session_id=session_id,
            profile=profile,
            extractor=self.extractor,
        )

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        state = self.sessions.get(session_id)
        if state is None:
            # The harness treats an exception as a miss, so degrade instead.
            self.reset(session_id, {})
            state = self.sessions[session_id]
        try:
            return self._respond(state, user_message, turn, top_k)
        except Exception:  # pragma: no cover - last-resort guard
            return {
                "message": "Here are the closest matches I found.",
                "ask_attribute": None,
                "recommendations": [
                    {"parent_asin": asin} for asin in state.last_ranking[:top_k]
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }

    # -- turn pipeline ---------------------------------------------------
    def _respond(self, state: ShoppingState, user_message: str, turn: int, top_k: int) -> dict:
        retrieval = self.config.retrieval

        state.observe(user_message, turn, override_decay=self.config.dialogue.override_decay)
        decision = route(state, self.config.dialogue)

        queries = state.query(
            recency_bonus=self.config.dialogue.recency_bonus,
            term_commonness=self.lexical.commonness,
            commonness_penalty_strength=retrieval.constraint_commonness_penalty,
            max_df_ratio=retrieval.max_df_ratio,
        )
        lexical_mixture, per_field = self.lexical.search(queries, retrieval.per_field_depth)

        # The dense route reads the surface text, not the term list: character
        # n-grams need the original spelling to be worth anything.
        dense_text = " ".join(
            [state.category_phrase] + [span.text for span in state.spans if not span.superseded]
        ).strip()
        dense_scores = (
            self.dense.search(dense_text, retrieval.dense_depth)
            if retrieval.enable_dense else {}
        )

        fused = convex_combine(lexical_mixture, dense_scores, retrieval.fusion_alpha)
        candidate_ids = top_n(fused, retrieval.candidate_depth)
        if not candidate_ids:
            return self._empty_response(state, top_k)

        # Conjunctive injection (PRD_conjunctive_injection.md). The gate tests
        # len(matched) -- selectivity of the conjunction itself -- not the
        # post-dedup count, so a conjunction too common to trust is skipped
        # outright rather than truncated by some other order.
        injected: list[int] = []
        spans = state.active_spans()
        if len(spans) >= retrieval.injection_min_spans:
            matched = self._span_survivors(spans)
            if 0 < len(matched) <= retrieval.injection_max_survivors:
                existing = set(candidate_ids)
                injected = [i for i in matched if i not in existing]
        if injected:
            candidate_ids = candidate_ids + injected

        ctx = ScoringContext(
            catalog=self.catalog,
            constraints=state.constraints,
            profile=state.profile,
            # `if i in fused`: an injected survivor that neither BM25 nor dense
            # retrieved has no fused entry and scores 0.0 on that feature, the
            # same way an absent per-field or dense score already does.
            fused=theoretical_minmax({i: fused[i] for i in candidate_ids if i in fused}),
            per_field={
                field: theoretical_minmax(
                    {i: scores[i] for i in candidate_ids if i in scores}
                )
                for field, scores in per_field.items()
            },
            dense=theoretical_minmax(
                {i: dense_scores[i] for i in candidate_ids if i in dense_scores}
            ),
            query_terms=state.active_terms(),
            query_bigrams=state.active_bigrams(),
            category_terms=set(_tokens(state.category_phrase)),
            constraint_spans=state.active_spans(),
            turn=turn,
            intent=decision.intent,
        )

        # Injected survivors sit past the rerank_depth cut by construction
        # (appended after a full fused slice), so re-append whatever it drops
        # -- the reranked set grows by the survivors, never loses fused pool.
        rerank_ids = candidate_ids[: retrieval.rerank_depth]
        if injected:
            kept = set(rerank_ids)
            rerank_ids = rerank_ids + [i for i in injected if i not in kept]
        candidates = [self.catalog.products[i] for i in rerank_ids]
        ranked = self.ranker.rank(
            candidates, ctx, top_k, diversify=(decision.intent == BROWSING)
        )
        ordered = [product.parent_asin for product, _ in ranked[:top_k]]
        state.last_ranking = ordered

        # The clarifier sees the whole ordered pool, not just the ten returned.
        # Its first gate asks whether the candidate space is still large enough
        # to be worth narrowing, and a list truncated to ten always looks
        # settled -- which silently disables clarification altogether.
        clarification = self.clarifier.decide(
            state, [p for p, _ in ranked], [s for _, s in ranked], turn
        )
        state.note_ask(clarification.attribute)

        if self._trace is not None:
            self._log(state, ctx, candidates, turn)

        recommendations = (
            [{"parent_asin": asin} for asin in ordered]
            if (clarification.attribute is None or self.config.dialogue.recommend_on_ask_turns)
            else []
        )
        return {
            "message": clarification.message,
            "ask_attribute": clarification.attribute,
            "recommendations": recommendations,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }

    def _empty_response(self, state: ShoppingState, top_k: int) -> dict:
        """No lexical or dense match at all -- ask rather than guess blindly."""
        return {
            "message": "Could you tell me a bit more about what you are after?",
            "ask_attribute": "category",
            "recommendations": [{"parent_asin": a} for a in state.last_ranking[:top_k]],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }

    def _log(self, state, ctx, candidates, turn: int) -> None:
        """Section 7: append feature rows for the offline training loop.

        Labels are unavailable at run time, so rows are written label-free and
        joined to ground truth offline. Negatives are therefore mined from this
        retriever's own output -- which is exactly why the retriever must be
        frozen before anything is fitted on them.
        """
        from .features import extract

        for product in candidates:
            self._trace.write(json.dumps({
                "session_id": state.session_id,
                "turn": turn,
                "intent": ctx.intent,
                "candidate_asin": product.parent_asin,
                "features": extract(product, ctx),
            }) + "\n")

    def close(self) -> None:
        if self._trace is not None:
            self._trace.close()
            self._trace = None


def _tokens(text: str) -> list[str]:
    from .state import BOILERPLATE_TERMS
    from .text import tokenize

    return [t for t in tokenize(text) if t not in BOILERPLATE_TERMS]
