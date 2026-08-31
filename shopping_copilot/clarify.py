"""Section 5 -- clarification policy.

The gate is EAR's (Lei et al. 2020): ask only when the candidate space is small
enough to be worth narrowing, the question still carries information, and the
recommender is not already confident its top results will be accepted.

What is deliberately *not* lifted: SCPR (arXiv 2007.00194), UNICORN
(arXiv 2105.09710) and that line are RL policies over knowledge graphs, and they
assume every item carries a clean attribute set. This catalog does not -- Color
is present on 4.9% of rows, Size on 1.9%, Material on 4.1%. So information gain
is computed only over `categories`, `Department` and title-derived tokens, where
coverage is real. Entropy-based attribute selection appears in that literature
mostly as the baseline the RL methods beat, so it is expected to be shakier here
than it looks there.

Two hard overrides sit on top of the gate:

  * At turn >= the budget, stop asking and answer. Running out of turns costs
    Efficiency and can cost the session outright.
  * Never re-ask an attribute the customer has already declined. A boundary
    customer refuses once; asking again wastes a second turn on a session that
    only has ten.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .catalog import Product

# The contract's allowed values. `ask_attribute` must be one of these or null.
ALLOWED_ATTRIBUTES = (
    "category", "material", "color", "size", "style", "brand",
    "budget", "feature", "use_case", "other",
)

# What each attribute is estimated from, and the question to ask for it.
_QUESTION = {
    "category": "What kind of item are you after, specifically?",
    "material": "Do you have a material preference?",
    "color": "Any colour you are set on?",
    "size": "What size or fit are you looking for?",
    "style": "What style or cut works best for you?",
    "brand": "Is there a brand you prefer?",
    "budget": "Roughly what budget are you working with?",
    "feature": "Is there a particular feature that matters most?",
    "use_case": "What will you mainly be using it for?",
    "other": "Anything else that matters for this one?",
}


@dataclass
class ClarificationDecision:
    attribute: str | None
    message: str
    info_gain: float = 0.0
    reason: str = ""


def _entropy(counts: dict[str, int], total: int) -> float:
    if total <= 0:
        return 0.0
    entropy = 0.0
    for value in counts.values():
        if value <= 0:
            continue
        p = value / total
        entropy -= p * math.log2(p)
    return entropy


def _partition_entropy(candidates: list[Product], key) -> float:
    """Entropy of the candidate pool under one partitioning function.

    High entropy means the attribute genuinely splits the pool, so an answer
    removes real uncertainty. Candidates where the field is missing land in a
    single `None` bucket, which correctly makes a sparse field look uninformative
    rather than magically discriminative.
    """
    counts: dict[str, int] = {}
    for product in candidates:
        value = key(product)
        bucket = str(value) if value else "__missing__"
        counts[bucket] = counts.get(bucket, 0) + 1
    return _entropy(counts, len(candidates))


def nqc(scores: list[float]) -> float:
    """NQC (Normalized Query Commitment; Shtok et al. 2009): how much the
    ranker has "committed" to separating good candidates from bad ones, with
    no ground truth available -- standard deviation of the top-10 scores,
    normalised by the top score. High spread means the ranker clearly pulled
    some candidates ahead of others (trustworthy); scores bunched close
    together means it didn't (worth a question).

    Module-level and single-source deliberately. Two callers need this number
    -- the ask gate below and the recommendation gate in `agent.py` -- and a
    second copy is the failure this repo has already paid for once, when
    `tools/offline_eval.py`'s `ReplayScorer` kept a stale unknown-penalty loop
    after `features.py` moved on. `tools/offline_eval.py` imports this one.
    """
    if len(scores) < 2:
        return 1.0
    top = scores[0]
    if top <= 0:
        return 0.0
    window = scores[:10]
    mean = sum(window) / len(window)
    variance = sum((s - mean) ** 2 for s in window) / len(window)
    std = variance ** 0.5
    return max(0.0, min(1.0, std / abs(top)))


class ClarificationPolicy:
    def __init__(self, config) -> None:
        self.config = config

    def decide(
        self,
        state,
        candidates: list[Product],
        scores: list[float],
        turn: int,
    ) -> ClarificationDecision:
        # -- turn-budget override -----------------------------------------
        # Hard stop. Running out of turns costs Efficiency and can cost the
        # session, so late turns always answer.
        if turn >= self.config.ask_turn_budget:
            return ClarificationDecision(None, self._closing_message(), reason="turn_budget")

        last = state.history[-1] if state.history else None
        invited = bool(last and last.is_empty_feedback)

        # The customer explicitly said the options are wrong and asked for a
        # question. Staying silent here burns the rest of the session: no new
        # information can arrive, so the ranking cannot change, so every
        # remaining turn repeats the same wrong answer.
        if not invited:
            # -- EAR gate 1: is the space still large enough to be worth narrowing?
            if len(candidates) < self.config.ask_min_candidates:
                return ClarificationDecision(None, self._closing_message(), reason="pool_small")

            # -- EAR gate 3: are we already confident enough to just answer?
            if self._confidence(scores) >= self.config.ask_max_confidence:
                return ClarificationDecision(None, self._closing_message(), reason="confident")

        # -- EAR gate 2: is a question still worth a turn?
        attribute, gain = self._best_attribute(state, candidates)
        if attribute is None:
            return ClarificationDecision(None, self._closing_message(), reason="exhausted")
        if not invited and gain < self.config.ask_min_info_gain:
            return ClarificationDecision(None, self._closing_message(), reason="no_gain")

        return ClarificationDecision(
            attribute=attribute,
            message=_QUESTION.get(attribute, _QUESTION["other"]),
            info_gain=gain,
            reason="ear_gate",
        )

    def confidence(self, scores: list[float]) -> float:
        """Public accessor for the NQC signal.

        The agent gates recommendation-withholding on the same number the EAR
        gate uses to decide whether to ask, so the two decisions cannot
        disagree about whether the ranker has committed."""
        return self._confidence(scores)

    def _confidence(self, scores: list[float]) -> float:
        """Delegates to the module-level `nqc` -- see its docstring for why
        there is exactly one definition."""
        return nqc(scores)

    def _best_attribute(self, state, candidates: list[Product]) -> tuple[str | None, float]:
        """Expected information gain = P(answered) x (uncertainty it removes).

        Both halves are needed. Entropy alone picks `category` and `brand`,
        which split the candidate pool superbly and which this customer never
        answers -- a perfectly reasoned question that wastes one of ten turns.
        `attribute_prior` supplies the measured answer side (see
        `tools.measure_attribute_yield`); entropy over the fields with real
        coverage supplies the uncertainty side.
        """
        pool = candidates[:120]
        if not pool:
            return None, 0.0

        # Entropy is only estimated over fields with real coverage. Sparse
        # fields land every candidate in one "__missing__" bucket, which
        # correctly reports them as uninformative rather than discriminative.
        measurable = {
            "category": _partition_entropy(pool, lambda p: p.category_path[-1] if p.category_path else None),
            "brand": _partition_entropy(pool, lambda p: p.brand_key or None),
            "size": _partition_entropy(pool, lambda p: p.effective_gender),
            "budget": _partition_entropy(pool, lambda p: _price_bucket(p)),
        }
        max_entropy = math.log2(len(pool)) or 1.0
        prior = self.config.attribute_prior
        floor = self.config.attribute_prior_floor
        decay = self.config.repeat_ask_decay

        best_attribute, best_gain = None, 0.0
        for attribute in ALLOWED_ATTRIBUTES:
            if attribute in state.exhausted_attributes:
                continue
            answer_probability = max(prior.get(attribute, 0.2), floor)
            normalised = measurable.get(attribute)
            # Attributes with no measurable catalog partition are assumed
            # averagely informative *if* answered; the prior carries the rest.
            informativeness = (normalised / max_entropy) if normalised is not None else 0.6
            # The customer discloses at most two spans per turn, so a repeat
            # ask still has value -- just less of it.
            repeats = state.asked_attributes.count(attribute)
            gain = answer_probability * informativeness * (decay ** repeats)
            if gain > best_gain:
                best_attribute, best_gain = attribute, gain
        return best_attribute, best_gain

    def _closing_message(self) -> str:
        return "Here are the closest matches I found."


def _price_bucket(product: Product) -> str | None:
    if product.price is None:
        return None
    for edge in (15, 25, 40, 60, 100, 200):
        if product.price <= edge:
            return f"<={edge}"
    return ">200"
