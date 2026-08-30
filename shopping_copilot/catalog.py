"""Catalog loading and normalisation.

Owns the one honest view of how sparse this catalog actually is:

    price        null on 78.9% of rows
    description  empty on 47.8%
    features     empty on 10.4%
    details.Color / Size / Material   4.9% / 1.9% / 4.1%
    details.Department               87.2%   <- worth filtering on
    store                            99.4%   <- worth filtering on

Anything that deletes candidates on a missing field will delete the target, so
this module exposes coverage flags (`has_price`, `has_description`, ...) and
lets the ranker price missingness instead of the retriever eliminating on it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .text import bigrams, flatten, tokenize

# details.Department is free text with inconsistent casing and hyphenation.
# Everything collapses onto these buckets.
GENDER_CANON = {
    "womens": "women", "women": "women", "woman": "women", "female": "women",
    "ladies": "women", "lady": "women", "juniors": "women",
    "mens": "men", "men": "men", "man": "men", "male": "men",
    "unisex adult": "unisex", "unisex-adult": "unisex", "unisexadult": "unisex",
    "unisex": "unisex", "unisex child": "kids", "unisex-child": "kids",
    "girls": "girls", "girl": "girls", "baby girls": "girls", "baby-girls": "girls",
    "boys": "boys", "boy": "boys", "baby boys": "boys", "baby-boys": "boys",
    "kids": "kids", "children": "kids", "child": "kids", "toddler": "kids",
    "baby": "kids", "infant": "kids",
}

# Gender terms as they appear in titles -- the fallback for the ~13% of rows
# with no Department. The word "women" alone appears in 42% of titles.
TITLE_GENDER = {
    "women": "women", "womens": "women", "woman": "women", "ladies": "women",
    "men": "men", "mens": "men", "man": "men",
    "girl": "girls", "girls": "girls",
    "boy": "boys", "boys": "boys",
    "unisex": "unisex", "kids": "kids", "toddler": "kids", "baby": "kids",
}

_CATEGORY_NOISE = {
    "clothing", "shoes", "jewelry", "clothing shoes & jewelry",
    "clothing, shoes & jewelry", "novelty & more", "clothing shoes jewelry",
    "shoes & jewelry", "shoes and jewelry",
}


@dataclass(slots=True)
class Product:
    """One catalog row, pre-parsed. `idx` is the dense integer id used by the
    inverted indexes; `parent_asin` is the only thing ever scored."""

    idx: int
    parent_asin: str
    title: str
    features_text: str
    categories_text: str
    description_text: str
    store: str
    category_path: tuple[str, ...]
    search_blob: str            # lowercased title+features+categories+description, for span matching
    gender: str | None          # canonical, from details.Department
    gender_fallback: str | None  # canonical, inferred from title/categories
    brand_key: str              # normalised `store`, for exact brand match
    price: float | None
    average_rating: float
    rating_number: int
    n_features: int
    has_price: bool
    has_description: bool
    has_features: bool
    popularity: float           # log1p(rating_number), min-max scaled to [0, 1]
    quality: float              # Bayesian-shrunk average_rating, scaled to [0, 1]

    @property
    def effective_gender(self) -> str | None:
        return self.gender or self.gender_fallback


def _canon_gender(value: object) -> str | None:
    if not value:
        return None
    key = str(value).strip().lower().replace("_", " ").replace("-", " ")
    key = " ".join(key.split())
    if key in GENDER_CANON:
        return GENDER_CANON[key]
    return GENDER_CANON.get(key.replace(" ", "-"))


def _gender_from_text(text: str) -> str | None:
    for raw in text.lower().replace("'", " ").split():
        token = "".join(ch for ch in raw if ch.isalpha())
        if token in TITLE_GENDER:
            return TITLE_GENDER[token]
    return None


def _gender_fallback(title: str, category_path: tuple[str, ...]) -> str | None:
    """Gender for the ~13% of rows with no details.Department.

    Title first (the word "women" alone appears in 42% of titles), then the
    category path, which is populated on 100% of rows and frequently carries a
    "Women" / "Men" / "Boys" level. Between them, unresolved gender drops from
    12.8% of the catalog to under 2%.
    """
    return _gender_from_text(title) or _gender_from_text(" ".join(category_path))


def _brand_key(store: object) -> str:
    return "".join(ch for ch in str(store or "").lower() if ch.isalnum())


def _parse_price(value: object) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _category_path(values: object) -> tuple[str, ...]:
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for value in values or []:
        for part in str(value).split(","):
            part = part.strip()
            if part and part.lower() not in _CATEGORY_NOISE:
                out.append(part)
    return tuple(out)


class Catalog:
    """The frozen 50k-row catalog, loaded once per process."""

    def __init__(self, path: str | Path = "data/catalog.jsonl") -> None:
        self.path = Path(path)
        self.products: list[Product] = []
        self.by_asin: dict[str, Product] = {}
        self._load()
        self._fit_priors()

    def _load(self) -> None:
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                raw_parent_asin = row.get("parent_asin")
                parent_asin = raw_parent_asin.strip() if isinstance(raw_parent_asin, str) else ""
                if not parent_asin or parent_asin in self.by_asin:
                    continue
                details = row.get("details") or {}
                title = str(row.get("title") or "")
                features = row.get("features") or []
                description = row.get("description") or []
                price = _parse_price(row.get("price"))
                # details is folded into the features text on purpose: the
                # simulator draws its constraint strings from features *and*
                # details, so they share one retrieval surface.
                features_text = " ".join([flatten(features), flatten(details)]).strip()
                category_path = _category_path(row.get("categories"))
                # idx must track list position, not the raw file line number --
                # every branch above can skip a line, and _tokens_cached()/
                # _bigrams_cached() index back into self.products by this value.
                product = Product(
                    idx=len(self.products),
                    parent_asin=parent_asin,
                    title=title,
                    features_text=features_text,
                    categories_text=flatten(row.get("categories")),
                    description_text=flatten(description),
                    store=str(row.get("store") or ""),
                    category_path=category_path,
                    search_blob=" ".join([
                        title, features_text, flatten(row.get("categories")),
                        flatten(description),
                    ]).lower(),
                    gender=_canon_gender(details.get("Department")),
                    gender_fallback=_gender_fallback(title, category_path),
                    brand_key=_brand_key(row.get("store")),
                    price=price,
                    average_rating=float(row.get("average_rating") or 0.0),
                    rating_number=int(row.get("rating_number") or 0),
                    n_features=len(features) if isinstance(features, list) else 0,
                    has_price=price is not None,
                    has_description=bool(description),
                    has_features=bool(features),
                    popularity=0.0,
                    quality=0.0,
                )
                self.products.append(product)
                self.by_asin[product.parent_asin] = product

    def _fit_priors(self) -> None:
        """Scale the prior features to [0, 1] so ranking weights stay
        comparable across features."""
        if not self.products:
            return
        logs = [math.log1p(p.rating_number) for p in self.products]
        lo, hi = min(logs), max(logs)
        span = (hi - lo) or 1.0
        rated = [p for p in self.products if p.rating_number > 0]
        global_mean = sum(p.average_rating for p in rated) / len(rated) if rated else 4.0
        prior_weight = 50.0
        for product, log_value in zip(self.products, logs):
            product.popularity = (log_value - lo) / span
            # Bayesian shrinkage toward the global mean: a 5.0 from two reviews
            # must not outrank a 4.6 from twelve thousand.
            shrunk = (
                (product.average_rating * product.rating_number + global_mean * prior_weight)
                / (product.rating_number + prior_weight)
            )
            product.quality = max(0.0, min(1.0, (shrunk - 1.0) / 4.0))

    # -- accessors -------------------------------------------------------
    def __len__(self) -> int:
        return len(self.products)

    def get(self, parent_asin: str) -> Product | None:
        return self.by_asin.get(parent_asin)

    def field_text(self, product: Product, field: str) -> str:
        if field == "title":
            return product.title
        if field == "features":
            return product.features_text
        if field == "categories":
            return product.categories_text
        if field == "description":
            return product.description_text
        if field == "store":
            return product.store
        raise KeyError(field)

    @lru_cache(maxsize=16384)
    def _tokens_cached(self, idx: int, field: str) -> tuple[str, ...]:
        return tuple(tokenize(self.field_text(self.products[idx], field)))

    def tokens(self, product: Product, field: str) -> tuple[str, ...]:
        """Lazily tokenised field text for a *candidate*.

        Deliberately lazy: only the ~200 reranked candidates per turn ever need
        this, so materialising it for all 50k rows would be wasted memory.
        """
        return self._tokens_cached(product.idx, field)

    @lru_cache(maxsize=16384)
    def _bigrams_cached(self, idx: int, field: str) -> frozenset[str]:
        return frozenset(bigrams(list(self._tokens_cached(idx, field))))

    def bigram_set(self, product: Product, field: str) -> frozenset[str]:
        return self._bigrams_cached(product.idx, field)
