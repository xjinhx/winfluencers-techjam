"""Safe personalisation from the anonymised `user_profile`.

The profile carries no identifiers -- five aggregate fields:

    purchase_frequency     e.g. "3-4 prior purchases"
    average_prior_rating   float or null
    rating_style           e.g. "critical" / "usually positive"
    preference_tags        e.g. ["fit", "comfort", "durability"]
    summary                a sentence restating the above

It is weak signal and is treated as such: it contributes a small affinity term
to the reranker and never gates a candidate. Its real use is tie-breaking
between candidates the query cannot separate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Preference tags map to the words that actually appear in listing copy.
TAG_LEXICON: dict[str, tuple[str, ...]] = {
    "fit": ("fit", "fitted", "true to size", "slim", "relaxed", "tailored", "sizing"),
    "comfort": ("comfort", "comfortable", "soft", "breathable", "lightweight", "cozy"),
    "durability": ("durable", "sturdy", "long lasting", "heavy duty", "reinforced", "quality"),
    "style": ("style", "stylish", "fashion", "trendy", "elegant", "classic", "chic"),
    "material": ("cotton", "leather", "wool", "polyester", "fabric", "material", "silk"),
    "price": ("affordable", "value", "budget", "cheap", "inexpensive"),
    "color": ("color", "colour", "vibrant", "shade"),
    "size": ("size", "sizing", "petite", "plus", "wide", "narrow"),
    "quality": ("quality", "premium", "craftsmanship", "well made"),
    "versatility": ("versatile", "everyday", "all occasions", "multi purpose"),
}

_FREQUENCY_RE = re.compile(r"(\d+)")


@dataclass
class ShopperProfile:
    """Parsed view of the anonymised profile."""

    tags: tuple[str, ...] = ()
    lexicon: tuple[str, ...] = ()
    purchase_count: int = 0
    average_prior_rating: float | None = None
    rating_style: str = ""
    summary: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, payload: dict | None) -> "ShopperProfile":
        payload = payload or {}
        raw_tags = payload.get("preference_tags")
        tags = tuple(
            str(tag).strip().lower()
            for tag in (raw_tags if isinstance(raw_tags, list) else [])
            if str(tag).strip()
        )
        lexicon: list[str] = []
        for tag in tags:
            lexicon.extend(TAG_LEXICON.get(tag, (tag,)))
        frequency = _FREQUENCY_RE.findall(str(payload.get("purchase_frequency") or ""))
        rating = payload.get("average_prior_rating")
        return cls(
            tags=tags,
            lexicon=tuple(dict.fromkeys(lexicon)),
            purchase_count=int(frequency[-1]) if frequency else 0,
            average_prior_rating=float(rating) if isinstance(rating, (int, float)) else None,
            rating_style=str(payload.get("rating_style") or "").lower(),
            summary=str(payload.get("summary") or ""),
            raw=dict(payload),
        )

    @property
    def is_critical(self) -> bool:
        return "critical" in self.rating_style or (
            self.average_prior_rating is not None and self.average_prior_rating <= 2.5
        )

    def affinity(self, title: str, features_text: str) -> float:
        """Fraction of the shopper's preference lexicon present in the listing.

        Capped and normalised to [0, 1] so it can never overwhelm a retrieval
        signal -- this is a tie-breaker, not a ranking criterion.
        """
        if not self.lexicon:
            return 0.0
        haystack = (title + " " + features_text).lower()
        hits = sum(1 for word in self.lexicon if word in haystack)
        return min(1.0, hits / max(3.0, len(self.lexicon) * 0.5))

    def quality_bias(self) -> float:
        """A critical rater is marginally better served by well-reviewed items.

        Small on purpose: the profile describes how this shopper *rates*, not
        what they will buy.
        """
        if self.is_critical:
            return 1.0
        if self.average_prior_rating is not None and self.average_prior_rating >= 4.5:
            return 0.25
        return 0.5
