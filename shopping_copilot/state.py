"""Section 2 -- conversation state.

Two jobs.

**Parse.** The customer's turns are templated: a category phrase, then
constraint spans introduced by a small set of lead-ins. Bag-of-words over the
raw string is actively harmful -- "A *key* requirement is" pulls every listing
with the word "key" in its title to the top of the ranking. So each utterance is
split into (category phrase, constraint spans, control signals), and only the
content spans reach the retriever. Parsing is pattern-first with a boilerplate
stoplist behind it, so paraphrasing degrades the signal instead of breaking it.

**Accumulate.** Slots build up across turns; an intent override rewrites them.
15% of sessions are overrides and they cannot convert before the new intent
arrives, so getting the rewrite right is worth real effort.

On override the retracted constraint is *demoted, not deleted*. A constraint the
customer withdrew must never again be checked for satisfaction -- penalising a
candidate for violating a withdrawn preference is simply wrong -- but its terms
still say something about the region of the catalog being shopped, so they stay
in the query at a decayed weight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .profile import ShopperProfile
from .structured import SOFT_FIELDS, ConstraintExtractor, Constraints
from .text import bigrams, tokenize

# Phrases the simulator wraps around real content. Stripped before tokenising.
_SCAFFOLD = [
    r"i'?m looking for", r"i am looking for",
    r"a key requirement is", r"key requirement is",
    r"for that,? what matters is",
    r"actually,? ignore my earlier preference",
    r"actually,? ignore my earlier",
    r"what i need is", r"what i want is",
    r"i don'?t have an additional preference for",
    r"i don'?t have a preference for",
    r"please use your judgment", r"please use your judgement",
    r"those options are not quite right yet",
    r"ask me about one specific attribute",
    r"but i'?m still exploring", r"i'?m still exploring",
    r"please prioriti[sz]e the target requirements",
    r"i prefer a different style",
]
_SCAFFOLD_RE = re.compile("|".join(_SCAFFOLD), re.I)

# Residue of the templates above. These survive paraphrase and must not be
# allowed to act as content terms.
BOILERPLATE_TERMS = frozenset({
    "key", "requirement", "requirements", "matter", "matters", "need", "needs",
    "preference", "preferences", "additional", "judgment", "judgement",
    "option", "options", "quite", "right", "yet", "ask", "specific",
    "attribute", "attributes", "exploring", "explore", "ignore", "earlier",
    "actually", "still", "prefer", "prioritize", "prioritise", "target",
    "instead", "rather", "thing", "something", "anything", "sure", "maybe",
})

_OVERRIDE_MARKERS = (
    "actually", "instead", "rather", "no, i want", "changed my mind",
    "ignore my earlier", "on second thought", "scratch that", "forget",
)
_NO_PREFERENCE_RE = re.compile(
    r"don'?t have (?:an? )?(?:additional )?preference for (\w+)", re.I
)
_CATEGORY_RE = re.compile(r"looking for\s+(.+?)(?:[.,]|$)", re.I)
_LEAD_IN_RE = re.compile(
    r"(?:requirement is|matters is|need is|want is|preference:)\s*:?\s*(.+)$", re.I
)

# Within a single override utterance, text after one of these cues names the
# value being REPLACED, e.g. "white sneakers instead of black running shoes".
# Scanning that half for constraints would re-introduce the very thing the
# customer just overrode, so it is stripped before extraction runs.
_OVERRIDE_CUE_RE = re.compile(r"\binstead of\b|\brather than\b|\bnot\b", re.I)


def _kept_text(text: str) -> str:
    """The part of a constraint span describing the CURRENT value only."""
    match = _OVERRIDE_CUE_RE.search(text)
    return text[: match.start()].strip(" .,:;") if match else text


def _touched_fields(extractor: ConstraintExtractor, text: str) -> set[str]:
    """Which Constraints slots would extracting `text` alone fill in.

    Used to scope an override to the attribute(s) it actually re-states --
    a color change should not also wipe an already-disclosed budget -- and
    to decide which *existing* spans are about that same attribute and
    should therefore be demoted.
    """
    scratch = Constraints()
    extractor.update(scratch, text)
    return set(scratch.filled_slots())


@dataclass
class Utterance:
    """One parsed customer turn."""

    turn: int
    raw: str
    category_phrase: str = ""
    constraints: list[str] = field(default_factory=list)
    is_override: bool = False
    refused_attribute: str | None = None
    is_empty_feedback: bool = False


def parse_utterance(message: str, turn: int) -> Utterance:
    text = (message or "").strip()
    utterance = Utterance(turn=turn, raw=text)
    if not text:
        return utterance

    lowered = text.lower()
    utterance.is_override = any(marker in lowered for marker in _OVERRIDE_MARKERS)

    refusal = _NO_PREFERENCE_RE.search(text)
    if refusal:
        utterance.refused_attribute = refusal.group(1).lower()

    utterance.is_empty_feedback = "not quite right" in lowered

    category = _CATEGORY_RE.search(text)
    if category:
        phrase = _SCAFFOLD_RE.sub(" ", category.group(1))
        utterance.category_phrase = " ".join(phrase.split())

    # Content after a lead-in, minus anything already claimed as the category.
    body = text
    if category:
        body = text[category.end():]
    spans: list[str] = []
    if utterance.refused_attribute or utterance.is_empty_feedback:
        # A refusal or a nudge carries no product content; anything the regexes
        # find in it is template residue.
        return utterance
    lead_in = _LEAD_IN_RE.search(body)
    if lead_in:
        spans.extend(lead_in.group(1).split(";"))
    else:
        # No recognised lead-in: treat the remainder as content, but only after
        # the scaffolding is stripped. This is the paraphrase-tolerant path.
        remainder = _SCAFFOLD_RE.sub(" ", body)
        remainder = remainder.strip(" .,:;")
        if remainder and not utterance.refused_attribute and not utterance.is_empty_feedback:
            spans.append(remainder)

    for span in spans:
        cleaned = _SCAFFOLD_RE.sub(" ", span).strip(" .,:;\t\n")
        cleaned = " ".join(cleaned.split())
        if len(cleaned) > 1:
            utterance.constraints.append(cleaned)
    return utterance


@dataclass
class ConstraintSpan:
    """A disclosed constraint phrase and how much it should still count."""

    text: str
    turn: int
    weight: float = 1.0
    superseded: bool = False


class ShoppingState:
    """Per-session state. One instance per `reset`."""

    def __init__(self, session_id: str, profile: ShopperProfile, extractor: ConstraintExtractor) -> None:
        self.session_id = session_id
        self.profile = profile
        self.extractor = extractor
        self.turn = 0
        self.category_phrase: str = ""
        self.spans: list[ConstraintSpan] = []
        self.constraints = Constraints()
        self.asked_attributes: list[str] = []
        self.exhausted_attributes: set[str] = set()
        self.override_turn: int | None = None
        self.history: list[Utterance] = []
        self.last_ranking: list[str] = []

    # -- ingestion -------------------------------------------------------
    def observe(self, message: str, turn: int, *, override_decay: float = 0.25) -> Utterance:
        self.turn = turn
        utterance = parse_utterance(message, turn)
        self.history.append(utterance)

        if utterance.refused_attribute:
            self.exhausted_attributes.add(utterance.refused_attribute)

        if utterance.category_phrase and not self.category_phrase:
            self.category_phrase = utterance.category_phrase

        # An override retracts what it actually re-states, not everything.
        # Demote, don't delete: a withdrawn preference stops being checked but
        # its words still describe the neighbourhood of the catalog in play.
        texts_to_add = utterance.constraints
        if utterance.is_override and utterance.constraints:
            self.override_turn = turn
            kept_texts = [_kept_text(text) for text in utterance.constraints]

            touched: set[str] = set()
            for text in kept_texts:
                touched |= _touched_fields(self.extractor, text)
            touched &= SOFT_FIELDS   # never gender/category -- those are kept regardless

            if touched:
                # Surgical: only demote spans about the SAME attribute(s) the
                # customer just re-stated, e.g. only color, not price.
                for span in self.spans:
                    if not span.superseded and _touched_fields(self.extractor, span.text) & touched:
                        span.superseded = True
                        span.weight *= override_decay
                self.constraints.clear_soft(only=touched)
            else:
                # Couldn't tell which attribute changed (no recognisable slot
                # in the new text) -- fall back to the old blanket reset
                # rather than silently leaving stale state in place.
                for span in self.spans:
                    span.superseded = True
                    span.weight *= override_decay
                self.constraints.clear_soft()

            texts_to_add = kept_texts   # never re-extract the rejected half

        for text in texts_to_add:
            self.spans.append(ConstraintSpan(text=text, turn=turn))
            self.extractor.update(self.constraints, text)

        if self.category_phrase:
            self.extractor.update(self.constraints, self.category_phrase)
        return utterance

    def note_ask(self, attribute: str | None) -> None:
        if attribute:
            self.asked_attributes.append(attribute)

    # -- query construction ----------------------------------------------
    def query(self, *, recency_bonus: float = 0.15) -> dict[str, list[tuple[str, float]]]:
        """Per-field weighted term lists.

        Field routing is the point. The category phrase is the customer naming
        a taxonomy node, so it belongs against `categories` and `title`, not
        against 400 words of marketing copy. Constraint spans are quoted product
        copy, so they belong against `features` and `title`.
        """
        category_terms = [
            term for term in tokenize(self.category_phrase)
            if term not in BOILERPLATE_TERMS
        ]
        constraint_terms: dict[str, float] = {}
        for span in self.spans:
            # Later disclosures are more informative than the opening line.
            recency = 1.0 + recency_bonus * max(0, span.turn - 1)
            weight = span.weight * recency
            for term in tokenize(span.text):
                if term in BOILERPLATE_TERMS:
                    continue
                constraint_terms[term] = max(constraint_terms.get(term, 0.0), weight)

        category_weighted = [(term, 1.0) for term in dict.fromkeys(category_terms)]
        constraint_weighted = sorted(
            constraint_terms.items(), key=lambda kv: (-kv[1], kv[0])
        )

        def blend(cat_w: float, con_w: float) -> list[tuple[str, float]]:
            merged: dict[str, float] = {}
            for term, weight in category_weighted:
                merged[term] = merged.get(term, 0.0) + cat_w * weight
            for term, weight in constraint_weighted:
                merged[term] = merged.get(term, 0.0) + con_w * weight
            return sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))

        return {
            "title": blend(1.00, 0.85),
            "features": blend(0.35, 1.00),
            "categories": blend(1.00, 0.25),
            "description": blend(0.30, 0.70),
            "store": blend(0.10, 0.60),
        }

    def active_terms(self) -> set[str]:
        """Terms from constraints still in force, for phrase/coverage features."""
        out: set[str] = set()
        for span in self.spans:
            if span.superseded:
                continue
            out.update(t for t in tokenize(span.text) if t not in BOILERPLATE_TERMS)
        out.update(t for t in tokenize(self.category_phrase) if t not in BOILERPLATE_TERMS)
        return out

    def active_bigrams(self) -> set[str]:
        """Ordered bigrams of live constraint text.

        The customer quotes spans of the target's own copy, so an ordered
        bigram match is far more discriminative than the unigrams that produced
        the candidate pool.
        """
        out: set[str] = set()
        for span in self.spans:
            weight_ok = not span.superseded
            if not weight_ok:
                continue
            tokens = [t for t in tokenize(span.text) if t not in BOILERPLATE_TERMS]
            out.update(bigrams(tokens))
        cat_tokens = [t for t in tokenize(self.category_phrase) if t not in BOILERPLATE_TERMS]
        out.update(bigrams(cat_tokens))
        return out

    @property
    def disclosed_count(self) -> int:
        return sum(1 for span in self.spans if not span.superseded)
