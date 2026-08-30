# Phase 4 — Implementation Report (Post-Implementation Verification)

Branch: `dylan-data-error`
Status: Implementation complete and verified. This is a **verification report**, not a design document — every number below was produced by actually running the current repository's code, not recalled from a prior plan.

Source material: the prior phase's data audit, failure audit, and design documents (since superseded and removed from the repo), the final `evaluator/local_evaluator.py` and `tests/test_evaluator.py`, and freshly re-run test/evaluation output (re-verified at the time this report was written, not reused from earlier in the conversation).

---

## 1. Executive Summary

This branch exists because Phase 1's full-corpus audit (50,000 catalog rows, 200 public sessions) and Phase 2's empirical failure audit established that, while the shipped competition data is structurally clean, the evaluator code had almost no defense against malformed input: a single bad catalog line could abort the entire 50,000-row load, and a single malformed session could crash the whole evaluation run and **discard every already-scored session in that run** (Phase 2, "BUG-3"). Phase 3 approved a specific, narrow remediation: validate and isolate failures at both the catalog-row and session level, distinguish an evaluator-side inability to construct a legitimate test (excluded from scoring) from an agent-side failure on a legitimate test (scored as a documented miss), and fix four narrow derived-data defects (`categories` string handling, nested `details` dict leakage, non-numeric `price` handling, `parent_asin` type/whitespace/duplicate handling) — without touching retrieval, ranking, metric formulas, or the datasets themselves.

Phase 4 implemented exactly that scope in `evaluator/local_evaluator.py`, added 35 new regression tests to `tests/test_evaluator.py`, and verified the result two ways: (1) a full regression suite (38/38 tests passing) proving every identified failure mode is now handled safely, and (2) a full re-run of the real 200-session public evaluation against the real 50,000-row catalog, producing metrics **identical** to `docs/baseline_results.json`.

**This branch is about evaluation robustness and reliability, not retrieval or ranking quality.** No BM25/FTS5 logic, no ranking formula, no metric arithmetic, and no conversation-simulation function outside the four approved fixes was touched.

---

## 2. Implementation Scope (Verified Against Git)

```
$ git diff --stat
 evaluator/local_evaluator.py | 301 +++++++++++++++++++++++++++++---------
 tests/test_evaluator.py      | 335 ++++++++++++++++++++++++++++++++++++++++++-
 2 files changed, 570 insertions(+), 66 deletions(-)
```

Only these two files were modified. Confirmed by `git status`/`git diff --stat` at the time of this report — no other path appears in the diff.

**Explicitly NOT modified** (confirmed by the same `git diff --stat` showing no other files):
- `data/catalog.jsonl`, `data/public_set.jsonl` — the datasets themselves
- `starter/agent.py` — participant-replaceable scaffold, out of scope per `docs/PHASE3_DESIGN.md` §14
- `docs/competition_specification.md`, `docs/agent_api_contract.json`, `docs/evaluation_config.json`, `docs/submission_rules.md`, `docs/baseline_results.json` — competition documentation/configuration
- BM25/FTS5 retrieval logic (lives only in `starter/agent.py`, untouched)
- `metric_summary()`'s arithmetic (unchanged; see §11 for the one adjacent guard that *was* added)
- `coarse_category()`, `classify_constraint()`, `customer_reply()`, `behavior_for()`, `initial_message()` — conversation-simulation logic, unchanged

---

## 3. Phase 3 → Phase 4 Traceability

