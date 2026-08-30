"""Route C -- structured constraints, and their three-way evaluation.

Only three catalog fields have coverage worth acting on:

    details.Department   87.2%  ->  gender, the highest-elimination-power
                                    attribute in clothing
    store                99.4%  ->  brand, 19,855 distinct values
    categories          100.0%  ->  soft boost only; 800 leaves, contains junk

Everything else (Color 4.9%, Material 4.1%, Size 1.9%, price 21.1%) is too
sparse to filter on. So constraints are never filters. Every check returns
SATISFIED / VIOLATED / UNKNOWN and the ranker prices each outcome, with UNKNOWN
a mild penalty. A hard filter on a field that is null four times out of five
deletes the target four times out of five.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .catalog import GENDER_CANON, Catalog, Product

SATISFIED = "satisfied"
VIOLATED = "violated"
UNKNOWN = "unknown"

# The slot names clear_soft() is allowed to touch -- "preference-shaped" slots,
# as opposed to gender/categories, which describe *what kind of thing* is
# being bought and are kept through an override regardless of which attribute
# changed. Shared with state.py so an override can scope its reset to only
# the attribute(s) the customer actually re-stated.
SOFT_FIELDS = frozenset({"materials", "colors", "sizes", "use_cases", "brands", "price"})

# Mirrors the vocabulary the simulated customer actually uses.
MATERIALS = (
    "cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk",
    "rayon", "fabric", "denim", "linen", "cashmere", "suede", "satin",
    "velvet", "mesh", "acrylic", "bamboo", "alloy", "sterling", "silver",
    "gold", "steel", "titanium", "brass", "copper", "rubber", "canvas",
)
COLORS = (
    "black", "white", "blue", "red", "pink", "green", "brown", "gray",
    "grey", "purple", "yellow", "orange", "beige", "navy", "gold", "silver",
    "ivory", "burgundy", "teal", "khaki", "cream",
)
SIZES = (
    "xs", "small", "medium", "large", "xl", "xxl", "petite", "plus",
    "regular", "wide", "narrow", "slim",
)
USE_CASES = (
    "hiking", "running", "gym", "workout", "winter", "summer", "outdoor",
    "work", "wedding", "party", "casual", "formal", "travel", "beach",
    "sleep", "yoga", "office", "school", "everyday", "date", "dinner",
)

MATERIAL_RE = re.compile(r"\b(" + "|".join(MATERIALS) + r")\b", re.I)
COLOR_RE = re.compile(r"\b(" + "|".join(COLORS) + r")\b", re.I)
SIZE_RE = re.compile(r"\b(" + "|".join(SIZES) + r")\b", re.I)
USE_CASE_RE = re.compile(r"\b(" + "|".join(USE_CASES) + r")\b", re.I)
PRICE_RE = re.compile(r"(?:\$|under|below|less than|budget around|up to)\s*\$?\s*(\d+(?:\.\d+)?)", re.I)

# Gender words as a customer says them, mapped through the catalog's canon.
GENDER_WORDS = {
    "women": "women", "womens": "women", "woman": "women", "ladies": "women",
    "female": "women", "her": "women", "she": "women",
    "men": "men", "mens": "men", "man": "men", "male": "men", "him": "men",
    "he": "men", "girls": "girls", "girl": "girls", "boys": "boys",
    "boy": "boys", "kids": "kids", "children": "kids", "toddler": "kids",
    "baby": "kids", "unisex": "unisex",
}

# Store names that are also ordinary English. Matching these as brands costs
# more than it gains, so they are never treated as a brand mention.
BRAND_BLOCKLIST = frozenset({
    "fashion", "classic", "original", "premium", "quality", "style", "comfort",
    "essentials", "collection", "apparel", "clothing", "brand", "generic",
    "unbranded", "sports", "outdoor", "casual", "modern", "simple", "basic",
    "gold", "silver", "star", "love", "cool", "pure", "true", "one", "gap",
    "next", "new", "the", "and", "for", "art", "sun", "sky", "top", "hot",
})


@dataclass
class Constraints:
    """Accumulated structured slots for one session."""

    gender: str | None = None
    brands: set[str] = field(default_factory=set)
    categories: set[str] = field(default_factory=set)
    materials: set[str] = field(default_factory=set)
    colors: set[str] = field(default_factory=set)
    sizes: set[str] = field(default_factory=set)
    use_cases: set[str] = field(default_factory=set)
    price_max: float | None = None

    def filled_slots(self) -> list[str]:
        out: list[str] = []
        if self.gender:
            out.append("gender")
        for name in ("brands", "categories", "materials", "colors", "sizes", "use_cases"):
            if getattr(self, name):
                out.append(name)
        if self.price_max is not None:
            out.append("price")
        return out

    def clear_soft(self, only: set[str] | None = None) -> None:
        """Drop preference-shaped slots on an intent override, keeping the
        ones that describe *what kind of thing* is being bought.

        `only` scopes the reset to specific slot names (as returned by
        `filled_slots()`) -- e.g. an override that only re-states color
        should not also wipe an already-disclosed budget. Pass None (the
        default) for the old unconditional behaviour: clear every soft slot.
        """
        fields = SOFT_FIELDS if only is None else (only & SOFT_FIELDS)
        if "materials" in fields:
            self.materials.clear()
        if "colors" in fields:
            self.colors.clear()
        if "sizes" in fields:
            self.sizes.clear()
        if "use_cases" in fields:
            self.use_cases.clear()
        if "brands" in fields:
            self.brands.clear()
        if "price" in fields:
            self.price_max = None


class BrandVocabulary:
    """Lookup from surface text to a catalog `brand_key`.

    Built from `store`, which is populated on 99.4% of rows. Single-token brands
    must be at least four characters and outside the blocklist, because a false
    brand match is a large, confident, wrong constraint.
    """

    def __init__(self, catalog: Catalog) -> None:
        self.by_surface: dict[str, str] = {}
        self.max_words = 1
        for product in catalog.products:
            store = product.store.strip().lower()
            if not store or not product.brand_key:
                continue
            words = re.findall(r"[a-z0-9]+", store)
            if not words:
                continue
            surface = " ".join(words)
            if len(words) == 1 and (len(words[0]) < 4 or words[0] in BRAND_BLOCKLIST):
                continue
            self.by_surface.setdefault(surface, product.brand_key)
            self.max_words = max(self.max_words, min(len(words), 4))

    def find(self, text: str) -> set[str]:
        words = re.findall(r"[a-z0-9]+", text.lower())
        found: set[str] = set()
        for size in range(min(self.max_words, len(words)), 0, -1):
            for i in range(len(words) - size + 1):
                surface = " ".join(words[i:i + size])
                key = self.by_surface.get(surface)
                if key:
                    found.add(key)
        return found


class ConstraintExtractor:
    """Pulls structured slots out of customer text."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.brands = BrandVocabulary(catalog)
        self.category_vocab: dict[str, str] = {}
        for product in catalog.products:
            for level in product.category_path:
                self.category_vocab.setdefault(level.lower(), level)

    def update(self, constraints: Constraints, text: str) -> Constraints:
        lowered = text.lower()

        if constraints.gender is None:
            for raw in re.findall(r"[a-z]+", lowered):
                mapped = GENDER_WORDS.get(raw)
                if mapped:
                    constraints.gender = GENDER_CANON.get(mapped, mapped)
                    break

        constraints.brands |= self.brands.find(text)
        constraints.materials |= {m.lower() for m in MATERIAL_RE.findall(text)}
        constraints.colors |= {c.lower() for c in COLOR_RE.findall(text)}
        constraints.sizes |= {s.lower() for s in SIZE_RE.findall(text)}
        constraints.use_cases |= {u.lower() for u in USE_CASE_RE.findall(text)}

        for level in self.category_vocab:
            if len(level) > 3 and level in lowered:
                constraints.categories.add(level)

        price = PRICE_RE.search(text)
        if price:
            try:
                value = float(price.group(1))
                if 0 < value < 100000:
                    constraints.price_max = (
                        value if constraints.price_max is None
                        else min(constraints.price_max, value)
                    )
            except ValueError:
                pass
        return constraints


