"""Section 1 -- intent routing.

Classifies each turn as BUYING / BROWSING / UNCERTAIN. The label selects the
retrieval weight profile and whether diversity is allowed to touch the tail of
the list; it does not decide whether the target is retrievable.

PSCon (arXiv 2502.13881) observes that e-commerce conversational-recommendation
work is usually anchor-based -- conversations simulated from predefined intent
slots, entities, and attributes. That is exactly this simulator's construction,
which is why slot-based routing is the right abstraction here and free-form NLU
is not.

Four features, one linear decision, no model call. It runs every turn and must
be deterministic; an LLM here would buy nothing and cost reproducibility.
"""

from __future__ import annotations

from dataclasses import dataclass

BUYING = "buying"
BROWSING = "browsing"
UNCERTAIN = "uncertain"

_BROWSING_MARKERS = (
    "still exploring", "not sure", "just looking", "browsing", "ideas",
    "suggestions", "recommend", "something", "anything", "exploring",
    "open to", "help me find", "what do you",
)
_BUYING_MARKERS = (
    "requirement", "must", "need", "specifically", "exactly", "only",
    "has to", "looking for a", "i want", "buy",
)


@dataclass
class IntentDecision:
    intent: str
    confidence: float
    signals: dict[str, float]


def route(state, config) -> IntentDecision:
    """Score the turn on four bounded features and threshold the mixture."""
    utterance = state.history[-1] if state.history else None
    raw = (utterance.raw if utterance else "").lower()

    # 1. Constraint density -- how much the customer has actually committed to.
    disclosed = state.disclosed_count
    constraint_density = min(1.0, disclosed / 3.0)

    # 2. Linguistic markers, as a signed contrast.
    browsing_hits = sum(1 for marker in _BROWSING_MARKERS if marker in raw)
    buying_hits = sum(1 for marker in _BUYING_MARKERS if marker in raw)
    marker_score = 0.5 + 0.25 * (buying_hits - browsing_hits)
    marker_score = max(0.0, min(1.0, marker_score))

    # 3. Slot specificity -- structured slots resolved, not just words said.
    slots = state.constraints.filled_slots()
    slot_specificity = min(1.0, len(slots) / 3.0)

    # 4. Profile alignment -- a shopper with a long purchase history and firm
    #    preference tags behaves more like a buyer than a browser.
    profile = state.profile
    alignment = min(1.0, (profile.purchase_count / 6.0) * 0.5 + (len(profile.tags) / 4.0) * 0.5)

    score = (
        0.40 * constraint_density
        + 0.25 * marker_score
        + 0.25 * slot_specificity
        + 0.10 * alignment
    )

    if score >= config.intent_buying_threshold:
        intent = BUYING
    elif score <= config.intent_browsing_threshold:
        intent = BROWSING
    else:
        intent = UNCERTAIN

    return IntentDecision(
        intent=intent,
        confidence=score,
        signals={
            "constraint_density": constraint_density,
            "linguistic_markers": marker_score,
            "slot_specificity": slot_specificity,
            "profile_alignment": alignment,
        },
    )