| Phase 3 Requirement | Implementation | File/Function | Verification | Status |
|---|---|---|---|---|
| Session validity gate (INVALID EVALUATION RECORD) | `validate_session()` checks `ground_truth` shape, target-in-catalog, `user_profile`/`scenario_type` key presence | `evaluator/local_evaluator.py:306-331` | `SessionValidationTest` (9 tests) | Done |
| Per-session failure isolation | `evaluate()`'s loop wraps validation and execution in separate try/except blocks per sample; a failure never propagates past the current sample | `evaluator/local_evaluator.py:400-467` (`evaluate()`) | `FailureIsolationTest.test_malformed_session_between_valid_sessions_does_not_destroy_results` | Done |
| `reset()` protection gap closed | `run_session()` calls `agent.reset()` with no internal try/except, so a raised exception propagates to `evaluate()`'s outer wrapper and is scored as a miss, not a crash | `evaluator/local_evaluator.py:334-397` (`run_session()`), `evaluator/local_evaluator.py:427-442` (calling `try/except`) | `FailureIsolationTest.test_reset_failure_on_valid_session_counts_as_miss` | Done |
| Malformed catalog-line tolerance | `catalog_index()` catches `JSONDecodeError`, skips blank lines silently, rejects non-dict records | `evaluator/local_evaluator.py:126-199` | `CatalogValidationTest` (malformed JSON, blank line, non-dict tests) | Done |
| `parent_asin` validation (type/non-empty) | Rejects non-string or empty `parent_asin` after `.strip()`; never coerces via `str()` | `evaluator/local_evaluator.py:171-176` | `test_missing_parent_asin_is_skipped`, `test_wrong_type_parent_asin_is_rejected_not_coerced`, `test_empty_parent_asin_is_rejected` | Done |
| `parent_asin` whitespace normalization | `.strip()` applied at catalog load, and `.strip()` in `materialize_hidden_fields()`/`run_session()`/`validate_session()` for the session-side target | `evaluator/local_evaluator.py:172`, `:283`, `:322`, `:345` | `test_whitespace_parent_asin_is_stripped` | Done |
| Duplicate `parent_asin` handling | Keep first occurrence, skip and count subsequent duplicates | `evaluator/local_evaluator.py:178-182` | `test_duplicate_parent_asin_keeps_first_occurrence` | Done |
| `categories` string normalization | Wrapped as a single-element list instead of iterated character-by-character | `evaluator/local_evaluator.py:184-192` | `test_categories_string_is_wrapped_not_split`, `test_categories_list_unchanged` (regression) | Done |
| Nested `details` exclusion from constraint candidates | `_flatten_values()` excludes any `dict`/`list`-typed value from both its dict and list branches | `evaluator/local_evaluator.py:42-54` | `test_nested_dict_details_value_excluded_from_candidates`, `test_scalar_details_value_unchanged` (regression) | Done |
| Non-numeric price suppression | `intent_card()` only appends a budget candidate when `isinstance(price, (int, float))` | `evaluator/local_evaluator.py:71-77` | `test_em_dash_price_produces_no_budget_candidate`, `test_from_range_price_produces_no_budget_candidate_and_is_not_rewritten`, `test_null_price_still_suppressed`, `test_numeric_price_still_included`, `test_zero_price_still_included` | Done |
| `catalog_report` observability | `catalog_index()` returns a 4th value: a report dict with counts and a `warnings` list | `evaluator/local_evaluator.py:126-141`, merged into `results.json` at `:477-479` (`main()`) | Verified in the live public-set run (§8) | Done |
| `errors` observability | `evaluate()` returns an `errors` list of `{sample_id, field, reason, action}` for every excluded session | `evaluator/local_evaluator.py:407-425, 466` | `FailureIsolationTest` (multiple), verified `[]` on the real 200-session run | Done |
| Regression testing | 35 new tests across 4 test classes | `tests/test_evaluator.py` | `python3 -m unittest tests.test_evaluator -v` → 38/38 pass | Done |

---

## 4. Implementation Details

### Catalog (`catalog_index()`, `evaluator/local_evaluator.py:126-199`)

