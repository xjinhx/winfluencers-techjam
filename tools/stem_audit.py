"""Find vocabulary splits: lemma families the stemmer pulls apart.

A stemming rule fails silently. When `hoodies` stems to `hoody` while every
title says `hoodie`, nothing raises -- the query simply scores zero against the
field that mattered, the target never enters the candidate pool, and the session
reads as an ordinary miss. The defect that motivated this tool cost one hit on
the public set and was invisible in every aggregate metric.

So audit the vocabulary directly. For each surface form in the catalog, generate
the forms that are plausibly the *same word* (plural, possessive-free plural,
-y/-ies and -f/-ves alternations), and report every pair that is attested in the
catalog but stems to two different terms. Each such pair is a query spelling
that cannot match an index spelling.

Not every split is a bug -- 'lens'/'lenses' and genuinely distinct words that
happen to look related will show up. The output is a ranked worklist, not a
verdict. Rank is by the postings mass at stake: a split between two forms that
each occur thousands of times matters more than one between a pair of hapaxes.

    python -m tools.stem_audit
    python -m tools.stem_audit --min-count 50 --top 40
    python -m tools.stem_audit --field title
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from shopping_copilot.catalog import Catalog
from shopping_copilot.text import (
    LOW_VALUE,
    STOPWORDS,
    TOKEN_RE,
    stem,
)


IRREGULAR = {
    "men": "man", "women": "woman", "children": "child", "feet": "foot",
    "teeth": "tooth", "mice": "mouse", "geese": "goose", "people": "person",
}


def variants(word: str) -> set[str]:
    """Surface forms that would normally be the same lemma as `word`.

    Deliberately generous on the inflection side and silent about semantics:
    the point is to propose pairs a shopper might type interchangeably, and let
    the frequency ranking sort out which proposals are worth a human's time.
    """
    out: set[str] = {word + "s", word + "es"}
    if word in IRREGULAR:
        out.add(IRREGULAR[word])
    out.update(plural for plural, singular in IRREGULAR.items() if singular == word)
    if word.endswith("y") and len(word) > 2:
        out.add(word[:-1] + "ies")
    if word.endswith("ie"):
        out.add(word[:-2] + "y")
    if word.endswith("f") and len(word) > 2:
        out.add(word[:-1] + "ves")
    if word.endswith("fe") and len(word) > 3:
        out.add(word[:-2] + "ves")
    if word.endswith(("ch", "sh", "s", "x", "z")):
        out.add(word + "es")
    if word.endswith("s") and len(word) > 3:
        out.add(word[:-1])
        if word.endswith("es") and len(word) > 4:
            out.add(word[:-2])
            if word.endswith("ies"):
                out.update({word[:-3] + "y", word[:-3] + "ie"})
            if word.endswith("ves"):
                out.update({word[:-3] + "f", word[:-3] + "fe"})
    out.discard(word)
    return out


def collect_counts(catalog: Catalog, field: str) -> dict[str, int]:
    """Unstemmed surface counts. `catalog.surface_counts` already holds titles
    and categories; anything wider needs its own pass."""
    if field == "indexed":
        return dict(catalog.surface_counts)
    counts: dict[str, int] = {}
    for product in catalog.products:
        if field == "title":
            text = product.title
        elif field == "categories":
            text = product.categories_text
        else:
            text = " ".join([
                product.title, product.features_text,
                product.categories_text, product.description_text, product.store,
            ])
        for raw in TOKEN_RE.findall(text):
            token = raw.lower()
            if len(token) < 2 or token in STOPWORDS or token in LOW_VALUE:
                continue
            counts[token] = counts.get(token, 0) + 1
    return counts


def find_splits(counts: dict[str, int], min_count: int) -> list[tuple]:
    """Attested pairs of related surface forms that stem to different terms."""
    seen: set[tuple[str, str]] = set()
    rows: list[tuple] = []
    for word, count in counts.items():
        if count < min_count:
            continue
        for other in variants(word):
            other_count = counts.get(other, 0)
            if other_count < min_count:
                continue
            pair = (word, other) if word < other else (other, word)
            if pair in seen:
                continue
            seen.add(pair)
            a_stem, b_stem = stem(pair[0]), stem(pair[1])
            if a_stem == b_stem:
                continue
            # The smaller side is what actually goes unmatched, so it bounds
            # the damage; ties on the total keep the ordering stable.
            at_stake = min(counts[pair[0]], counts[pair[1]])
            rows.append((at_stake, pair[0], counts[pair[0]], a_stem,
                         pair[1], counts[pair[1]], b_stem))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument(
        "--field", default="indexed",
        choices=["indexed", "title", "categories", "all"],
        help="'indexed' reuses the counts the catalog already built (titles + "
             "categories); the others rebuild from that field alone.",
    )
    parser.add_argument("--min-count", type=int, default=20,
                        help="ignore forms rarer than this on either side")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    catalog = Catalog(args.catalog)
    counts = collect_counts(catalog, args.field)
    rows = find_splits(counts, args.min_count)

    print(f"catalog        : {len(catalog.products)} products")
    print(f"field          : {args.field}")
    print(f"vocabulary     : {len(counts)} surface forms")
    print(f"splits found   : {len(rows)} (min-count {args.min_count})")
    if not rows:
        print("\nNo vocabulary splits above the threshold.")
        return

    print(f"\n{'at stake':>9}  {'form a':<16}{'count':>7}  {'stem':<14}"
          f"{'form b':<16}{'count':>7}  {'stem':<14}")
    print("-" * 96)
    for at_stake, a, a_n, a_s, b, b_n, b_s in rows[:args.top]:
        print(f"{at_stake:>9}  {a:<16}{a_n:>7}  {a_s:<14}{b:<16}{b_n:>7}  {b_s:<14}")
    if len(rows) > args.top:
        print(f"\n... {len(rows) - args.top} more; raise --top to see them.")


if __name__ == "__main__":
    main()