def check_gender(product: Product, constraints: Constraints) -> str:
    if not constraints.gender:
        return UNKNOWN
    actual = product.effective_gender
    if actual is None:
        return UNKNOWN
    if actual == constraints.gender:
        return SATISFIED
    # unisex satisfies an adult gender request; kids sizing does not.
    if actual == "unisex" and constraints.gender in {"women", "men"}:
        return SATISFIED
    if constraints.gender == "unisex" and actual in {"women", "men"}:
        return UNKNOWN
    return VIOLATED


def check_brand(product: Product, constraints: Constraints) -> str:
    if not constraints.brands:
        return UNKNOWN
    if not product.brand_key:
        return UNKNOWN
    return SATISFIED if product.brand_key in constraints.brands else VIOLATED


def check_category(product: Product, constraints: Constraints) -> str:
    if not constraints.categories:
        return UNKNOWN
    if not product.category_path:
        return UNKNOWN
    levels = {level.lower() for level in product.category_path}
    return SATISFIED if levels & constraints.categories else VIOLATED


def check_price(product: Product, constraints: Constraints) -> str:
    if constraints.price_max is None or product.price is None:
        return UNKNOWN
    # 20% headroom: the simulator says "budget around $X", not "at most $X".
    return SATISFIED if product.price <= constraints.price_max * 1.2 else VIOLATED


def _check_text_set(text: str, wanted: set[str]) -> str:
    if not wanted:
        return UNKNOWN
    lowered = text.lower()
    hits = [w for w in wanted if w in lowered]
    if hits:
        return SATISFIED
    return UNKNOWN


def check_material(product: Product, constraints: Constraints) -> str:
    return _check_text_set(
        product.title + " " + product.features_text, constraints.materials
    )


def check_color(product: Product, constraints: Constraints) -> str:
    return _check_text_set(
        product.title + " " + product.features_text, constraints.colors
    )


def evaluate_all(product: Product, constraints: Constraints) -> dict[str, str]:
    """Three-way outcome per constraint dimension.

    Note what is missing: nothing here returns "drop this candidate". Material
    and colour resolve to SATISFIED or UNKNOWN and never VIOLATED, because
    absence of the word in sparse copy is not evidence of conflict.
    """
    return {
        "gender": check_gender(product, constraints),
        "brand": check_brand(product, constraints),
        "category": check_category(product, constraints),
        "price": check_price(product, constraints),
        "material": check_material(product, constraints),
        "color": check_color(product, constraints),
    }
