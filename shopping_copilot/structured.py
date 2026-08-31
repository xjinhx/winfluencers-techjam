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
from .text import tokenize

SATISFIED = "satisfied"
VIOLATED = "violated"
UNKNOWN = "unknown"

# The slot names clear_soft() is allowed to touch -- "preference-shaped" slots,
# as opposed to gender/categories, which describe *what kind of thing* is
# being bought and are kept through an override regardless of which attribute
# changed. Shared with state.py so an override can scope its reset to only
# the attribute(s) the customer actually re-stated.
SOFT_FIELDS = frozenset({"materials", "colors", "sizes", "use_cases", "brands", "price"})

# categories/gender are ordinarily excluded from clear_soft's default reset --
# they describe *what kind of thing* is being bought, which is assumed not to
# change on override. That assumption breaks when the override IS a category
# change (e.g. "actually, a shirt instead of shoes"). CLEARABLE_FIELDS is the
# wider set an override may explicitly target when it positively re-states a
# new value for one of these -- never used as part of the blanket fallback.
CLEARABLE_FIELDS = SOFT_FIELDS | {"categories", "gender"}

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

# "faux leather" and "faux suede" are not leather or suede -- a bare substring
# match treats them identically to the genuine material. Measured against the
# catalog: 18.4% of "leather" mentions and 17.8% of "suede" mentions carry one
# of these qualifiers, versus <1% for every other material -- and "synthetic
# rubber" is a real material category, not a euphemism, which is why this is
# scoped to these two materials rather than applied by analogy to all of them.
QUALIFIABLE_MATERIALS = frozenset({"leather", "suede"})
_FAKE_QUALIFIER_WORDS = (
    "faux", "synthetic", "pu", "vegan", "artificial",
    "manmade", "man-made", "imitation", "fake",
)
_FAKE_MATERIAL_RE = {
    material: re.compile(
        r"\b(" + "|".join(_FAKE_QUALIFIER_WORDS) + r")[\s-]+" + material + r"\b", re.I
    )
    for material in QUALIFIABLE_MATERIALS
}

# The reverse case: words other than the bare material name that also mean
# "genuine" for that material, so a listing that only says "cowhide" still
# satisfies a customer who said "leather". Each is catalog-verified at a real
# volume (genuine leather 559, full grain 263+208, cowhide 155, sheepskin 122,
# lambskin 60, nappa 47, calfskin 47, top grain 43+12 -- in a 50k-row catalog),
# not guessed. "leather" itself stays in the set but is handled separately
# below, since the bare word also appears inside "faux leather".
GENUINE_MATERIAL_SYNONYMS: dict[str, frozenset[str]] = {
    "leather": frozenset({
        "genuine leather", "cowhide", "sheepskin", "lambskin",
        "nappa", "calfskin", "full grain", "full-grain", "top grain", "top-grain",
    }),
}

