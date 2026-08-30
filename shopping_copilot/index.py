"""Route A -- lexical retrieval, one BM25 index per field.

Fields are indexed separately and never concatenated. Concatenation throws away
the single most useful thing about this catalog: a term matching in `title`
means something quite different from the same term matching in a 400-word
`description`. Keeping them apart lets the reranker weigh each field, and lets
`bm25_title` / `bm25_features` / `bm25_categories` enter the feature vector as
independent signals.

Implementation is a plain inverted index over `array('i')` postings -- stdlib
only, because official scoring may run offline with no third-party wheels.
"""

from __future__ import annotations

import math
from array import array
from collections import Counter
from dataclasses import dataclass

from .text import tokenize

FIELDS = ("title", "features", "categories", "description", "store")


@dataclass(slots=True)
class FieldParams:
    k1: float
    b: float


class BM25Field:
    """One field's inverted index.

    Postings are stored interleaved as (doc_id, term_frequency) pairs inside a
    single `array('i')` per term, which keeps 50k documents in tens of MB
    instead of the hundreds a dict-of-dicts would cost.
    """

    __slots__ = ("name", "params", "postings", "doc_len", "avgdl", "n_docs", "idf")

    def __init__(self, name: str, params: FieldParams) -> None:
        self.name = name
        self.params = params
        self.postings: dict[str, array] = {}
        self.doc_len: array = array("i")
        self.avgdl: float = 1.0
        self.n_docs: int = 0
        self.idf: dict[str, float] = {}

    def build(self, docs: list[tuple[str, ...]]) -> None:
        staging: dict[str, list[int]] = {}
        total_len = 0
        for doc_id, tokens in enumerate(docs):
            counts = Counter(tokens)
            length = len(tokens)
            total_len += length
            self.doc_len.append(length)
            for term, tf in counts.items():
                bucket = staging.get(term)
                if bucket is None:
                    bucket = staging[term] = []
                bucket.append(doc_id)
                bucket.append(min(tf, 32767))
        self.n_docs = len(docs)
        self.avgdl = (total_len / self.n_docs) if self.n_docs else 1.0
        for term, bucket in staging.items():
            self.postings[term] = array("i", bucket)
            df = len(bucket) // 2
            # Robertson/Sparck-Jones IDF with the +1 that keeps it non-negative.
            self.idf[term] = math.log(1.0 + (self.n_docs - df + 0.5) / (df + 0.5))

    def doc_frequency(self, term: str) -> int:
        bucket = self.postings.get(term)
        return 0 if bucket is None else len(bucket) // 2

    def search(
        self,
        query: list[tuple[str, float]],
        *,
        max_df_ratio: float = 1.0,
        limit: int | None = None,
    ) -> dict[int, float]:
        """Score every document containing at least one query term.

        `query` is (term, weight) so that later-turn constraints can outweigh
        earlier ones without rebuilding the index.
        """
        if not self.n_docs:
            return {}
        k1, b = self.params.k1, self.params.b
        avgdl = self.avgdl or 1.0
        df_cap = max_df_ratio * self.n_docs
        scores: dict[int, float] = {}
        doc_len = self.doc_len
        for term, weight in query:
            bucket = self.postings.get(term)
            if bucket is None:
                continue
            df = len(bucket) // 2
            # Very common terms carry almost no IDF but dominate the cost of the
            # scan, so they are dropped rather than scored.
            if df > df_cap:
                continue
            idf = self.idf[term] * weight
            for i in range(0, len(bucket), 2):
                doc_id = bucket[i]
                tf = bucket[i + 1]
                norm = 1.0 - b + b * (doc_len[doc_id] / avgdl)
                contribution = idf * (tf * (k1 + 1.0)) / (tf + k1 * norm)
                scores[doc_id] = scores.get(doc_id, 0.0) + contribution
        if limit is not None and len(scores) > limit:
            top = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
            return dict(top)
        return scores


class LexicalIndex:
    """The five per-field BM25 indexes, built once from the catalog."""

    def __init__(self, catalog, config) -> None:
        self.catalog = catalog
        self.config = config
        b_by_field = {
            "title": config.b_title,
            "features": config.b_features,
            "categories": config.b_categories,
            "description": config.b_description,
            "store": config.b_store,
        }
        self.weights = {
            "title": config.w_title,
            "features": config.w_features,
            "categories": config.w_categories,
            "description": config.w_description,
            "store": config.w_store,
        }
        self.fields: dict[str, BM25Field] = {}
        for field in FIELDS:
            index = BM25Field(field, FieldParams(config.k1, b_by_field[field]))
            # Tokenise directly rather than through `Catalog.tokens`. That cache
            # is sized for the ~200 candidates of a single turn; pushing 50,000
            # documents x 5 fields through it evicts every entry many times over
            # and buys nothing, since the index is the only consumer here.
            index.build([
                tuple(tokenize(catalog.field_text(product, field)))
                for product in catalog.products
            ])
            self.fields[field] = index

    def search(
        self, queries: dict[str, list[tuple[str, float]]], depth: int
    ) -> tuple[dict[int, float], dict[str, dict[int, float]]]:
        """Return (weighted lexical mixture, per-field raw scores).

        `queries` is keyed by field: the state routes the category phrase at the
        `categories` and `title` indexes and the quoted constraint spans at
        `features`, rather than firing one undifferentiated bag of words at
        everything. The mixture drives candidate selection; the per-field scores
        become reranker features.
        """
        per_field: dict[str, dict[int, float]] = {}
        mixture: dict[int, float] = {}
        for field, index in self.fields.items():
            query = queries.get(field) or []
            if not query:
                per_field[field] = {}
                continue
            scores = index.search(
                query[: self.config.max_query_terms],
                max_df_ratio=self.config.max_df_ratio,
                limit=depth,
            )
            per_field[field] = scores
            weight = self.weights[field]
            if not weight:
                continue
            for doc_id, score in scores.items():
                mixture[doc_id] = mixture.get(doc_id, 0.0) + weight * score
        return mixture, per_field

    def idf(self, field: str, term: str) -> float:
        return self.fields[field].idf.get(term, 0.0)

    def commonness(self, term: str) -> float:
        """Fraction of the catalog containing `term`, maximised across the
        three content-bearing fields (title/features/categories) -- used to
        soft-damp constraint-span query weight for near-universal
        boilerplate, independent of each field's own internal
        `max_df_ratio` hard cutoff (see `state.py`'s `term_commonness`
        parameter)."""
        n_docs = self.fields["title"].n_docs or 1
        return max(
            self.fields[field].doc_frequency(term) / n_docs
            for field in ("title", "features", "categories")
        )
