# Phase 1 — Data & Codebase Discovery Report

Branch: `dylan-data-error`
Scope: investigation only. No source code, tests, datasets, or configuration were modified to produce these findings.

Evidence key used throughout this document:

- **FACT** — directly observed by reading the code or by running an analysis script against the actual files `data/catalog.jsonl` (50,000 lines) and `data/public_set.jsonl` (200 lines).
- **DOCUMENTATION CLAIM** — a statement made in `README.md`, `DATA_ATTRIBUTION.md`, `data/README.md`, or `docs/*`.
- **INFERENCE** — a reasonable interpretation of observed evidence, not itself directly measured.
- **HYPOTHESIS** — a plausible explanation for an observation that cannot be confirmed from the artifacts available in this repo checkout (e.g. because it depends on the gitignored organizer-only pipeline).
- **RECOMMENDATION** — a suggested direction for Phase 2. Nothing in this document has been implemented.

---

## 1. Repository / Data Flow

### 1.1 Repository layout (FACT)

```
README.md
DATA_ATTRIBUTION.md
data/
  README.md
  catalog.jsonl        (50,000 lines)
  public_set.jsonl     (200 lines)
docs/
  competition_specification.md
  submission_rules.md
  agent_api_contract.json
  evaluation_config.json
  baseline_results.json
starter/
  __init__.py
  agent.py
evaluator/
  __init__.py
  local_evaluator.py
tests/
  __init__.py
  test_evaluator.py
```

There is **no separate validation/normalization module, no database module, no dedicated search-index module, and no ranking module**. All catalog loading, indexing, retrieval, and scoring logic lives in exactly two files: [starter/agent.py](../starter/agent.py) and [evaluator/local_evaluator.py](../evaluator/local_evaluator.py).

### 1.2 Gitignored / organizer-only material (FACT, from `.gitignore`)

```
__pycache__/, *.py[cod], .DS_Store, .env, results.json
data/catalog.jsonl
data/releases/
organizer/                                  # Organizer-only code, private evaluation data, manifests, and build reports.
secure/
docs/audits/                                # Internal provenance and release-audit working documents.
docs/data_selection_audit.md
docs/participant_release_checklist.md
tests/test_5core_builder.py                 # Organizer-only pipeline tests.
tests/test_organizer_pipeline.py
```

**INFERENCE:** README's references to `organizer/JUDGING_RUNBOOK.md`, `organizer/private_release_checklist.md`, and `organizer/JUDGING_DAY_SOP.md` are not broken links — those files intentionally do not ship in the participant checkout. This also means **the pipeline that produced `catalog.jsonl` / `public_set.jsonl` from raw Amazon Reviews 2023 (the "5-core builder", the "organizer pipeline", the "data selection audit") is not visible to us.** Everything in this report is an audit of the frozen output only, not of the generation process. See open question in §9.

### 1.3 Traced catalog data flow (FACT — file/function/input/output/validation)