# How a fake-only mention (e.g. "faux leather" with no genuine mention
# anywhere in title/features/description) scores against a customer who asked
# for the material by name. "unknown" is the measured-safe default: 9 of the
# 200 public targets are THEMSELVES only faux/PU leather, because the harness
# derives the customer's stated material from the target's own listing with
# the same naive match this guards against -- "violated" would penalise the
# correct answer in exactly those sessions. "off" disables the genuine/fake
# distinction entirely (a fake-only mention counts as SATISFIED, matching the
# pre-fix behaviour); kept only so the three modes can be A/B compared.
FAKE_MATERIAL_MODE = "unknown"  # "off" | "unknown" | "violated"
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
        ones that describe *what kind of thing* is being bought -- unless
        `only` explicitly says otherwise.

        `only` scopes the reset to specific slot names (as returned by
        `filled_slots()`) -- e.g. an override that only re-states color
        should not also wipe an already-disclosed budget. Pass None (the
        default) for the old unconditional behaviour: clear every soft slot,
        but never `categories`/`gender` -- those are only ever cleared when
        a caller has positively identified a new value for them (see
        CLEARABLE_FIELDS), never as part of this blanket fallback.
        """
        fields = SOFT_FIELDS if only is None else (only & CLEARABLE_FIELDS)
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
        if "categories" in fields:
            self.categories.clear()
        if "gender" in fields:
            self.gender = None


class BrandVocabulary:
    """Lookup from surface text to a catalog `brand_key`.

    Built from `store`, which is populated on 99.4% of rows. Single-token brands
    must be at least four characters and outside the blocklist, because a false
    brand match is a large, confident, wrong constraint.

    BRAND_BLOCKLIST is hand-written, and hand-written lists only cover the cases
    someone thought of. In a catalog with 19,855 distinct stores, a great many
    ordinary English words are *also* somebody's brand -- and the customer
    quotes listing boilerplate verbatim, so those words arrive constantly.
    Measured at the lock-in turn over the 200 public sessions: a brand was
    extracted in 66 sessions and **62 of them were wrong** (a 94% false-positive
    rate), driven by `wash` (20), `sole` (15), `hand` (15) and `machine` (11)
    out of "Machine Wash" and "Rubber sole".

    That rate is real; its scoring cost is NOT, and the gate below is off by
    default for exactly that reason. `check_brand` returns VIOLATED for every
    product whose brand_key is not the extracted one, so a spurious brand
    applies -0.06 to 99.4% of the pool *uniformly* -- and a uniform offset
    cannot reorder anything. Measured: identical TechnicalScore (0.909328) at
    every threshold from 0.005 to 0.20. Only two classes of candidate differ,
    and both are negligible: the 8 products in 50,000 literally stored as
    "Sole"/"Wash"/"Machine" etc., which would wrongly take brand_satisfied
    +0.18 if one were ever drawn into a 200-candidate pool, and the 314 rows
    (0.63%) with no store, which score UNKNOWN (0.0) rather than -0.06.

    So the blocklist is backed by a *measured* test, the same move
    `constraint_commonness_penalty` made when a hardcoded phrase list was
    rejected for the same reason: compare how often a word appears as ordinary
    listing text against the fact that it names a store. Real single-word brands
    and ordinary words separate by two orders of magnitude --

        sole 0.206   wash 0.317   hand 0.168   machine 0.214
        hanes 0.0021  crocs 0.0030  carhartt 0.0014  skechers 0.0077

    -- so this needs no per-word curation and no list to maintain.
    """

    def __init__(self, catalog: Catalog, commonness=None) -> None:
        self.by_surface: dict[str, str] = {}
        # Text commonness per single-word surface, recorded at build time but
        # applied at *match* time: `Agent.apply_config` deliberately does not
        # rebuild the extractor, so a build-time gate would make the threshold
        # a silent no-op under tuning -- the same trap `rerank_depth` set.
        self.text_commonness: dict[str, float] = {}
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
            if len(words) == 1 and commonness is not None and surface not in self.text_commonness:
                # The index is keyed by stemmed terms; a surface that survives
                # tokenisation as nothing (pure digits, say) scores 0.0 and is
                # left to the length/blocklist guards above.
                terms = tokenize(surface)
                self.text_commonness[surface] = max(
                    (commonness(term) for term in terms), default=0.0
                )
            self.by_surface.setdefault(surface, product.brand_key)
            self.max_words = max(self.max_words, min(len(words), 4))

    def find(self, text: str, max_text_commonness: float = 0.0) -> set[str]:
        """Brand keys named in `text`.

        `max_text_commonness` > 0 drops single-word matches whose word is that
        common as ordinary listing text. 0.0 disables the gate entirely, which
        reproduces the pre-measurement behaviour byte-for-byte.
        """
        words = re.findall(r"[a-z0-9]+", text.lower())
        found: set[str] = set()
        for size in range(min(self.max_words, len(words)), 0, -1):
            for i in range(len(words) - size + 1):
                surface = " ".join(words[i:i + size])
                key = self.by_surface.get(surface)
                if not key:
                    continue
                if (
                    max_text_commonness > 0.0
                    and size == 1
                    and self.text_commonness.get(surface, 0.0) > max_text_commonness
                ):
                    continue
                found.add(key)
        return found


class ConstraintExtractor:
    """Pulls structured slots out of customer text."""

    def __init__(self, catalog: Catalog, commonness=None) -> None:
        self.catalog = catalog
        self.brands = BrandVocabulary(catalog, commonness)
        # Set by Agent from RetrievalConfig, and re-set by apply_config, so a
        # tuning sweep over the threshold takes effect without an index rebuild.
        self.brand_max_text_commonness: float = 0.0
        self.category_vocab: dict[str, str] = {}
        # Stemmed tokens per vocab level, so "shirt" (customer, singular)
        # still matches a catalog level stored as "Shirts" (plural) -- a
        # plain substring check only works when one word happens to contain
        # the other, which depends on which side is plural by luck.
        self._category_tokens: dict[str, tuple[str, ...]] = {}
        for product in catalog.products:
            for level in product.category_path:
                key = level.lower()
                self.category_vocab.setdefault(key, level)
                self._category_tokens.setdefault(key, tuple(tokenize(level)))

    def update(self, constraints: Constraints, text: str) -> Constraints:
        lowered = text.lower()

        if constraints.gender is None:
            # First match wins, EXCEPT that a specific child audience beats the
            # generic one. "baby girls bodysuits" hit "baby" first and resolved
            # to `kids`, discarding the "girls" standing right next to it --
            # and the customer's opening line is built from the target's own
            # category path, so this pattern is common (224 catalog rows read
            # exactly this way). Only the parent/child pair is reordered;
            # nothing else about the scan changes.
            found: list[str] = []
            for raw in re.findall(r"[a-z]+", lowered):
                mapped = GENDER_WORDS.get(raw)
                if mapped:
                    mapped = GENDER_CANON.get(mapped, mapped)
                    if mapped not in found:
                        found.append(mapped)
                    if mapped not in {"kids", "boys", "girls"}:
                        break
            for candidate in found:
                if candidate in {"boys", "girls"}:
                    constraints.gender = candidate
                    break
            else:
                constraints.gender = found[0] if found else None

        constraints.brands |= self.brands.find(text, self.brand_max_text_commonness)
        constraints.materials |= {m.lower() for m in MATERIAL_RE.findall(text)}
        constraints.colors |= {c.lower() for c in COLOR_RE.findall(text)}
        constraints.sizes |= {s.lower() for s in SIZE_RE.findall(text)}
        constraints.use_cases |= {u.lower() for u in USE_CASE_RE.findall(text)}

        text_tokens = set(tokenize(lowered))
        for level, level_tokens in self._category_tokens.items():
            if len(level) > 3 and level_tokens and set(level_tokens) <= text_tokens:
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
    # "kids" is the PARENT of boys/girls, not a sibling. Treating the audience
    # relation as equality made a listing punish itself: the customer's opening
    # line is built from the target's own category path, so "Baby Girls
    # Bodysuits" extracts `kids` and then scored every actual baby-girls
    # listing VIOLATED at -0.23. Measured over all 50k rows against the opening
    # line each one would generate, 506 (1.01%) took that own goal -- 313 of
    # them this parent/child case. Public-set exposure was 0/200, which is why
    # it survived: ~0.6% of rows is ~1 session in 200 and ~5 in 800.
    if constraints.gender == "kids" and actual in {"boys", "girls"}:
        return SATISFIED
    # The reverse is weaker evidence, not contrary evidence: a listing filed
    # only "kids" against a customer who said "boys" is less specific, not
    # conflicting. Same asymmetry the unisex/adult pair above already uses,
    # and the module's rule that silence never reads as VIOLATED.
    if actual == "kids" and constraints.gender in {"boys", "girls"}:
        return UNKNOWN
    # `unisex` here is unisex-*adult* (GENDER_CANON sends "unisex child" to
    # "kids"), but a bare "unisex" listing can be either, so it is ambiguous
    # against a kids request rather than contrary.
    if constraints.gender == "kids" and actual == "unisex":
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


def _searchable_text(product: Product) -> str:
    """title + features + description, lowercased.

    Every text-based constraint check searches all three -- `description` was
    previously left out here (unlike the lexical/dense retrieval routes, which
    already index it), so a material/color mentioned only in the description
    silently read as UNKNOWN instead of SATISFIED.
    """
    return (
        product.title + " " + product.features_text + " " + product.description_text
    ).lower()


def _bare_word_is_genuine(word: str, lowered_text: str) -> bool:
    """True if at least one occurrence of `word` is not a faux/synthetic qualifier.

    Only meaningful for QUALIFIABLE_MATERIALS -- everything else has no known
    "fake" reading, so any occurrence counts.
    """
    if word not in QUALIFIABLE_MATERIALS:
        return word in lowered_text
    total = len(re.findall(r"\b" + word + r"\b", lowered_text))
    if total == 0:
        return False
    qualified = len(_FAKE_MATERIAL_RE[word].findall(lowered_text))
    return total > qualified


def check_material(product: Product, constraints: Constraints) -> str:
    """SATISFIED on a genuine mention (bare word or a GENUINE_MATERIAL_SYNONYMS
    entry); otherwise a fake-only mention is scored per FAKE_MATERIAL_MODE;
    otherwise UNKNOWN (never guessed VIOLATED from silence -- see module
    docstring)."""
    wanted = constraints.materials
    if not wanted:
        return UNKNOWN
    lowered = _searchable_text(product)
    fake_only = False
    for word in wanted:
        synonyms = GENUINE_MATERIAL_SYNONYMS.get(word, frozenset())
        if any(synonym in lowered for synonym in synonyms):
            return SATISFIED
        if _bare_word_is_genuine(word, lowered):
            return SATISFIED
        if word in QUALIFIABLE_MATERIALS and word in lowered:
            fake_only = True
    if fake_only:
        if FAKE_MATERIAL_MODE == "off":
            return SATISFIED
        if FAKE_MATERIAL_MODE == "violated":
            return VIOLATED
        return UNKNOWN
    return UNKNOWN


def check_color(product: Product, constraints: Constraints) -> str:
    return _check_text_set(_searchable_text(product), constraints.colors)


def evaluate_all(product: Product, constraints: Constraints) -> dict[str, str]:
    """Three-way outcome per constraint dimension.

    Note what is missing: nothing here returns "drop this candidate". Colour
    resolves to SATISFIED or UNKNOWN and never VIOLATED, because absence of
    the word in sparse copy is not evidence of conflict. Material is the same
    except for a fake-material mention (see FAKE_MATERIAL_MODE), which is the
    one case with positive contrary evidence rather than mere silence.
    """
    return {
        "gender": check_gender(product, constraints),
        "brand": check_brand(product, constraints),
        "category": check_category(product, constraints),
        "price": check_price(product, constraints),
        "material": check_material(product, constraints),
        "color": check_color(product, constraints),
    }