- **Malformed JSON**: each line is parsed inside `try/except json.JSONDecodeError`; a failure increments `report["malformed_json"]` and `report["skipped_records"]`, logs a warning, and `continue`s to the next line — the surrounding valid lines still load.
- **Blank lines**: `if not line.strip(): report["blank_lines"] += 1; continue` — counted, never warned (this is benign formatting, matching how `load_jsonl()` already treated blank lines for the public set).
- **Non-dict records**: `isinstance(product, dict)` check after successful JSON parsing; a JSON array/string/number line is skipped and warned.
- **`parent_asin` validation**: `raw_parent_asin.strip() if isinstance(raw_parent_asin, str) else ""` — a non-string value (int/`None`/list/etc.) or an empty/whitespace-only string both resolve to `""` and the record is skipped. **Never coerced via `str()`.**
- **Whitespace normalization**: the same `.strip()` above is the only transformation applied to a `parent_asin` that passes validation — its meaning is never altered, only trimmed.
- **Duplicate handling**: `if parent_asin in identifiers: ... continue` — the first occurrence in file order wins; every subsequent duplicate is skipped and counted in `report["normalized"]["duplicate_parent_asin_skipped"]`.
- **`categories` string normalization**: `isinstance(raw_categories, list)` → unchanged; `isinstance(raw_categories, str)` → wrapped as `[raw_categories]` (not iterated character-by-character); anything else → `[]`.

### Details / Intent Card (`_flatten_values()`, `intent_card()`, `evaluator/local_evaluator.py:42-85`)

- **Scalar vs. nested details**: `_flatten_values()`'s dict branch now excludes any `item` that `isinstance(item, (dict, list))` — a scalar value like `{"Brand": "Acme"}` still produces `"Brand: Acme"`; a nested value like `{"Best Sellers Rank": {"Clothing": 12345}}` is excluded entirely from the returned list.
- **Why excluded, not flattened**: `docs/PHASE3_DESIGN.md` §10 explicitly rejected building bespoke per-key flattening logic for an open-ended, 287-key, no-fixed-schema field — exclusion from constraint-candidate generation is the minimum change that prevents a raw Python dict repr from becoming customer-facing text. The same nested value remains **unchanged** in `searchable_text()`'s full-text corpus, where it is harmless.
- **Numeric vs. non-numeric price**: `intent_card()` now requires `isinstance(price, (int, float))` before appending a `"budget around $..."` candidate. `None`, `""`, `"—"`, and `"from 12.99"` are all treated identically — no budget candidate is generated for any of them.
- **Preservation of original catalog values**: `product["price"]` itself is never written to or reinterpreted — the check only gates whether `intent_card()` appends a *derived* text candidate. `test_from_range_price_produces_no_budget_candidate_and_is_not_rewritten` explicitly asserts `row["price"] == "from 12.99"` after calling `intent_card()`.

### Sessions (`validate_session()`, `run_session()`, `evaluate()`, `evaluator/local_evaluator.py:292-467`)

- **`validate_session(sample, catalog_ids)`**: a structural pre-check only — it verifies presence/shape (`ground_truth` is a dict, its `parent_asin` is a non-empty string present in `catalog_ids`, `user_profile` and `scenario_type` keys exist) and deliberately does **not** check value quality (a wrong-typed `user_profile` sub-field or an unrecognized `scenario_type` value both pass). Raises `InvalidSessionError(field, reason)` otherwise.
- **`InvalidSessionError`**: a small exception carrying `field` and `reason`, used only to signal "the evaluator cannot legitimately construct this test," never to signal an agent problem.
- **`run_session(agent, sample, catalog_ids, categories, products)`**: extracted from the original `evaluate()` loop body unchanged in logic (same turn loop, same override handling, same per-turn `agent.respond()` protection) except that `agent.reset()` is now called with **no internal try/except**, so a `reset()` failure propagates out of `run_session()` rather than being silently absorbed or crashing the batch.
- **`evaluate()`**: for each sample, first calls `validate_session()` inside its own `try/except InvalidSessionError` — on failure, appends to `errors` and `continue`s (never reaches `run_session()`). Otherwise calls `run_session()` inside a second `try/except Exception` — on failure (including a `reset()` exception), builds a synthetic miss result (`hit: False`, `first_hit_turn: None`, `reciprocal_rank: 0.0`) and appends it to `sessions`, so it **is** counted in the metrics denominator.
- **Distinction enforced in code, not just naming**: an `InvalidSessionError` never reaches `sessions` (excluded from `N`); any other exception during `run_session()` always reaches `sessions` as a miss (included in `N`). This is the literal implementation of Phase 3's INVALID EVALUATION RECORD vs. AGENT EXECUTION FAILURE distinction — not a renamed version of the old behavior.

---