| Stage | File : Function | Input | Output | Validation / error handling present? |
|---|---|---|---|---|
| Loading + parsing (starter) | [starter/agent.py:52-54](../starter/agent.py#L52) `Agent._build_index` | `data/catalog.jsonl`, opened and iterated line by line | one `dict` per line via `json.loads(line)` | **None.** No blank-line skip, no `try/except` around `json.loads`, no check that the parsed value is a `dict`. |
| Loading + parsing (evaluator) | [evaluator/local_evaluator.py:112-123](../evaluator/local_evaluator.py#L112) `catalog_index` | same file | `identifiers: set[str]`, `categories: dict[str, list[str]]`, `products: dict[str, dict]` | Same as above — no blank-line skip, no exception handling, no type check. |
| Field access | both functions above | parsed `dict` | — | Uses `product["parent_asin"]` (bracket indexing, **not** `.get`) — raises `KeyError` on any record missing that key. Not currently triggered (0 missing observed, §2). |
| "Normalization" for indexing | `_text()` in [starter/agent.py:17-24](../starter/agent.py#L17) | raw field value of any type (`None`, `dict`, `list`, scalar) | flattened string | Coerces `None`→`""`, `dict`→`"key value"` pairs joined, `list`→joined stringified items. No validation, purely a formatter. |
| "Normalization" for intent cards | `searchable_text()` / `_flatten_values()` in [evaluator/local_evaluator.py:27-45](../evaluator/local_evaluator.py#L27) | same | flattened string / list of strings | Same style of type coercion, no rejection logic. |
| Storage / index | `Agent._build_index` ([starter/agent.py:44-71](../starter/agent.py#L44)) | flattened rows | in-memory SQLite `:memory:` FTS5 virtual table (`unicode61 remove_diacritics 2` tokenizer) | Duplicate `parent_asin` would insert two FTS rows (no dedup) — diverges from the evaluator's dict-based index (see next row). Not currently triggered (0 duplicates observed). |
| Storage / index (evaluator) | `catalog_index` | same | `products` / `categories` dicts keyed by `parent_asin` | Duplicate `parent_asin` would **silently overwrite** — last-seen row wins, no warning. Not currently triggered. |
| Retrieval / ranking | `Agent.respond` ([starter/agent.py:77-102](../starter/agent.py#L77)) | tokenized `user_message` (via `TOKEN_RE` + `STOPWORDS` filtering, [starter/agent.py:9-14](../starter/agent.py#L9)) | FTS5 `MATCH` query ranked by `bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0)`, `LIMIT top_k` | No fallback if the index failed to build; no handling of empty query (`if not expression: recommendations = []` — this one case *is* handled). |
| Recommendation validation | `normalize_recommendations()` ([evaluator/local_evaluator.py:95-109](../evaluator/local_evaluator.py#L95)) | agent's raw `recommendations` payload (untrusted, agent-authored) | de-duplicated, catalog-valid, order-preserving list, capped at `TOP_K=10` | **This is the one place with real, deliberate validation in the whole codebase**: rejects non-list payloads, non-dict/non-string items, blank/whitespace IDs, duplicate IDs, and IDs absent from `catalog_ids`. |

### 1.4 Traced public-session data flow (FACT)

| Stage | File : Function | Notes |
|---|---|---|
| Parsing | `load_jsonl()` ([evaluator/local_evaluator.py:90-92](../evaluator/local_evaluator.py#L90)) | Skips blank lines (`if line.strip()`), **but no `try/except`** around `json.loads` — one malformed line aborts the entire evaluator run. |
| Hidden-field derivation | `materialize_hidden_fields()` ([evaluator/local_evaluator.py:204-213](../evaluator/local_evaluator.py#L204)) | If the sample dict lacks `intent_card` and `behavior` keys (true for **all 200** current public records, §3), derives them live via `intent_card(products[target])` and `behavior_for(...)`. `products[target]` is a bare dict index: **if `ground_truth.parent_asin` were ever absent from the catalog dict, this raises an uncaught `KeyError` and aborts the whole evaluation run** rather than scoring that one session as a miss. |
| Session loop | `evaluate()` ([evaluator/local_evaluator.py:216-295](../evaluator/local_evaluator.py#L216)) | Wraps only the `agent.respond(...)` call in `try/except Exception` (treated as an empty response on failure). Response shape is defensively checked (`isinstance(response, dict)` and `isinstance(response.get("message"), str)`), falling back to an empty response otherwise. Nothing else in the loop (parsing, hidden-field derivation, catalog indexing) is protected. |
| Scoring | `metric_summary()` ([evaluator/local_evaluator.py:188-201](../evaluator/local_evaluator.py#L188)) | Simple arithmetic over already-validated session dicts; no data-error surface of its own. |

---

## 2. Actual Catalog Characteristics (50,000 records — measured, not sampled)

- **Row count:** 50,000 lines. **0 blank lines, 0 malformed JSON, 0 non-dict records.** Every line parses cleanly. (FACT)
- **`parent_asin`:** 100% present, 100% non-empty, 100% `str` type. **0 duplicates across all 50,000 rows, 0 missing.** (FACT)
- **`title`:** 100% present, always `str`. **2 records (0.004%) have an empty string title**: `B009LL8WKY`, `B005CIGN88`.
- **`features`:** always a `list`; item type always `str` (250,842 total items checked, all `str`). **5,219 records (10.44%) have an empty list.**
- **`description`:** always a `list`; item type always `str` (72,566 total items checked, all `str`). **23,887 records (47.77%) have an empty list** — nearly half the catalog has no description text.
- **`price`:** type breakdown — `float`: 10,410 (20.82%); `null`: 39,473 (**78.95%**); `str`: 117 (0.234%). The 117 string values decompose into **112 literal em-dash `"—"` placeholders** and **5 `"from X.XX"`-style range strings** (examples: `"from 12.99"`, `"from 12.46"`, `"from 21.30"`, `"from 8.98"`, `"from 5.99"`). Exactly **1 record has `price == 0.0`**: `B07CB1T45B`. **0 negative prices.**
- **`categories`:** always a non-empty `list` (0 missing, 0 empty). Length distribution: 5 items → 23,544; 4 → 12,979; 6 → 5,858; 7 → 3,099; 3 → 2,280; 2 → 2,139; 8 → 101. First element is `"Clothing, Shoes & Jewelry"` in 49,990/50,000 records; the remaining **10 records start with `"Shoe, Jewelry & Watch Accessories"`** — a legitimate alternate top-level taxonomy branch (INFERENCE), not corruption.
- **`details`:** always a `dict`. **1,670 records (3.34%) have an empty `{}`.** 287 distinct keys observed across the full catalog (full breakdown in §4).
- **`average_rating`:** always `float`. **0 invalid values** — full distribution (bucketed to nearest 0.5) is 1.0→806, 1.5→136, 2.0→634, 2.5→746, 3.0→3,135, 3.5→6,571, 4.0→14,651, 4.5→15,010, 5.0→8,311. All within [0, 5].
- **`rating_number`:** always `int`. **0 negative values, 0 zero values.**
- **`store`:** `str` in 49,686 records (99.37%), `null` in **314 records (0.63%)**; **0 empty-string values** among the populated strings.

**INFERENCE:** none of the above gaps read as corruption — they are consistent with genuine upstream sparsity in Amazon product metadata (missing descriptions, missing prices for variant-priced listings, etc.). They are nonetheless real and unhandled by any code path (see §6/§8).

---

## 3. Actual public_set.jsonl Characteristics (200 records — measured)

- **Parsing:** 0 blank lines, 0 malformed JSON; all 200 lines parse to dicts.
- **Top-level keys present in 100% of the 200 records:** `category_bucket`, `difficulty_bucket`, `ground_truth`, `sample_id`, `scenario_type`, `user_profile`.
- **No record contains `intent_card` or `behavior`** (0/200) — confirms that `materialize_hidden_fields()`'s derive-branch is the one actually exercised for every public session (FACT, matches DOCUMENTATION CLAIM in `data/README.md` that hidden intent cards are "not shipped in this participant file").
- **`scenario_type` distribution:** `buying` 80, `browsing` 80, `intent_override` 30, `boundary` 10 → exactly 40% / 40% / 15% / 5%. **Matches `docs/competition_specification.md`'s documented scenario mix exactly.**
- **`sample_id`:** all 200 values are unique; all share the prefix `public_`; **0 duplicates.**
- **`ground_truth.parent_asin`:** contains exactly one key, `parent_asin`, in all 200 records. **All 200 targets exist in the catalog** (0 misses). **0 targets are reused** across sessions — every session points to a distinct product.
- **`user_profile`:** all 5 sub-fields required by `docs/agent_api_contract.json` (`purchase_frequency`, `average_prior_rating`, `rating_style`, `preference_tags`, `summary`) are present in 100% of records with the documented types; `average_prior_rating` is always a `float` (0 nulls, despite the contract schema allowing `null`); `summary` is always a non-empty string.
  - **`purchase_frequency`: the single value `"3-4 prior purchases"` appears in all 200 records — zero variance.** (Flagged in §8.)
  - **`rating_style`:** exactly 3 distinct values — `"usually positive"` (134), `"critical"` (45), `"mixed"` (21).
  - **`preference_tags`:** a closed vocabulary of exactly **9 distinct tags**: `fit` (163), `material` (154), `comfort` (144), `style` (101), `durability` (47), `performance` (26), `warmth` (18), `weather` (12), `general shopping` (1). List length distribution: 4 tags → 121, 2 → 43, 3 → 30, 1 → 6. 0 empty lists.
- **Two fields present in the data but absent from all documentation and all code:**
  - `category_bucket` — constant value `"clothing"` in all 200 records (0 variance).
  - `difficulty_bucket` — values `easy` (80), `medium` (90), `hard` (30); **perfectly collinear with `scenario_type`**: `buying`→`easy` always, `browsing`→`medium` always, `intent_override`→`hard` always, `boundary`→`medium` always. Grep of `evaluator/local_evaluator.py`, `starter/agent.py`, and `tests/test_evaluator.py` for `category_bucket`/`difficulty_bucket` returns **0 matches** — neither field is read anywhere in the codebase.

---

## 4. Schema / Type Findings — the `details` Dictionary

- **287 distinct keys** observed across all 50,000 `details` dicts. Frequency drops off fast after a short head:
  - `Date First Available` 46,886 (93.77%)
  - `Department` 43,582 (87.16%)
  - `Item model number` 27,729 (55.46%)
  - `Package Dimensions` 27,061 (54.12%)
  - `Manufacturer` 23,512 (47.02%)
  - `Is Discontinued By Manufacturer` 13,070 (26.14%)
  - `Product Dimensions` 10,210 (20.42%)
  - `Item Weight` 3,243 (6.49%)
  - `Color` 2,439 (4.88%)
  - `Brand` 2,328 (4.66%)
  - `Material` 2,069 (4.14%)
  - `Style` 1,752 (3.50%)
  - `Best Sellers Rank` 1,127 (2.25%) — the one key whose value type is `dict`, not `str`
  - `Size` 925 (1.85%)
  - (remaining 273 keys each below 1.5%; full list captured in the underlying audit script output)
- **Brand / Color / Material / Size / Style coverage is low, and mostly non-overlapping across records:**
  - Brand (either `Brand` or `Brand Name`): 2,823 / 50,000 = **5.65%**
  - Color: 2,439 = **4.88%**
  - Material: 2,069 = **4.14%**
  - Style: 1,752 = **3.50%**
  - Size: 925 = **1.85%**
  - **Records with at least one of these five keys: 3,032 / 50,000 = 6.06%.** Roughly **94% of the catalog carries none of Brand/Color/Material/Size/Style as a structured `details` key.**
- **Key-casing inconsistencies** (same concept present under two+ distinct capitalizations, both populated in real data):
  - `Number of Items` (249) / `Number Of Items` (37)
  - `Number Of Pieces` (28) / `Number of Pieces` (80) / `Number of pieces` (30)
  - `Special features` (5) / `Special Features` (105)
  - `Assembly required` (12) / `Assembly Required` (2)
  - `Maximum Weight Recommendation` (2) / `Maximum weight recommendation` (1)
  - `Wheel type` (1) / `Wheel Type` (1)

  No code anywhere normalizes key casing before lookup.
- **Duplicated / fragmented concepts** (same underlying attribute, different key spellings, none unified):
  - Material-related keys: `Material` (2,069), `Material Type` (103), `Outer Material` (120), `Inner Material` (105), `Frame Material` (64), `Handle Material` (45), `Shaft Material` (21), `Tick-repellent material` (15), `Band Material Type` (9), `material_composition` (10, snake_case — the one non-Title-Case key found), `Material Feature` (5), `Cover Material` (3), `Fastener Material` (4), `Grip Material` (2), `Wheel Material` (1), `Sole Material` (1), `Blade Material` (1), `Back Material Type` (1), `Material Composition` (1), `Material free` (1).
  - Size-related keys: `Size` (925), `Screen Size` (5), `Band Size` (9), `Grip Size` (4), `Size Map` (4), `Ring Size` (1), `File size` (1).
  - Color-related keys: `Color` (2,439), `Stone Color` (15), `Band Color` (8), `Color Name` (4), `Lens Color` (3).
  - Style-related keys: `Style` (1,752), `Neck Style` (176), `Collar Style` (25), `Head Style` (2), `Top Style` (1), `Lifestyle` (4).
- **`store` vs. `details.Brand`/`Brand Name` cross-check:** of 2,819 records where both fields are populated strings, **37 (1.31%) disagree** in a case-insensitive string comparison — small and plausibly legitimate (e.g. reseller `store` differs from manufacturer `Brand`), not investigated further.

---

## 5. Data Quality Findings — Documentation vs. Actual Data

| # | Documentation Claim | Observed Fact | Status |
|---|---|---|---|
| 1 | `docs/competition_specification.md:17` — participant-visible catalog fields are `parent_asin, title, features, description, price, categories, details, average_rating, rating_number, store` | Exactly these 10 keys, and only these, appear in 100% of the 50,000 records | **Reconciled — exact match.** |
| 2 | `data/README.md` — "Expected row count: 50,000" | 50,000 confirmed | **Reconciled.** |
| 3 | `data/README.md` — 200 sessions = "80 Buying, 80 Browsing, 30 Intent Override, and 10 Boundary" | Exact match, confirmed by direct count | **Reconciled.** |
| 4 | `data/README.md` — public file omits "hidden intent cards... simulator-policy internals" | 0/200 records contain `intent_card` or `behavior` keys | **Reconciled.** |
| 5 | `README.md` references `organizer/JUDGING_RUNBOOK.md`, `organizer/private_release_checklist.md`, `organizer/JUDGING_DAY_SOP.md` | `organizer/` directory does not exist in this checkout | **Resolved by `.gitignore`** (organizer-only, intentionally excluded) — not a broken reference. |
| 6 | No documentation source mentions `category_bucket` or `difficulty_bucket` | Both fields present in 100% of `public_set.jsonl` records; 0 references in any code file | **Unreconciled — undocumented field.** |
| 7 | Spec's framing of `user_profile.purchase_frequency` implies a genuine per-customer aggregate | All 200 public records carry the identical literal string `"3-4 prior purchases"` — zero variance | **Unreconciled — flagged as a discrepancy, not resolved.** See §8. |
| 8 | `docs/agent_api_contract.json` allows `ask_attribute` values including `material`, `color`, `size`, `style`, `brand` | Catalog `details` carries these five concepts (combined) in only ~6% of products | **Unreconciled — contract/data mismatch flagged, not a bug in either side individually.** See §8. |

---

## 6. Existing Error Handling (what the codebase currently does)

- `normalize_recommendations()` ([evaluator/local_evaluator.py:95-109](../evaluator/local_evaluator.py#L95)): the only genuinely defensive function in the codebase. Handles non-list payload, non-dict/non-string items, blank/whitespace-only IDs, duplicate IDs, and catalog-invalid IDs.
- `evaluate()`'s per-turn loop ([evaluator/local_evaluator.py:239-244](../evaluator/local_evaluator.py#L239)): wraps `agent.respond(...)` in `try/except Exception`, substituting an empty response dict on failure; also checks `isinstance(response, dict)` and `isinstance(response.get("message"), str)` before trusting the response, falling back to empty otherwise.
- `usage` token accounting ([evaluator/local_evaluator.py:245-250](../evaluator/local_evaluator.py#L245)): only accumulates `prompt_tokens`/`completion_tokens` if they are present, `int`, and `>= 0`; silently ignores malformed usage blocks rather than crashing.
- `_text()` / `searchable_text()` / `_flatten_values()`: type-coerce `None`/`dict`/`list`/scalar into strings for indexing and intent-card generation — this is normalization for search purposes, not validation, and never rejects a record.
- `load_jsonl()` ([evaluator/local_evaluator.py:90-92](../evaluator/local_evaluator.py#L90)): skips blank lines when loading `public_set.jsonl`. This same protection is **not** present for `catalog.jsonl` in either `catalog_index()` or `Agent._build_index()`.

Everything else — catalog line parsing, `parent_asin` key access, duplicate detection, hidden-field derivation via `products[target]` — has **no error handling**: any anomaly not already ruled out by the current data (§2, §3) would propagate as an unhandled exception and abort the run.

---

## 7. Confirmed Robust Areas

- `parent_asin`: 100% present, 100% non-empty, 100% correctly typed (`str`), **zero duplicates** across all 50,000 catalog rows.
- `average_rating` and `rating_number`: fully clean — correctly typed, in-range, no negative or invalid values.
- `categories`: never missing or empty; consistent list structure across the entire catalog.
- Public-set `ground_truth.parent_asin`: all 200 verified present in the catalog; all 200 distinct (no target reuse).
- Public-set `scenario_type` distribution: exact match to the documented 40/40/15/5% split.
- `normalize_recommendations()`: correctly handles every malformed-input case tested (see §6).
- Existing unit tests: `python3 -m unittest tests.test_evaluator -v` → all 3 tests pass against the current code and fixtures (`test_normalization_preserves_first_valid_unique_order`, `test_metric_summary_assigns_turn_11_to_miss`, `test_evaluate_derives_hidden_fields_when_public_set_omits_them`).

---

## 8. Potential Risks (plausible, not yet demonstrated as live failures)

1. **Unprotected `json.loads` on both catalog and public-set loaders.** No file catches `JSONDecodeError`. Not currently triggered (0 malformed lines observed in either file), but nothing prevents a future data refresh or the private 800-session holdout from containing one, in which case the entire evaluator run aborts rather than skipping the bad line.
2. **`product["parent_asin"]` bracket access** in both `Agent._build_index` and `catalog_index` — a record missing this key raises `KeyError`, aborting the whole run. Not triggered currently (0 missing observed).
3. **Divergent duplicate-`parent_asin` handling between the two loaders** — `Agent._build_index`'s FTS5 table would keep both rows for a duplicate ID; `catalog_index`'s dict-based index would silently keep only the last one. Not triggered currently (0 duplicates observed), but the two code paths would behave differently if it ever occurred.
4. **Non-numeric `price` strings flow uncensored into `intent_card()`.** [evaluator/local_evaluator.py:62-63](../evaluator/local_evaluator.py#L62): `if product.get("price") not in (None, ""): candidates.append(f"budget around ${product['price']}")`. For the 117 catalog records with string prices, this can generate a nonsensical simulated-customer constraint such as `"budget around $—"`. **Confirmed that none of the current 200 public targets land on one of these 117 products** (checked directly), but the private 800-session holdout has not been (and cannot be, from this repo) checked.
5. **`materialize_hidden_fields()`'s `products[target]` bare dict index** ([evaluator/local_evaluator.py:207-209](../evaluator/local_evaluator.py#L207)) — if a session's `ground_truth.parent_asin` were ever absent from the catalog, this raises `KeyError` and aborts evaluation entirely, rather than being scored as a single miss. Confirmed 0/200 current misses; no defense exists in code.
6. **Sparse structured attributes vs. the agent contract's clarification vocabulary.** `ask_attribute` supports `material`/`color`/`size`/`style`/`brand`, but only ~6% of catalog products carry any of these as a structured `details` key (§4). An agent that asks a clarifying question and then tries to filter on the corresponding structured field will find no signal for ~94% of the catalog; any real signal must come from free-text fields (`title`/`features`/`description`).
7. **`purchase_frequency` constant across all 200 public sessions.** Either genuinely non-varying in the full population, or an artifact of how the public split was sampled/generated. Cannot be resolved without access to the gitignored organizer pipeline (§1.2).
8. **`category_bucket` / `difficulty_bucket` are fully collinear with `scenario_type` and unused by any code.** Likely harmless leftover metadata from generation, but worth confirming it isn't intended to drive some not-yet-implemented scoring or stratification path.
9. **Key-casing/synonym fragmentation in `details`** (§4) — any future normalization step that does exact-key lookups (e.g. `details.get("Material")`) will silently miss `Material Type`, `material_composition`, and the casing-variant keys, undercounting real attribute coverage.
10. **Price field carries three semantically different "missing" encodings** (`null` for 78.95% of records, literal `"—"` string for 112 records, `"from X.XX"` range string for 5 records) with no unified representation — any downstream numeric handling of `price` must account for all three, and currently only the `null` case (`not in (None, "")`) is defended against.

---

## 9. Open Questions Requiring Deeper Investigation

1. Is the upstream catalog/public-set generation pipeline (referenced by the gitignored `organizer/`, `docs/data_selection_audit.md`, `tests/test_5core_builder.py`, `tests/test_organizer_pipeline.py`) available in any other form, so the reason for a constant `purchase_frequency` can be confirmed rather than hypothesized?
2. Do the private 800 holdout sessions share the same guarantees observed here (0 malformed lines, all targets in-catalog, no duplicate targets, no non-numeric-price target collisions), or were those guarantees checked/enforced only for the public 200?
3. Is `category_bucket` / `difficulty_bucket` reserved for a scoring or stratification mechanism not yet implemented in `evaluator/local_evaluator.py`, or is it purely vestigial metadata from the generation pipeline?
4. Should the 117 non-numeric `price` strings and the 1 zero-price record be normalized upstream (e.g. to `null`), given that `"—"` is already semantically "missing" but is currently treated as a truthy, usable string value by `intent_card()`?
5. Given that ~94% of products lack any structured Brand/Color/Material/Size/Style signal, is the intended design for agents to parse free-text fields for these concepts, and should the competition provide guidance/tooling for that — or is the sparse `details` dict the accepted ground truth participants are expected to work around as-is?
6. Should `catalog_index()` and `Agent._build_index()` be reconciled to handle a hypothetical duplicate or missing `parent_asin` identically, given they currently diverge (silent-overwrite vs. keep-both-rows)?

---

## 10. Code Locations Reference (all functions discussed above)

| Function | File : Lines |
|---|---|
| `Agent.__init__`, `Agent._build_index` | [starter/agent.py:38-71](../starter/agent.py#L38) |
| `Agent.reset` | [starter/agent.py:73-75](../starter/agent.py#L73) |
| `Agent.respond` | [starter/agent.py:77-102](../starter/agent.py#L77) |
| `_text` | [starter/agent.py:17-24](../starter/agent.py#L17) |
| `_terms` | [starter/agent.py:27-32](../starter/agent.py#L27) |
| `searchable_text` | [evaluator/local_evaluator.py:27-37](../evaluator/local_evaluator.py#L27) |
| `_flatten_values` | [evaluator/local_evaluator.py:40-45](../evaluator/local_evaluator.py#L40) |
| `_clean_constraint` | [evaluator/local_evaluator.py:48-49](../evaluator/local_evaluator.py#L48) |
| `intent_card` | [evaluator/local_evaluator.py:52-71](../evaluator/local_evaluator.py#L52) |
| `behavior_for` | [evaluator/local_evaluator.py:74-87](../evaluator/local_evaluator.py#L74) |
| `load_jsonl` | [evaluator/local_evaluator.py:90-92](../evaluator/local_evaluator.py#L90) |
| `normalize_recommendations` | [evaluator/local_evaluator.py:95-109](../evaluator/local_evaluator.py#L95) |
| `catalog_index` | [evaluator/local_evaluator.py:112-123](../evaluator/local_evaluator.py#L112) |
| `coarse_category` | [evaluator/local_evaluator.py:126-134](../evaluator/local_evaluator.py#L126) |
| `classify_constraint` | [evaluator/local_evaluator.py:137-151](../evaluator/local_evaluator.py#L137) |
| `initial_message` | [evaluator/local_evaluator.py:154-163](../evaluator/local_evaluator.py#L154) |
| `customer_reply` | [evaluator/local_evaluator.py:166-185](../evaluator/local_evaluator.py#L166) |
| `metric_summary` | [evaluator/local_evaluator.py:188-201](../evaluator/local_evaluator.py#L188) |
| `materialize_hidden_fields` | [evaluator/local_evaluator.py:204-213](../evaluator/local_evaluator.py#L204) |
| `evaluate` | [evaluator/local_evaluator.py:216-295](../evaluator/local_evaluator.py#L216) |
| `main` | [evaluator/local_evaluator.py:298-312](../evaluator/local_evaluator.py#L298) |

---

## 11. Methodology Note

Catalog statistics were produced by a full single-pass scan of all 50,000 lines of `data/catalog.jsonl` (no sampling), tabulating per-field type, presence, emptiness, and value distributions, plus a dedicated pass over every `details` dict to tabulate all 287 observed keys, their frequencies, and their value types. Public-set statistics were produced by a full single-pass scan of all 200 lines of `data/public_set.jsonl`, cross-referenced against the full catalog `parent_asin` set for ground-truth integrity checks. Analysis scripts were run read-only against the repository's actual data files; no repository file was modified in the course of this investigation.
