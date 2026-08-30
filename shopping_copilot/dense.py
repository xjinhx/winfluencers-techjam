"""Route B -- the semantic route.

Honest statement of what this is: **not** a neural dense retriever. Official
scoring may run with network access disabled and no third-party wheels, so the
shipped default is a character-n-gram similarity index over `title +
categories` -- stdlib only, deterministic, no model download.

What it buys is the part of "dense" that this catalog actually needs: tolerance
to morphology and spelling drift ("camisole" / "cami", "sterling silver" /
"silver sterling", "Skechers" / "skecher"). What it does not buy is true
semantic generalisation -- "something elegant for a dinner date" will not reach
a listing that never says "elegant".

`EmbeddingRoute` is the seam for a real dense route. Precompute vectors offline
to a local `.npy`-free JSON/binary asset, point the config at it, and the fusion
layer downstream does not change. That path is deliberately left unpopulated
rather than half-wired to a service that may not exist at scoring time.
"""

from __future__ import annotations

import math
from array import array
from collections import Counter
from typing import Protocol

from .catalog import Catalog
from .text import char_ngrams


class DenseRoute(Protocol):
    """Anything that can score the catalog against a query string."""

    def search(self, text: str, depth: int) -> dict[int, float]:
        ...


class CharNgramRoute:
    """Cosine similarity over TF-IDF-weighted character n-grams.

    Uses the same inverted-index trick as the lexical route so a query costs a
    scan of the matching posting lists rather than 50k vector comparisons.
    """

    __slots__ = ("n", "postings", "norms", "idf", "n_docs", "max_df_ratio")

    def __init__(self, catalog: Catalog, n: int = 4, max_df_ratio: float = 0.25) -> None:
        self.n = n
        self.max_df_ratio = max_df_ratio
        self.n_docs = len(catalog.products)
        staging: dict[str, list[int]] = {}
        raw_counts: list[Counter] = []
        for doc_id, product in enumerate(catalog.products):
            # title + categories only. Feature copy is long and boilerplate-
            # heavy; adding it drags every listing toward the corpus mean.
            surface = product.title + " " + product.categories_text
            counts = Counter(char_ngrams(surface, n))
            raw_counts.append(counts)
            for gram in counts:
                bucket = staging.get(gram)
                if bucket is None:
                    bucket = staging[gram] = []
                bucket.append(doc_id)

        self.idf: dict[str, float] = {}
        for gram, docs in staging.items():
            self.idf[gram] = math.log(1.0 + self.n_docs / (1.0 + len(docs)))

        self.postings: dict[str, array] = {}
        for gram, docs in staging.items():
            weight_index = array("i")
            for doc_id in docs:
                weight_index.append(doc_id)
            self.postings[gram] = weight_index

        # L2 norms of the TF-IDF document vectors, for a true cosine.
        self.norms = array("f", [0.0]) * self.n_docs
        for doc_id, counts in enumerate(raw_counts):
            total = 0.0
            for gram, tf in counts.items():
                weight = (1.0 + math.log(tf)) * self.idf[gram]
                total += weight * weight
            self.norms[doc_id] = math.sqrt(total) or 1.0
        # The per-document counters are only needed for the norms above.
        raw_counts.clear()

    def search(self, text: str, depth: int) -> dict[int, float]:
        if not text.strip():
            return {}
        query_counts = Counter(char_ngrams(text, self.n))
        if not query_counts:
            return {}
        df_cap = self.max_df_ratio * self.n_docs
        query_norm = 0.0
        contributions: list[tuple[str, float]] = []
        for gram, tf in query_counts.items():
            postings = self.postings.get(gram)
            if postings is None:
                continue
            idf = self.idf[gram]
            weight = (1.0 + math.log(tf)) * idf
            query_norm += weight * weight
            if len(postings) > df_cap:
                continue
            contributions.append((gram, weight))
        query_norm = math.sqrt(query_norm) or 1.0

        scores: dict[int, float] = {}
        for gram, weight in contributions:
            postings = self.postings[gram]
            idf = self.idf[gram]
            for doc_id in postings:
                # Approximation: the document side of the dot product uses idf
                # alone, i.e. it treats every n-gram as occurring once in the
                # document. Over `title + categories` -- a few dozen words --
                # that holds for the large majority of 4-grams, and storing
                # per-posting term frequencies would double the index for a
                # route that contributes ~0.003 to the score. The norms below
                # are the exact TF-IDF norms, so this is a true cosine only in
                # the tf=1 case and a close approximation otherwise.
                scores[doc_id] = scores.get(doc_id, 0.0) + weight * idf

        for doc_id in scores:
            scores[doc_id] /= (self.norms[doc_id] * query_norm)

        if len(scores) > depth:
            top = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:depth]
            return dict(top)
        return scores


class NullRoute:
    """Disabled dense route. Fusion collapses to pure lexical."""

    def search(self, text: str, depth: int) -> dict[int, float]:  # noqa: ARG002
        return {}


def build_dense_route(catalog: Catalog, config) -> DenseRoute:
    if not config.enable_dense:
        return NullRoute()
    return CharNgramRoute(catalog, n=config.dense_ngram)
