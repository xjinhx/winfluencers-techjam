"""Shared text normalisation.

Every module tokenises through here so that index-time and query-time
vocabularies stay identical. The conservative plural stripper matters more than
it looks: the simulator names categories in the plural ("Necklaces") while
titles use the singular ("Necklace").
"""

from __future__ import annotations

import re
from typing import Iterable, Iterator, Mapping

TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

# Deliberately small. Aggressive stoplists delete constraint words ("no", "not",
# "for") that carry meaning in a shopping request.
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "its", "me", "my", "of", "on", "or", "please",
    "some", "that", "the", "this", "to", "want", "with", "would", "you",
    "looking", "im", "id", "ive", "am", "was", "were", "been", "have", "has",
    "do", "does", "did", "so", "just", "really", "very", "can", "could",
})

# Words that appear in almost every clothing listing and carry no selectivity.
LOW_VALUE = frozenset({"product", "item", "quality", "great", "new", "amp"})


def flatten(value: object) -> str:
    """Render a catalog field (scalar, list, or dict) as a flat string."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items() if item not in (None, ""))
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value if item not in (None, ""))
    return str(value)


# Canonical singulars derived from the catalog at load time; see
# `install_plural_exceptions`. Empty until installed, and while empty `stem`
# behaves exactly as it did before this map existed -- so any consumer that
# never builds a Catalog (the unit suite, a bare import) keeps the old vocabulary.
_CANONICAL: dict[str, str] = {}

# A candidate singular has to be attested this often before it displaces the
# default rule. The floor is what keeps brand noise and typos out: 'dickie'
# occurs 5 times, so 'dickies' is left alone.
PLURAL_MIN_SUPPORT = 20

# `-es` is only a plural suffix after a sibilant. Trimming two characters
# anywhere else invents a different word: 'capes' would become 'cap' (709
# listings, a hat, not a cape). Gating on the sibilant is what makes the
# aggressive trim safe to attempt at all.
SIBILANT_ENDINGS = ("ch", "sh", "s", "x", "z")

# English plurals no suffix rule reaches. Only applied when both forms are
# actually attested; the catalog picks which surface survives.
IRREGULAR_PLURALS = {
    "men": "man", "women": "woman", "children": "child", "feet": "foot",
    "teeth": "tooth", "mice": "mouse", "geese": "goose", "people": "person",
}


def _singular_candidates(token: str) -> list[str]:
    """Every form `token` could be the plural of.

    Deliberately generates competing hypotheses rather than committing to one:
    'scarves' proposes both 'scarf' and 'scarfe', 'hoodies' proposes both
    'hoodie' and 'hoody'. Which one is real is a question about this catalog,
    and `install_plural_exceptions` answers it by counting.
    """
    if len(token) <= 3 or not token.endswith("s"):
        return []
    candidates: list[str] = []
    if len(token) > 4 and token.endswith("ies"):
        candidates.append(token[:-3] + "ie")
        candidates.append(token[:-3] + "y")
    if len(token) > 4 and token.endswith("ves"):
        # scarves -> scarf, knives -> knife. But 'gloves' and 'sleeves' are not
        # f-alternations at all, so both hypotheses stay unattested there and
        # the plain trim below wins on the count.
        candidates.append(token[:-3] + "f")
        candidates.append(token[:-3] + "fe")
    if len(token) > 4 and token.endswith("es") and token[:-2].endswith(SIBILANT_ENDINGS):
        candidates.append(token[:-2])
    if not token.endswith("ss"):
        # 'brass' must not propose 'bras' -- which is attested 966 times and is
        # an entirely different garment.
        candidates.append(token[:-1])
    return candidates


def install_plural_exceptions(counts: Mapping[str, int]) -> None:
    """Derive canonical singulars from raw (unstemmed) surface-form counts.

    Suffix rules alone cannot do this. `-ies -> -y` is right for 'bodies' and
    wrong for 'hoodies'; `-es` must be trimmed in 'watches' and must not be in
    'capes'; `-ves` is an f-alternation in 'scarves' and not in 'gloves'. Each
    pair is indistinguishable by shape and obvious by frequency, so the rule
    here is to propose every reading and let the catalog choose the one it
    actually attests.

    That is also why this generalises: a word nobody has looked at yet gets the
    same treatment as 'hoodies', with no list to extend.

    The result depends only on the counts, never on iteration order, so the map
    is deterministic.
    """
    resolved: dict[str, str] = {}
    for token in counts:
        best: str | None = None
        best_count = 0
        for candidate in _singular_candidates(token):
            count = counts.get(candidate, 0)
            if count > best_count:
                best, best_count = candidate, count
        if best is not None and best != token and best_count >= PLURAL_MIN_SUPPORT:
            resolved[token] = best
        elif (
            best_count == 0
            and counts.get(token, 0) >= PLURAL_MIN_SUPPORT
            and _singular_candidates(token)
        ):
            # A singular that merely ends in s: 'lens', 'canvas', 'atlas'. No
            # reading of it as a plural is attested, so pin it to itself before
            # the default rule strips it to 'len' and splits it from 'lenses'.
            # Only when nothing at all is attested -- a real plural whose
            # singular is merely rare still gets stripped and merged.
            resolved[token] = token

    for plural, singular in IRREGULAR_PLURALS.items():
        plural_count, singular_count = counts.get(plural, 0), counts.get(singular, 0)
        if min(plural_count, singular_count) == 0:
            continue
        if max(plural_count, singular_count) < PLURAL_MIN_SUPPORT:
            continue
        # Whichever spelling the catalog prefers becomes the index term; the
        # direction is arbitrary as long as both sides agree on it.
        if plural_count >= singular_count:
            resolved[singular] = plural
            resolved.pop(plural, None)
        else:
            resolved[plural] = singular
            resolved.pop(singular, None)

    # Collapse chains so every member of a family lands on the same term. If
    # 'lenses' resolved to 'lens' and 'lens' resolved onward, the two would
    # split again -- the exact failure this whole map exists to prevent.
    canonical: dict[str, str] = {}
    for token, target in resolved.items():
        seen = {token}
        while target in resolved and target not in seen:
            seen.add(target)
            target = resolved[target]
        # Identity entries are kept, not discarded: they are how a singular
        # ending in s short-circuits the default rule.
        canonical[token] = target
    global _CANONICAL
    _CANONICAL = canonical


def reset_plural_exceptions() -> None:
    """Drop the installed map, restoring the pre-catalog stemming behaviour.

    Exists for test isolation: `stem` is imported directly in places, and a map
    left installed by one test would leak into the next."""
    global _CANONICAL
    _CANONICAL = {}


def stem(token: str) -> str:
    """Conservative plural stripper. Not linguistics -- just enough to make
    'necklaces' and 'necklace' the same index term."""
    canonical = _CANONICAL.get(token)
    if canonical is not None:
        return canonical
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("sses"):
        return token[:-2]
    # Unambiguous sibilants, for words the catalog never attested and so could
    # not vote on: nothing English ends in -che/-she/-xe/-ze whose plural could
    # be confused with a bare -s. ('-ses' is left out on purpose: it is the one
    # sibilant that is genuinely ambiguous -- lenses/lens against horses/horse.)
    if len(token) > 4 and token.endswith(("ches", "shes", "xes", "zes")):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def tokenize(text: str, *, keep_stopwords: bool = False) -> list[str]:
    out: list[str] = []
    for raw in TOKEN_RE.findall(text):
        token = raw.lower()
        if len(token) < 2:
            continue
        if not keep_stopwords and (token in STOPWORDS or token in LOW_VALUE):
            continue
        out.append(stem(token))
    return out


def bigrams(tokens: list[str]) -> Iterator[str]:
    for i in range(len(tokens) - 1):
        yield tokens[i] + "\x00" + tokens[i + 1]


def char_ngrams(text: str, n: int = 4) -> Iterator[str]:
    """Character n-grams over a space-collapsed string; the fuzzy-match backbone
    of the semantic-lite dense route."""
    cleaned = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "
    cleaned = re.sub(r"\s+", " ", cleaned)
    for i in range(len(cleaned) - n + 1):
        gram = cleaned[i:i + n]
        if gram.strip():
            yield gram


def unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))