## 5. Error Handling Semantics (Final Policy, Unchanged from Phase 3)

Quoting the approved model from `docs/PHASE3_DESIGN.md` §5 without reinterpretation:

> **VALID SCOREABLE SESSION**: A session for which the evaluator can (a) establish a real, catalog-resident target `parent_asin`, and (b) construct a schema-compliant `reset_request` and a determinable simulated-conversation policy.
>
> **INVALID EVALUATION RECORD**: A record for which the evaluator itself cannot legitimately construct the task — independent of any agent's behavior.
>
> **AGENT EXECUTION FAILURE**: A valid evaluation case where `reset()` raises, `respond()` raises, or the agent returns invalid output.

Implemented exactly as designed:
- **Invalid evaluation records are isolated and recorded** — they land in `evaluate()`'s `errors` list with `{sample_id, field, reason, action: "skip_session"}`, and are excluded from `sessions`, and therefore from `HitRate@10`/`MRR`/`MTTC`'s denominator.
- **Valid sessions where the agent fails remain scoreable as misses** — a `reset()`/`respond()` exception, or `respond()` returning malformed output, on an already-validated session results in a `hit: False` entry that **is** included in `sessions` and counted in `N`.
- **One malformed session does not terminate the remaining evaluation** — every sample is processed inside the same `for` loop with per-sample exception boundaries; nothing about one sample's outcome affects the control flow for any other sample. Verified directly by `test_malformed_session_between_valid_sessions_does_not_destroy_results`.

---

## 6. Tests (Verified by Running the Suite)

```
$ python3 -m unittest tests.test_evaluator -v
...
----------------------------------------------------------------------
Ran 38 tests in 0.033s

OK
```

**38 tests total: 3 pre-existing + 35 new, 38/38 passed.** (Re-run at the time of writing this report, not carried over from an earlier claim.)

| Category | Test class | Count | What it proves |
|---|---|---|---|
| Pre-existing (unmodified logic, only `catalog_index()` call-site updated for the new 4-tuple) | `EvaluatorTest` | 3 | Normalization ordering, `metric_summary()` miss-turn arithmetic, hidden-field derivation — all still pass unchanged |
| Catalog validation | `CatalogValidationTest` | 12 | Malformed JSON, blank lines, non-dict records, missing/wrong-type/empty/whitespace/duplicate `parent_asin`, `categories` list vs. string, sparse-but-valid metadata passes through untouched |
| Price/details in `intent_card()` | `IntentCardPriceAndDetailsTest` | 7 | Nested-dict exclusion, scalar-details regression, `"—"`/`"from X.XX"`/`None` price suppression, numeric/zero price inclusion regression |
| Session validation | `SessionValidationTest` | 9 | Every `validate_session()` accept/reject branch, including the two "must still pass" cases (malformed-but-present `user_profile`, invalid-but-present `scenario_type`) |
| Failure isolation | `FailureIsolationTest` | 7 | Invalid-record exclusion, target-not-in-catalog exclusion, `reset()`/`respond()`/invalid-output failures counted as misses, the critical isolation test, and the all-valid regression |

**Critical regression test** (`FailureIsolationTest.test_malformed_session_between_valid_sessions_does_not_destroy_results`):

```
samples = [valid_A, valid_B, malformed_C(ground_truth={}), valid_D]
result = evaluate(AlwaysHitsAgent(), samples, ids, categories, products)
```

Expected and actual behavior (test passed): `result["sessions"]` contains exactly `{A, B, D}`, all three hit; `result["errors"]` contains exactly one entry for `C` with `action: "skip_session"`. **A and B are not lost, D is still reached** — the exact failure mode Phase 2 identified (BUG-3) is now demonstrably closed.

---

## 7. Public Data Regression

Command run (default arguments — the real catalog and real public set):
```
python3 -m evaluator.local_evaluator --output /tmp/results_phase4_verify.json
```

Verified output (re-run fresh for this report):

```json
{
  "sample_count": 200,
  "hit_rate_at_10": 0.125,
  "mrr": 0.068034,
  "mttc": 9.81,
  "efficiency": 0.119,
  "recommended_technical_score": 0.10671
}
```

