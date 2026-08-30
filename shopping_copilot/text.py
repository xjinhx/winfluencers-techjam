"""Shared text normalisation.

Every module tokenises through here so that index-time and query-time
vocabularies stay identical. The conservative plural stripper matters more than
it looks: the simulator names categories in the plural ("Necklaces") while
titles use the singular ("Necklace").
"""

from __future__ import annotations

import re
from typing import Iterable, Iterator

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


def stem(token: str) -> str:
    """Conservative plural stripper. Not linguistics -- just enough to make
    'necklaces' and 'necklace' the same index term."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("sses"):
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
