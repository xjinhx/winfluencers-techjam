"""Score fusion -- convex combination, not RRF.

Bruch et al. (arXiv 2210.11934) report RRF to be sensitive to its parameters and
to generalise poorly out of domain, while convex combination beats it both in-
and out-of-domain, is largely agnostic to the choice of score normalisation, and
is sample-efficient -- one parameter, tunable from a small set of examples.

With 200 public sessions to tune on and 800 private ones to generalise to, "one
parameter, tuned on a small sample" is the deciding property.

The paper studied fusing *two* retrievers and explicitly leaves three-or-more to
future work. This system fuses two (lexical mixture, dense) and treats the
per-field lexical scores as reranker features rather than as extra fusion
inputs, which keeps it inside the regime the evidence covers.
"""

from __future__ import annotations


def theoretical_minmax(scores: dict[int, float]) -> dict[int, float]:
    """TM2C2 normalisation.

    The theoretical minimum of a BM25 or cosine score is 0, so only the maximum
    is estimated from the sample. Using the observed minimum instead would make
    the bottom of every result list identically zero and destroy the tail
    ordering that MRR cares about.
    """
    if not scores:
        return {}
    highest = max(scores.values())
    if highest <= 0.0:
        return {doc_id: 0.0 for doc_id in scores}
    return {doc_id: value / highest for doc_id, value in scores.items()}


def convex_combine(
    lexical: dict[int, float],
    dense: dict[int, float],
    alpha: float,
) -> dict[int, float]:
    """alpha * lexical_norm + (1 - alpha) * dense_norm, over the union.

    Union, not intersection: a document only the dense route found still gets
    its dense contribution, scored against a lexical component of zero.
    """
    lex = theoretical_minmax(lexical)
    den = theoretical_minmax(dense)
    fused: dict[int, float] = {}
    for doc_id, value in lex.items():
        fused[doc_id] = alpha * value
    for doc_id, value in den.items():
        fused[doc_id] = fused.get(doc_id, 0.0) + (1.0 - alpha) * value
    return fused


def top_n(scores: dict[int, float], n: int) -> list[int]:
    """Deterministic top-n: ties break on document id, never on dict order."""
    return [
        doc_id
        for doc_id, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    ]