Scenario metrics:

| Scenario | sample_count | hit_rate_at_10 | mrr | mttc |
|---|---:|---:|---:|---:|
| Boundary | 10 | 0.0 | 0.0 | 11.0 |
| Browsing | 80 | 0.025 | 0.004514 | 10.75 |
| Buying | 80 | 0.2375 | 0.126508 | 8.625 |
| Intent Override | 30 | 0.133333 | 0.104167 | 10.066667 |

Before/after comparison:

| Metric | Baseline | After | Result |
|---|---:|---:|---|
| HitRate@10 | 0.125 | 0.125 | MATCH |
| MRR | 0.068034 | 0.068034 | MATCH |
| MTTC | 9.81 | 9.81 | MATCH |
| Efficiency | 0.119 | 0.119 | MATCH |
| Technical Score | 0.10671 | 0.10671 | MATCH |
| Sample Count | 200 | 200 | MATCH |

**Important clarification: the evaluator itself does not read `docs/baseline_results.json` and has no built-in baseline-comparison or regression gate.** The before/after comparison above was performed **manually**, with a separate one-off Python script run after the evaluator produced its output, that loaded both `docs/baseline_results.json` and the freshly produced results file and compared fields for equality. This is not a claim about a feature of the codebase — it is a description of how this verification step was carried out.

---

## 8. Real Data Validation

`catalog_report` from the live run against the real `data/catalog.jsonl`:

```json
{
  "total_lines": 50000,
  "blank_lines": 0,
  "malformed_json": 0,
  "non_dict_records": 0,
  "valid_records": 50000,
  "skipped_records": 0,
  "normalized": {"categories_wrapped_as_list": 0, "duplicate_parent_asin_skipped": 0},
  "warnings": []
}
```

`errors` from the live run against the real 200-session `data/public_set.jsonl`: `[]`.

**Significance:** the real public dataset does not contain any of the malformed conditions this branch defends against — this matches Phase 1's original finding exactly (0 malformed/duplicate/missing `parent_asin`, 0 malformed sessions). Consequently, **the new failure-handling code paths were validated primarily through the 35 dedicated regression tests using synthetic fixtures (§6)**, while **the clean-data, no-regression guarantee was validated through the full 200-session public evaluation (§7)**. These are two different, complementary claims — the public run proves nothing was broken for valid data; the regression tests prove the new handling actually works for invalid data, none of which currently exists in the real files.

---

## 9. Robustness Results

Unchanged scoring on clean data is necessary but not sufficient evidence of success on its own — the second, equally important result is that previously-crashing or silently-corrupting conditions are now demonstrably handled.

| Area | Previous Risk (Phase 2) | New Behaviour | Verification |
|---|---|---|---|
| Malformed catalog records (bad JSON, blank, non-dict) | Uncaught exception aborted the entire 50,000-row load | Skipped individually, counted, warned; load continues | `CatalogValidationTest` (3 tests) |
| Malformed sessions | Uncaught exception aborted the entire evaluation run, discarding all prior results | Isolated per-sample; excluded from scoring, recorded in `errors`; batch continues | `FailureIsolationTest.test_malformed_session_between_valid_sessions_does_not_destroy_results` |
| `reset()` failures | Uncaught exception, same full-batch crash as above (protection gap identified in Phase 2) | Counted as a miss on a valid session; batch continues | `test_reset_failure_on_valid_session_counts_as_miss` |
| `parent_asin` issues (missing/wrong-type/empty/whitespace/duplicate) | Missing/wrong-type silently coerced via `str()` or crashed; whitespace handled asymmetrically; duplicates handled inconsistently | Non-string/empty rejected (never coerced); whitespace stripped symmetrically; duplicates deterministically keep-first | `CatalogValidationTest` (5 tests) |
| `categories` string typing | Silently iterated character-by-character (`"Clothing"` → `['C','l',...]`) | Wrapped as a single-element list | `test_categories_string_is_wrapped_not_split` |
| Nested `details` values | Raw Python dict repr could leak into simulated customer text | Excluded from constraint candidates; scalar values unaffected | `test_nested_dict_details_value_excluded_from_candidates` |
| Invalid price representations | `"—"`/`"from X.XX"` produced nonsensical `"budget around $—"` text | Suppressed identically to `None`; catalog value itself untouched | `IntentCardPriceAndDetailsTest` (5 tests) |
| Error observability | No exception carried record identity; no way to know what happened or why | `catalog_report` (line/field/reason/action) and `errors` (sample_id/field/reason/action) returned in `results.json` | Verified present (and empty, correctly) in the live public run (§8) |
| Evaluation continuation | One failure = zero results for the entire run | Every valid sample is scored regardless of any other sample's outcome | `test_all_valid_sessions_score_identically_to_before`, critical isolation test |

