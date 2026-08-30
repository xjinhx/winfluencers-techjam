"""Section 4a -- feature extraction.

One fixed-width vector per candidate, produced by a pure function of
(candidate, context). Purity is the requirement that matters: the same function
has to run inside the live agent and offline over logged sessions, or the model
trained in section 7 is trained on features the agent does not actually emit.

Feature families:

  retrieval   per-field BM25, dense similarity, the fused score
  phrase      ordered-bigram overlap and term coverage -- the discriminative
              layer, because the customer quotes spans of the target's own copy
  prior       popularity and listing-completeness signals (see section 8 on why
              popularity is correct here and wrong in production)
  constraint  three-way satisfied/violated/unknown, one-hot-minus-one encoded so
              a tree can split on missingness natively
  context     profile affinity and category focus

Constraint dimensions are encoded as two binary columns rather than one signed
column so that `satisfied` and `violated` can carry independent weights --
violating a stated gender is far more costly than satisfying it is valuable.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import Catalog, Product
from .profile import ShopperProfile
from .structured import SATISFIED, UNKNOWN, VIOLATED, Constraints, evaluate_all

CONSTRAINT_DIMENSIONS = ("gender", "brand", "category", "price", "material", "color")

FEATURE_NAMES: tuple[str, ...] = (
    "fused",
    "bm25_title",
    "bm25_features",
    "bm25_categories",
    "bm25_description",
    "bm25_store",
    "dense",
    "phrase_title",
    "phrase_features",
    "phrase_categories",
    "span_coverage",
    "span_all",
    "coverage",
    "title_low_coverage",
    "popularity_low_coverage",
    "popularity",
    "quality",
    "has_price",
    "has_description",
    "n_features_norm",
    *[f"{dim}_{outcome}" for dim in CONSTRAINT_DIMENSIONS for outcome in ("satisfied", "violated")],
    *[f"{dim}_unknown" for dim in CONSTRAINT_DIMENSIONS],
    "profile_affinity",
    "category_focus",
)

FEATURE_INDEX = {name: i for i, name in enumerate(FEATURE_NAMES)}


@dataclass
class ScoringContext:
    """Everything the feature function is allowed to see.

    Passed explicitly rather than read off the agent, so that an offline replay
    can reconstruct it exactly from a log line.
    """

    catalog: Catalog
    constraints: Constraints
    profile: ShopperProfile
    fused: dict[int, float]
    per_field: dict[str, dict[int, float]]
    dense: dict[int, float]
    query_terms: set[str]
    query_bigrams: set[str]
    category_terms: set[str]
    constraint_spans: tuple[str, ...] = ()
    turn: int = 1
    intent: str = "buying"


def _overlap(query: set[str], document: frozenset[str]) -> float:
    if not query:
        return 0.0
    return len(query & document) / len(query)


def extract(product: Product, ctx: ScoringContext) -> list[float]:
    """The feature vector for one candidate. Pure: no I/O, no mutation."""
    doc_id = product.idx
    catalog = ctx.catalog

    title_tokens = frozenset(catalog.tokens(product, "title"))
    feature_tokens = frozenset(catalog.tokens(product, "features"))
    category_tokens = frozenset(catalog.tokens(product, "categories"))
    searchable = title_tokens | feature_tokens | category_tokens

    coverage = _overlap(ctx.query_terms, searchable)
    category_focus = _overlap(ctx.category_terms, title_tokens | category_tokens)
    bm25_title = ctx.per_field.get("title", {}).get(doc_id, 0.0)

    phrase_title = _overlap(ctx.query_bigrams, catalog.bigram_set(product, "title"))
    phrase_features = _overlap(ctx.query_bigrams, catalog.bigram_set(product, "features"))
    phrase_categories = _overlap(ctx.query_bigrams, catalog.bigram_set(product, "categories"))

    # Constraint spans matched IN FULL. `phrase_*` above measures bigram
    # overlap, which cannot tell "3 of 4 spans matched" from "all 4" -- and the
    # conjunction is what discriminates: on public_0092 the four disclosed spans
    # narrow 284 candidates to 2, while each one alone matches 13-41% of them.
    spans = ctx.constraint_spans
    if spans:
        matched = sum(1 for s in spans if s in product.search_blob)
        span_coverage = matched / len(spans)
        span_all = 1.0 if matched == len(spans) else 0.0
    else:
        span_coverage = 0.0
        span_all = 0.0

    outcomes = evaluate_all(product, ctx.constraints)

    vector = [
        ctx.fused.get(doc_id, 0.0),
        bm25_title,
        ctx.per_field.get("features", {}).get(doc_id, 0.0),
        ctx.per_field.get("categories", {}).get(doc_id, 0.0),
        ctx.per_field.get("description", {}).get(doc_id, 0.0),
        ctx.per_field.get("store", {}).get(doc_id, 0.0),
        ctx.dense.get(doc_id, 0.0),
        phrase_title,
        phrase_features,
        phrase_categories,
        span_coverage,
        span_all,
        coverage,
        bm25_title * (1.0 - coverage),
        product.popularity * (1.0 - coverage),
        product.popularity,
        product.quality,
        1.0 if product.has_price else 0.0,
        1.0 if product.has_description else 0.0,
        # 7.72 features for targets vs 5.02 for the catalog; squashed so a
        # listing with forty bullets does not run away with it.
        min(1.0, product.n_features / 12.0),
    ]
    for dimension in CONSTRAINT_DIMENSIONS:
        outcome = outcomes[dimension]
        vector.append(1.0 if outcome == SATISFIED else 0.0)
        vector.append(1.0 if outcome == VIOLATED else 0.0)
    for dimension in CONSTRAINT_DIMENSIONS:
        vector.append(1.0 if outcomes[dimension] == UNKNOWN else 0.0)
    vector.append(ctx.profile.affinity(product.title, product.features_text))
    vector.append(category_focus)
    return vector


def as_row(product: Product, ctx: ScoringContext, label: int) -> dict:
    """One training row for the offline LambdaRank loop (section 7)."""
    return {
        "candidate_asin": product.parent_asin,
        "turn": ctx.turn,
        "intent": ctx.intent,
        "label": label,
        "features": dict(zip(FEATURE_NAMES, extract(product, ctx))),
    }