---

## 10. Data Integrity

- **`data/catalog.jsonl` was not modified** — confirmed by `git diff --stat` showing no changes under `data/`.
- **`data/public_set.jsonl` was not modified** — same confirmation.
- **Source metadata values are preserved.** No fix in this implementation rewrites a catalog record's field value. The `categories`-string fix produces a new *in-memory* list wrapping the original string; the original `product["categories"]` value inside `products[parent_asin]` is left as parsed. The nested-`details` fix only changes what `_flatten_values()` returns for constraint-candidate purposes — the `details` dict itself is untouched.
- **Non-numeric prices are suppressed only for budget-constraint generation** — verified directly by `test_from_range_price_produces_no_budget_candidate_and_is_not_rewritten`, which asserts the catalog row's `price` field is still the literal string `"from 12.99"` after `intent_card()` runs.
- **Legitimate sparse metadata remains valid** — `test_sparse_valid_metadata_passes_through_unchanged` asserts that empty `features`/`description`/`details` and a `None` `store` pass through `catalog_index()` completely unaltered, with zero warnings generated.

The implementation handles data safely **at consumption/validation boundaries** — the point where a value is read and used — rather than by rewriting the source dataset. This matches the standing instruction across all four phases: never modify `catalog.jsonl` or `public_set.jsonl`.

---

## 11. Deviations from Phase 3

Two deviations were made, both disclosed in the Phase 4 completion report and reconfirmed here against the actual diff:

1. **`mttc is None` guard before computing `efficiency` in `evaluate()`** (`evaluator/local_evaluator.py:448-450`). This was not explicit in `docs/PHASE3_DESIGN.md`. **Why it became reachable:** before this branch, `sessions` could only be empty if the input `samples` list itself was empty (in which case the original crash-prone `float(overall["mttc"])` was never actually exercised in practice by any real caller). Once `validate_session()` can exclude samples, `sessions` can now be empty even when `samples` is not — e.g. if every sample in a batch were invalid — which would make `metric_summary()` return `mttc: None` and the original unguarded `float(None)` would raise `TypeError`. **Why the guard was necessary:** without it, a batch consisting entirely of invalid sessions would crash `evaluate()` at the metrics-computation stage instead of cleanly reporting zero scored sessions and a full `errors` list. **Why it does not alter normal valid-data behaviour:** the guard only activates when `mttc is None`, which only happens when `sessions` is empty; for every batch with at least one scored session (including the entire real 200-session public set, confirmed 0 excluded), `mttc` is a number and the guard is a no-op — the arithmetic is byte-identical to before.

2. **`validate_session()`/`run_session()` reject a non-string `ground_truth.parent_asin` rather than silently coercing it via `str()`**, which is what the original code did (`target = str(sample["ground_truth"]["parent_asin"])`). This is treated as a **direct extension of the already-approved `parent_asin` non-coercion policy** (`docs/PHASE3_DESIGN.md` §8, "Never coerced from `int`/`None`/`list`/etc.") applied at the point where a target ID is read from session data, rather than a new, independently-invented rule. It has zero effect on the real public 200 (Phase 1 confirmed all 200 `ground_truth.parent_asin` values are strings) and was exercised only by `SessionValidationTest.test_missing_target_rejected`.

No other deviation was found on a direct re-comparison of the final diff against `docs/PHASE3_DESIGN.md`'s §3/§4/§7-§13 specifications — every other implemented behavior matches the design document's stated policy without alteration.

---

## 12. Unresolved Issues / Limitations

- **The private 800-session holdout is unavailable for direct testing.** All session-level failure-handling behavior (invalid-record exclusion, agent-failure-as-miss) was validated against synthetic fixtures in `FailureIsolationTest`/`SessionValidationTest`, not against real private data, because that data does not exist in this repository. Whether the private set contains any condition this branch defends against remains **unverifiable from participant data**, consistent with every prior phase's framing.
- **Some malformed-session and malformed-catalog test cases are synthetic regression fixtures**, not observed failures in the real 50,000-row catalog or 200-session public set — both were independently reconfirmed clean in this report's own §8. The tests demonstrate the code's behavior under conditions that do not currently occur in the shipped data.
- **Competition policy is explicitly silent on certain invalid-evaluation-record cases.** As documented in `docs/PHASE3_DESIGN.md` §6, the exclusion of missing/malformed `ground_truth`, missing `user_profile`, and missing `scenario_type` from scoring is labeled `RECOMMENDATION BECAUSE SPECIFICATION IS SILENT` — these are engineering judgment calls consistent with the metrics' own structure, not organizer-documented rules. Only the "target not in catalog → exclude" case and "agent exception → miss" case have direct textual/structural support from the competition documentation.
- **The competition-scoring implications of rejecting a malformed `parent_asin` catalog row cannot be empirically tested without private data.** If a future or private catalog ever contained such a row, and a session's target happened to point at it, rejecting that row would make the target unreachable — this was flagged as a known, accepted tradeoff in `docs/PHASE3_DESIGN.md` §8/§19, not something Phase 4 could test end-to-end, since 0 such rows exist in the real catalog.

None of these limitations were treated as blocking — they are the same limitations `docs/PHASE3_DESIGN.md` already disclosed before implementation began, restated here rather than newly discovered.

---

## 13. Final Assessment

**Did Phase 4 accomplish its approved objective? Yes.**

- **Implementation completeness:** every item in the Phase 3 → Phase 4 traceability table (§3) is implemented and independently verified.
- **Test coverage:** 38/38 tests pass, including the specific critical regression Phase 2/3 identified as the highest-priority fix.
- **Failure isolation:** demonstrated directly — a malformed session between two valid ones no longer destroys them, and no longer prevents a fourth valid session from being reached.
- **Error observability:** `catalog_report` and `errors` are present in the evaluator's output and populated correctly (empty on clean real data, populated correctly in synthetic-failure tests).
- **Data integrity:** neither dataset file was modified; source field values are preserved; the four derived-data fixes operate only at the point of use.
- **Public regression:** `HitRate@10`, `MRR`, `MTTC`, `Efficiency`, `TechnicalScore`, and `sample_count` are all identical to `docs/baseline_results.json` on the real 200-session public set.
- **Scoring stability:** confirmed byte-for-byte identical, not merely "close."
- **Remaining limitations:** disclosed in §12, all pre-existing and expected, none newly discovered by implementation.

**UNCHANGED SCORE ≠ FAILED IMPLEMENTATION.** For this branch specifically, an unchanged public-set score is exactly the expected and desired outcome: the goal was never to improve `HitRate@10`/`MRR`/`MTTC` — it was to make the evaluator survive conditions it previously could not survive, without altering how it scores conditions it already handled correctly. The identical metrics in §7 are evidence that the robustness work achieved its goal cleanly, not evidence that nothing happened — the 570 lines of diff and 35 new passing tests are the evidence of what happened.

---

## 14. Recommended Next Step

Data-error handling on this branch is complete to the scope approved in `docs/PHASE3_DESIGN.md`. This work is ready to be considered done for its stated purpose, and the project can move to a separate research area.

Future work on **retrieval, BM25 field weighting/combinations, query construction, or ranking** — aimed at improving `HitRate@10`, `MRR`, or `MTTC` — is a distinct objective from this branch's scope (evaluation reliability, not evaluation outcome) and should be pursued as its own, separately-scoped effort rather than mixed into further changes on `dylan-data-error`.
