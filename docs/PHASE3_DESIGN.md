# Phase 3 — Data Error Handling Design (Definitive)

Branch: `dylan-data-error`
Status: **Design only. No source code, tests, datasets, or configuration were modified to produce this document.**
Supersedes: the in-conversation Phase 3 draft and reconciliation passes. Builds on [docs/PHASE1_DATA_AUDIT.md](PHASE1_DATA_AUDIT.md), [docs/PHASE2_FAILURE_AUDIT.md](PHASE2_FAILURE_AUDIT.md), and `docs/CLAUDE_CODE_HANDOFF.md`.

Evidence labels used throughout: **FACT** (measured against the real repo/data), **INFERENCE** (reasoned from FACTs), **RECOMMENDATION** (a design choice, not an organizer rule), **DOCUMENTED BY COMPETITION** (an explicit statement in `docs/competition_specification.md` or sibling docs), **UNSPECIFIED BY COMPETITION** (the documentation is silent — flagged, never silently resolved).

---

## 1. Executive Summary

Phase 1 established, by scanning all 50,000 catalog rows and all 200 public sessions, that the **shipped data is structurally clean** — no malformed JSON, no missing/duplicate `parent_asin`, no session pointing at a non-existent target. Phase 2 then empirically demonstrated, by running the real `starter/agent.py` and `evaluator/local_evaluator.py` against synthetic edge-case fixtures (and, for two findings, against real catalog rows), that the **code has almost no defense** against any of these conditions if they ever occurred: a single malformed catalog line crashes the whole load; a single malformed session crashes the entire evaluation run and **discards every already-scored session in that run**; a handful of real (not synthetic) catalog rows already produce nonsensical customer-facing text due to un-scoped type handling.

This design closes those gaps with the smallest change set that fixes every genuinely demonstrated issue, while explicitly refusing to "clean" the 78.9% null-price, 47.8%-empty-description, sparse-`details` reality of the Amazon metadata — that sparsity is confirmed legitimate and already handled safely today.

**What this design will change:** (1) isolate catalog-row and session-level failures so one bad record can never take down the whole run; (2) draw a hard line between *the evaluator being unable to construct a legitimate test* (excluded from scoring) and *the agent failing a legitimate test* (scored as a documented miss); (3) fix four narrow, evidence-backed derived-data bugs (`categories` string mishandling, nested-dict `details` leaking into customer text, non-numeric `price` producing nonsense budget text, `parent_asin` type/whitespace handling); (4) add a lightweight, structured error report to the existing evaluator output.

**What this design will deliberately NOT change:** retrieval, ranking, BM25 scoring, the metric formulas (`HitRate@10`/`MRR`/`MTTC`/`Efficiency`), conversation-simulation logic beyond the two narrow bug fixes above, the shipped datasets, or `starter/agent.py` (participant-replaceable scaffold — see §14).

---

## 2. Evidence Base

| Finding | Evidence type | Classification |
|---|---|---|
| Catalog: 50,000 rows, 0 malformed JSON, 0 blank lines, 0 non-dict records, 0 missing/duplicate/empty `parent_asin`, 100% string type | **FACT** — full-corpus scan (Phase 1) | CONFIRMED ROBUST (real data) |
| Catalog: `price` null 78.9%, empty `description` 47.8%, empty `features` 10.4%, empty `details` 3.3%, `store` null 0.63% | **FACT** — full-corpus scan (Phase 1) | DATA QUALITY CHARACTERISTIC — not a bug |
| Catalog: 287 distinct `details` keys, casing/synonym fragmentation, Brand/Color/Material/Size/Style combined present in only ~6% of rows | **FACT** — full-corpus scan (Phase 1) | DATA QUALITY CHARACTERISTIC — no code does keyed lookup, so no demonstrated impact |
| `categories`-as-string silently char-split by `catalog_index()` | **FACT** — reproduced against real code with a synthetic fixture; **0 real occurrences** (catalog is 100% list-typed) | CONFIRMED BUG (code-level; dormant on current data) |
| Nested-dict `details` value (e.g. `Best Sellers Rank`) leaks as Python-repr text into simulated customer messages | **FACT** — reproduced against real code; **confirmed live on 19 of the 50,000 real catalog rows**; confirmed 0/200 public targets currently hit it | CONFIRMED BUG (demonstrated on real catalog data, not yet on a scored session) |
| One malformed/unexpected session field crashes `evaluate()` and discards every already-scored session in the run | **FACT** — reproduced 6 ways against real code with synthetic session fixtures; **0/200 real public sessions trigger it** | CONFIRMED BUG (code-level; relevant primarily to the unverifiable private 800) |
| Wrong-type `parent_asin` (`int`/`None`/`list`) silently coerced to a string via `str()` | **FACT** — reproduced against real code; **0 real occurrences** | CONFIRMED BUG (code-level; dormant) |
| `parent_asin` whitespace handling asymmetric between `catalog_index()` and `normalize_recommendations()` | **FACT** — reproduced with a synthetic trailing-space ID; **0 real occurrences** | POTENTIAL ROBUSTNESS RISK (code-level; dormant) |
| Duplicate-`parent_asin` handling diverges between `starter/agent.py` (keeps both, FTS) and `evaluator/local_evaluator.py` (keeps last, dict overwrite) | **FACT** — reproduced with a synthetic duplicate fixture; **0 real occurrences** | CONFIRMED BUG (code-level inconsistency; dormant) |
| Non-numeric `price` string (`"—"`, `"from X.XX"`) produces `"budget around $—"` | **FACT** — reproduced against real code; **confirmed live on 117 of the 50,000 real catalog rows**; confirmed 0/200 public targets currently hit it | POTENTIAL ROBUSTNESS RISK → confirmed live on real catalog data, not yet on a scored session |
| `user_profile` schema (`docs/agent_api_contract.json`) not enforced at runtime | **INFERENCE** — grep-confirmed the baseline `Agent.reset()` never reads `user_profile` at all, so nothing in organizer code can currently break on it | POTENTIAL ROBUSTNESS RISK, but **NOT AN ISSUE for organizer code specifically** — see §14/§19 |
| `average_rating`, `rating_number` type/range | **FACT** — full-corpus scan, 100% valid; grep-confirmed **zero references** anywhere in `starter/` or `evaluator/` | NOT AN ISSUE |
| `agent.reset()` calls are not exception-protected (only `agent.respond()` is) | **FACT** — read directly from `evaluator/local_evaluator.py:228` | CONFIRMED BUG (protection gap, closed by this design's failure-isolation wrapper — §12) |
| `results.json` has no external consumer/schema in this repository | **FACT** — repo-wide grep found only the evaluator's own writer and descriptive prose in `README.md`; `docs/baseline_results.json` is a separate, smaller, hand-maintained file | Resolves an open compatibility question — see §13 |

---

## 3. Complete Issue Inventory

| Issue | Evidence | Type | Priority | Current Behaviour | Proposed Behaviour |
|---|---|---|---|---|---|
| Malformed JSON line (catalog) | Synthetic, code-confirmed | Robustness | SHOULD FIX | Uncaught `JSONDecodeError`, whole catalog load aborts | Skip that line, warn, continue |
| Blank line (catalog) | Synthetic, code-confirmed | Robustness | SHOULD FIX | Uncaught `JSONDecodeError` | Skip silently (matches existing `load_jsonl` convention for sessions) |
| Non-dict record (catalog) | Synthetic, code-confirmed | Robustness | SHOULD FIX | Uncaught `TypeError` | Skip, warn |
| Missing `parent_asin` | Synthetic, code-confirmed; 0 real occurrences | Robustness | SHOULD FIX | Uncaught `KeyError` | Skip record, warn |
| Invalid `parent_asin` type (int/null/list) | Synthetic, code-confirmed; 0 real occurrences | Robustness | SHOULD FIX | Silently coerced via `str()` | Skip record, warn — never coerce |
| Empty `parent_asin` (`""`/whitespace-only) | Not previously tested; reasoned extension of the type-check | Robustness | SHOULD FIX | Currently accepted as-is (no check) | Skip record, warn |
| Whitespace around `parent_asin` | Synthetic, code-confirmed; 0 real occurrences | Robustness | SHOULD FIX | Stored/compared inconsistently (catalog side unstripped, recommendation side stripped) | Strip symmetrically at every read site |
| Duplicate `parent_asin` | Synthetic, code-confirmed; 0 real occurrences | Robustness | SHOULD FIX | Diverges: evaluator keeps last, starter keeps both | Evaluator: keep first, warn, skip subsequent (deterministic) |
| `categories` as a string | Synthetic, code-confirmed; 0 real occurrences | Robustness (confirmed bug) | SHOULD FIX | Silently char-split (`"Clothing"` → `['C','l',...]`) | Normalize: wrap as `[value]` |
| Nested dict inside `details` | **Confirmed live on 19/50,000 real rows** | Robustness (confirmed bug) | SHOULD FIX | Leaks as Python-repr text into simulated customer message | Exclude non-scalar `details` values from constraint-candidate generation only |
| Non-numeric `price` (`"—"`, `"from X.XX"`) | **Confirmed live on 117/50,000 real rows** | Robustness (confirmed bug) | SHOULD FIX | Produces `"budget around $—"` | Treat as unavailable in `intent_card()` only — catalog data untouched |
| Zero price (1 real record) | FACT, Phase 1 | Data quality | DO NOT FIX | Handled identically to any float, no crash, no misleading text | No change |
| Missing/wrong-type `title` | FACT, Phase 1/2 — already safe | Data quality / confirmed robust | DO NOT FIX | Coerced safely, no crash | No change |
| Missing/wrong-type `features` | FACT, Phase 1/2 — already safe | Data quality / confirmed robust | DO NOT FIX | Coerced safely, no crash | No change |
| Missing/wrong-type `description` | FACT, Phase 1/2 — already safe | Data quality / confirmed robust | DO NOT FIX | Coerced safely, no crash | No change |
| Missing/wrong-type `details` (whole field) | FACT, Phase 1/2 — already safe | Data quality / confirmed robust | DO NOT FIX | Coerced safely, no crash | No change |
| `details` key casing/synonym fragmentation | FACT, Phase 1 | Data quality | DO NOT FIX | No code does keyed lookup; not exercised | No change |
| Missing `ground_truth` / malformed (non-dict) `ground_truth` | Synthetic, code-confirmed; 0 real occurrences | Invalid evaluation record | MUST FIX | Uncaught `KeyError`/`TypeError`, crashes whole batch | Isolate: exclude from scoring, record error |
| Missing `ground_truth.parent_asin` | Synthetic, code-confirmed; 0 real occurrences | Invalid evaluation record | MUST FIX | Same | Same |
| `ground_truth.parent_asin` not in catalog | Synthetic, code-confirmed; 0 real occurrences | Invalid evaluation record | MUST FIX | Uncaught `KeyError` from `materialize_hidden_fields()` | Same |
| Missing `user_profile` | Synthetic, code-confirmed; 0 real occurrences | Invalid evaluation record | MUST FIX | Uncaught `KeyError` | Same |
| Malformed `user_profile` (present, wrong-typed sub-fields) | Reasoned; grep-confirmed baseline agent never reads these fields | Not an evaluator-side issue | NICE TO HAVE | No crash in organizer code today | Warn only, pass through — do not skip |
| Missing `scenario_type` | Synthetic, code-confirmed; 0 real occurrences | Invalid evaluation record | MUST FIX | Uncaught `KeyError` | Isolate: exclude, record error |
| Invalid (present) `scenario_type` value | Synthetic, code-confirmed — already correct | Confirmed robust | DO NOT FIX (log only) | Graceful fallback to browsing-like behavior, already correct | No behavior change; add a visibility log line |
| Malformed session occurring after valid sessions | Synthetic, code-confirmed | Robustness (Critical) | **MUST FIX** | Crashes the whole batch, discards prior results | Isolate per-sample; surrounding valid sessions unaffected |
| `agent.reset()` failure | FACT — currently unprotected | Robustness gap | MUST FIX | Uncaught exception, crashes whole batch | Wrap with the same session-execution guard as `respond()`; count as a miss for a **valid** session |
| `agent.respond()` failure | FACT — already protected (ROBUST-4) | Confirmed robust | DO NOT FIX | Caught, substituted with empty response, correctly scored | No change |
| Timeout | Not implemented anywhere in the current evaluator (no wall-clock enforcement exists) | Out of scope | DO NOT FIX (this branch) | N/A — no timeout mechanism exists today | Documented as an explicit non-goal; adding one is a different kind of change than data-error handling |
| Invalid agent output (bad shape from `respond()`) | FACT — already protected (ROBUST-4: `isinstance` checks) | Confirmed robust | DO NOT FIX | Falls back to empty response safely | No change |

Explicit non-modifications and why: every "Data quality" row above is confirmed-safe today (Phase 2 ROBUST-1) and touching it would be exactly the "aggressive cleaning" this branch is scoped to avoid. Timeout handling is excluded because no timeout mechanism exists in the current evaluator at all — building one is a scope expansion beyond "data error handling," not a fix to a demonstrated data-handling defect.

---

## 4. Final Handling Policy

| Issue | Policy | Why |
|---|---|---|
| Malformed JSON / blank / non-dict catalog line | Blank: **ACCEPT** (skip silently); malformed JSON / non-dict: **SKIP RECORD + WARN** | Blank lines are benign formatting; malformed/non-dict lines are real corruption worth a log line, but not worth aborting 49,999 good rows over. |
| Missing / wrong-type / empty `parent_asin` | **SKIP RECORD + WARN** | No usable identity; fabricating one (`str(None)` → `"None"`) is worse than dropping the row — see §8. |
| Whitespace `parent_asin` | **NORMALIZE** (strip) | Unambiguous, lossless, cannot alter which real product an ID refers to. |
| Duplicate `parent_asin` | **WARN + SKIP RECORD** (2nd+ occurrence, keep first) | Deterministic; matches file order; this catalog is already confirmed 1-row-per-`parent_asin`, so a duplicate row is a pipeline defect, not legitimate Amazon variant semantics. |
| `categories` as string | **NORMALIZE** (wrap as `[value]`) + WARN | Preserves the value's meaning; the alternative (silent char-split) actively destroys it. |
| Nested dict in `details` | **SKIP** that value from constraint-candidate generation only + WARN (aggregated) | The value is harmless as raw search text; it is only wrong when rendered as a "constraint." Fixing the narrow function that renders it is the minimum correct scope. |
| Non-numeric `price` string | **NORMALIZE** (treat as unavailable, in `intent_card()` only) | Matches how `None` price is already (correctly) handled; the catalog's `price` field itself is never rewritten. |
| Zero price, sparse text fields, `details` variation | **ACCEPT** | Legitimate data; no downstream defect demonstrated. |
| Missing `ground_truth` / malformed `ground_truth` / missing `ground_truth.parent_asin` / target not in catalog | **SKIP SESSION** (excluded from scoring, recorded as an error) | No legitimate target exists — see §5/§6 for full justification. |
| Missing `user_profile` | **SKIP SESSION** | Evaluator cannot construct a schema-compliant `reset_request` at all (see §6). |
| Malformed (present, wrong-typed) `user_profile` sub-fields | **WARN + ACCEPT** (proceed) | Evaluator *can* still construct a contract-shaped call; any resulting agent crash is then a documented agent miss, not an evaluation-data defect. |
| Missing `scenario_type` | **SKIP SESSION** | Evaluator's simulator has no defined branch for a missing key. |
| Invalid (present) `scenario_type` value | **ACCEPT** (existing fallback) + WARN for visibility | Already graceful and deterministic — a defined design path, not a defect. |
| Malformed session after valid sessions | **SKIP SESSION**, isolate, continue batch | This is the fix for BUG-3 — see §12. |
| `agent.reset()` / `agent.respond()` failure on a **valid** session | **COUNT AS MISS** | Documented: *"Exceptions, invalid output, and timeouts may count as a miss."* |

**No condition in this design defaults to FAIL FAST.** The only two places anything is rejected outright (bad catalog rows, invalid evaluation records) are scoped to the single affected row/session — the surrounding 49,999 rows or 199 sessions are never put at risk.

---

## 5. Session Validity Model

### VALID SCOREABLE SESSION
A session for which the evaluator can (a) establish a real, catalog-resident target `parent_asin`, and (b) construct a schema-compliant `reset_request` and a determinable simulated-conversation policy — i.e., the evaluator can actually execute the documented protocol, whatever the agent subsequently does with it.

### INVALID EVALUATION RECORD
A record for which the evaluator itself cannot legitimately construct the task — independent of any agent's behavior. No agent, however capable, could be fairly tested against it.

### AGENT EXECUTION FAILURE
A **valid** evaluation case where `reset()` raises, `respond()` raises, the agent times out (no mechanism exists for this today — see §3), or the agent returns invalid/malformed output.

```
VALIDATE SESSION
       |
       +---- INVALID EVALUATION RECORD
       |          |  (ground_truth missing/malformed, ground_truth.parent_asin
       |          |   missing or not in catalog, user_profile key missing,
       |          |   scenario_type key missing)
       |          +--> record structured error
       |          +--> isolate (skip this sample only)
       |          +--> EXCLUDED from HitRate@10 / MRR / MTTC denominator
       |
       +---- VALID SESSION
                  |  (target resolvable in catalog, reset_request constructible,
                  |   simulator has a determinable behavior — including the
                  |   graceful fallback for an unrecognized scenario_type VALUE)
                  |
                  +--> execute agent (reset + turn loop)
                         |
                         +--> success → score normally (possible hit or miss on merits)
                         |
                         +--> reset()/respond() exception, invalid output
                              → COUNT AS MISS, included in denominator
```

**Reasoning:** the metric formulas themselves (`HitRate@10 = successful sessions / N`, etc.) are defined in terms of a target's rank — they have no meaning for a session with no target. Scoring an "invalid evaluation record" as a guaranteed miss would not measure anything about agent quality (Phase 2/reconciliation: a non-catalog target *cannot* be recommended by any agent, since `normalize_recommendations()` filters to catalog-resident IDs — such a case is unwinnable by construction, not merely hard). Conversely, a session where the evaluator *did* successfully pose a legitimate task, and the agent failed it, is exactly what `competition_specification.md` already anticipates with "exceptions... may count as a miss" — collapsing these two cases into one bucket would either inflate every team's miss count with organizer data defects, or (worse) let a real agent bug hide as an "excluded" record.

---

## 6. Malformed Session Scoring — Documented vs. Inferred vs. Recommended

| Condition | Label | Basis |
|---|---|---|
| `agent.reset()` or `agent.respond()` raises, or agent returns invalid output, **on an otherwise-valid session** | **DOCUMENTED BY COMPETITION** | `competition_specification.md`: *"Exceptions, invalid output, and timeouts may count as a miss."* Applies to agent-side failures specifically. |
| Metrics (`HitRate@10`, `MRR`, `MTTC`) are defined in terms of "target_rank" / "successful sessions" | **DOCUMENTED BY COMPETITION** (`competition_specification.md`, `docs/evaluation_config.json`) | Direct quote of the formulas. |
| A session with no derivable target cannot be meaningfully scored by those formulas | **INFERRED FROM METRIC/EVALUATOR DESIGN** | The formulas presuppose a target exists; nothing in the docs states this explicitly for the "no target" case. |
| `ground_truth.parent_asin` not present in the catalog → excluded from metrics rather than scored as a miss | **INFERRED FROM METRIC/EVALUATOR DESIGN**, strongly supported | `normalize_recommendations()` filters all recommendations to catalog-resident IDs — no agent could ever hit such a target. Scoring it as a miss would be indistinguishable from a guaranteed zero unrelated to agent skill. |
| Missing `ground_truth` / malformed `ground_truth` / missing `ground_truth.parent_asin` → excluded | **RECOMMENDATION BECAUSE SPECIFICATION IS SILENT** | No explicit organizer text; recommendation follows the same "no legitimate target" logic as the row above. |
| Missing `user_profile` → excluded | **RECOMMENDATION BECAUSE SPECIFICATION IS SILENT** | `docs/agent_api_contract.json` marks `user_profile` **required** in `reset_request` — the evaluator cannot construct a schema-compliant call at all, but the spec doesn't say what to do when it can't. |
| Missing `scenario_type` → excluded | **RECOMMENDATION BECAUSE SPECIFICATION IS SILENT** | Not part of the Agent API contract at all (the agent never sees it) — but the evaluator's own simulator (`initial_message`/`customer_reply`/`behavior_for`) has no defined branch for a missing key. Recommendation, not a documented rule. |
| Wrong-typed (but present) `user_profile` sub-fields → session proceeds normally | **RECOMMENDATION**, supported by code evidence | Grep-confirmed the baseline agent never reads these fields; the evaluator *can* still construct a contract-shaped call. Any resulting agent-side crash is then the documented "exceptions may count as a miss" case, not an evaluation-data defect. |
| Invalid (present) `scenario_type` **value** → session proceeds normally, existing fallback used | **CONFIRMED CORRECT BY EXISTING CODE**, not merely inferred | Phase 2 empirically demonstrated the graceful, deterministic fallback already in `evaluator/local_evaluator.py`. No new decision needed. |

**The line that must never be crossed:** INVALID EVALUATION DATA ≠ AGENT MISS. A session the evaluator could legitimately run, where the agent failed, is scored as a miss — full stop, matching documented behavior. A session the evaluator could never legitimately run in the first place is excluded — it never becomes a miss, because "miss" implies a legitimate attempt occurred.

---

## 7. Catalog Validation Design

```
RAW LINE
  |
  v
JSON PARSE          --catch JSONDecodeError--> SKIP RECORD (warn, unless blank -> accept silently)
  |
  v
RECORD TYPE CHECK    --not a dict-->            SKIP RECORD (warn)
  |
  v
parent_asin CHECK    --missing/wrong-type/empty--> SKIP RECORD (warn)
                     --whitespace-->             NORMALIZE (strip)
                     --duplicate-->              SKIP RECORD (warn, keep first)
  |
  v
SAFE NORMALIZATION   categories: str -> [str] (warn)
                     details: unchanged (dict stays flexible, no schema)
                     price: unchanged at catalog layer (see §11 — fix is in intent_card() only)
                     title/features/description: unchanged (already safe)
  |
  v
INDEX                (unchanged mechanics: FTS5 in starter, dict-based in evaluator)
  |
  v
VALIDATION REPORT     {total_lines, blank_lines, malformed_json, non_dict_records,
                        valid_records, skipped_records, normalized: {...}, warnings: [...]}
```

`catalog.jsonl` itself is never written to. Every action above operates on the in-memory parsed representation only.

---

## 8. `parent_asin` Policy

| Rule | Value |
|---|---|
| Type | Must be `str` after parsing. **Never coerced** from `int`/`None`/`list`/etc. |
| Emptiness | Must be non-empty after stripping whitespace |
| Whitespace | Stripped at every read site: `catalog_index()`, `Agent._build_index()`, and (already true today) `normalize_recommendations()` |
| Duplicates | Keep first occurrence; skip and warn on subsequent ones; same rule applied consistently within the evaluator's own loader |

**Why stricter than optional metadata:** `parent_asin` is the sole scored identity field — `docs/evaluation_config.json`'s `"catalog_id_field": "parent_asin"` and `"exact_match": true` make it the one field where a silently wrong value has a direct, mechanical effect on scoring (a session either finds the exact string or it doesn't). A wrong-typed `title` degrades gracefully into worse search text; a wrong-typed `parent_asin` can make a real product **permanently unreachable** by any correctly-behaving agent, silently, with no warning and no way to reason about it after the fact.

**Competition-scoring implications:** rejecting a catalog row shrinks the catalog by one product. If a private-holdout session's `ground_truth.parent_asin` ever pointed at such a row, this fix causes a miss for that one session — strictly better than today's alternative (the whole catalog fails to load, crashing every session), but not risk-free. **Confirmed 0 real occurrences today**, so this rule changes nothing about the current public 200 or `baseline_results.json`; it is insurance against data not yet seen. This is called out again in §19 (Risks).

---

## 9. `categories` Policy

| Input | Current behavior | New behavior |
|---|---|---|
| `["Clothing", "Men"]` (list) | Stringify each item | **Unchanged** — regression-guarded |
| `"Clothing"` (string) | `catalog_index()`: iterates character-by-character → `['C','l','o',...]` (BUG-1); `starter/agent.py`'s `_text()`: already returns the string unchanged (safe) | `catalog_index()`: wrap as `["Clothing"]` — a single-element list, matching the intended meaning |
| Missing / `None` / other wrong type | `product.get("categories") or []` → `[]` | **Unchanged** — already correct |

**Consistency between evaluator and starter:** the two loaders do not need byte-identical code — they serve different purposes (`catalog_index()` builds a structured `categories: list[str]` used by `coarse_category()`; `starter/agent.py`'s `_text()` builds flat search text for FTS5). What matters is that **neither corrupts the input's meaning**. `starter/agent.py` already meets this bar for a string `categories` value; only `evaluator/local_evaluator.py`'s `catalog_index()` needs the fix. See §14 for why `starter/agent.py` is not otherwise in scope.

---

## 10. `details` Policy

| Value shape | Example | Behavior |
|---|---|---|
| Scalar (`str`, `int`, etc.) | `"Brand": "Acme"` | **Unchanged** — flows through to search text and constraint candidates exactly as today |
| Nested dict | `"Best Sellers Rank": {"Clothing, Shoes & Jewelry": 2082176, ...}` | **Excluded from constraint-candidate generation** (`_flatten_values()`/`intent_card()` only); still harmless, unchanged raw text for full-text search indexing (`_text()`/`searchable_text()`) |
| Nested list (of non-scalars) | Not observed in real data; same treatment as nested dict if it occurred | Excluded from constraint candidates, same reasoning |
| Missing `details` (whole field) | — | **Unchanged** — already falls through safely |
| Empty `details` (`{}`) | — | **Unchanged** — already handled |

**`details` remains a fully flexible, un-schema'd dictionary.** No fixed set of expected keys is introduced; Phase 1 confirmed 287 distinct keys with heavy long-tail variation and casing fragmentation, and Phase 2 confirmed **no code anywhere does a keyed lookup** (`details["Brand"]`) that fragmentation could break. Forcing a schema here would be exactly the "rigid schema onto Amazon metadata" this design is scoped to avoid, for zero demonstrated benefit.

---

## 11. `price` Policy

| Representation | Category | Behavior |
|---|---|---|
| `float`/`int`, including `0` | Valid price | Used as-is in `intent_card()`'s budget constraint — **unchanged** |
| `None` | Unavailable | Already correctly suppressed — **unchanged** |
| `""` (empty string) | Unavailable | Already correctly suppressed by the existing `not in (None, "")` check — **unchanged** |
| `"—"` | Unavailable (upstream's own placeholder convention, not malformed JSON) | **Change:** suppress the budget-constraint candidate, same as `None` |
| `"from 12.99"` | Unavailable for a single-price representation (it's a real number, but represents a range, not a fixed price) | **Change:** suppress, same as `None` — extracting `12.99` and presenting it as *the* price would misrepresent a range |
| Any other non-numeric string | Malformed | Same suppression — fail safe, not fail loud |

**Implementation scope:** the fix is a single added type check (`isinstance(price, (int, float))`) inside `intent_card()` (`evaluator/local_evaluator.py`). **`catalog.jsonl`'s `price` field is never rewritten, normalized, or reinterpreted at the data layer** — this is purely a derived-text generation fix, consistent with the standing instruction to never modify the dataset.

---

## 12. Failure Isolation

**Guarantee:** `Valid A → Valid B → Malformed C → Valid D` results in A, B, D scored, and C recorded as an isolated error — never a loss of A, B, or D.

```
sessions = []
errors = []
for sample in samples:
    try:
        validate_session(sample, catalog_ids)     # structural pre-check only —
    except InvalidSessionError as e:               # ground_truth shape, target-in-catalog,
        errors.append({...})                       # user_profile key present,
        continue                                   # scenario_type key present
    try:
        result = run_session(agent, sample, ...)    # agent.reset() + full turn loop
        sessions.append(result)                     # (agent.respond() already wrapped today)
    except Exception as exc:
        sessions.append(miss_result(sample, reason=str(exc)))  # counted in N, scored as a miss
return {..., "sessions": sessions, "errors": errors}
```

**Both `agent.reset()` and `agent.respond()` are covered.** Phase 2 identified that only `respond()` is currently exception-protected (`evaluator/local_evaluator.py:239-244`); `reset()` (`evaluator/local_evaluator.py:228`) is called with no protection at all today, meaning a `reset()`-raising agent currently crashes the whole batch identically to every other BUG-3 trigger. Wrapping the entire per-sample execution phase (`run_session`, covering both calls) in one try/except closes this gap as a natural consequence of the fix, rather than requiring a separate patch.

**Metrics** (`metric_summary()`, `scenario_metrics`) continue to operate only over `sessions` — `errors` entries never enter the `HitRate@10`/`MRR`/`MTTC` denominator, consistent with §5's validity model.

---

## 13. Error Observability

**Mechanism:** stdlib `logging` (no new dependency) to **stderr**, at WARNING level for normalize/warn actions and ERROR level for skip actions — kept separate from `main()`'s existing stdout JSON print. In parallel, plain `dict` entries (no custom exception hierarchy, no framework) accumulate into two small structures returned alongside existing data:

```python
catalog_report = {
    "total_lines": ..., "blank_lines": ..., "malformed_json": ..., "non_dict_records": ...,
    "valid_records": ..., "skipped_records": ...,
    "warnings": [{"line": 18293, "parent_asin": None, "field": "price",
                   "reason": "non-numeric price treated as unavailable", "action": "normalize"}, ...]
}
errors = [{"sample_id": "public_0047", "field": "ground_truth.parent_asin",
            "reason": "missing required field", "action": "skip_session"}, ...]
```

Every entry answers: **what failed** (`reason`), **where** (`line` or `sample_id`), **which record** (`parent_asin` when known), **why** (`reason`/`field`), **what the evaluator did** (`action`).

**Compatibility with the output contract:** repo-wide grep confirms `results.json` has **no external consumer** in this repository — only `evaluator/local_evaluator.py`'s own `main()` writes it, and only `tests/test_evaluator.py` and this repo's own documentation reference it descriptively. `docs/baseline_results.json` is a separate, smaller, hand-authored reference file, not generated by the same code path, and is unaffected by any change here. Adding `errors` and `catalog_report` keys to `evaluate()`'s return dict (and therefore to `results.json`) introduces no breaking change to any known consumer.

---

## 14. `starter/agent.py` Scope

**Decision: `starter/agent.py` is NOT modified by this design.**

- **What changing it would accomplish:** mirroring the `categories`/`parent_asin` fixes would make the baseline scaffold's own loader equally robust, and would close the DEEP-3 divergence with `evaluator/local_evaluator.py`'s loader.
- **Is it necessary for evaluator robustness?** No. `README.md` and `docs/submission_rules.md` both establish that `starter/agent.py` is explicitly participant-editable/replaceable — every team submits their own `agent.py`. The organizer's scoring path depends only on `evaluator/local_evaluator.py`'s correctness, which this design fully addresses independently.
- **Is it merely consistency/documentation value?** Yes, entirely. Hardening it protects no one's actual competition score, since it's expected to be overwritten.
- **Conclusion:** leaving it unchanged is preferable for this branch's stated scope (data-error handling for the evaluator/competition path). Modifying it is available as a separate, explicitly-optional follow-up if the team wants a more robust baseline example for participants — not part of this design's approved scope (see §20).

---

## 15. Competition Scoring Impact

| Change | Classification | Why |
|---|---|---|
| Session isolation (`validate_session`/`run_session` split, §12) | **No scoring impact on current public 200** (0 malformed sessions exist); robustness-only for the private 800 | Confirmed 0/200 trigger any isolation path today |
| `agent.reset()` exception coverage | **Could affect scoring only if an agent's `reset()` ever throws** — then correctly converts a full-run crash into a single documented miss | Strictly safer than today's crash-everything behavior |
| Catalog row skip rules (malformed JSON/blank/non-dict/`parent_asin` validation/duplicates) | **No scoring impact** — 0 real occurrences in the actual 50,000-row catalog, which is shared (frozen) across public and private evaluation per `README.md` | Confirmed by full-corpus Phase 1 scan |
| `categories` string-wrap | **Could affect simulator behaviour only** (conversation text quality); **no effect on HitRate@10/MRR/MTTC** | `categories` is never the scored field |
| `details` nested-dict exclusion | **Could affect simulator behaviour** for the 19/50,000 exposed real records if one is ever a private-holdout target (0/200 public targets affected today); **no effect on `parent_asin` matching** | Confirmed via full-catalog scan |
| Price normalization in `intent_card()` | **Could affect simulator behaviour** for 117/50,000 records if targeted (0/200 today); **no scoring impact** | `price` is never the scored field |
| `parent_asin` whitespace-strip + duplicate-keep-first | **No scoring impact today** (0 real occurrences); **potentially risky if applied asymmetrically** — must touch every read site in the same change | Flagged explicitly as the one change type that could silently alter a hit/miss outcome if done partially |
| `parent_asin` type/emptiness rejection | **Potentially risky in the abstract** — could make a private-holdout target unreachable if it ever hit a rejected row; **0 real occurrences today** | See §8/§19 |
| Error/report additions to `results.json` | **No scoring impact** — additive only, no existing field removed or renamed, no external consumer exists | See §13 |

**Explicitly not redesigned:** BM25 ranking, `Agent.respond()`'s query construction, `metric_summary()`'s arithmetic, `coarse_category()`, `classify_constraint()`, `customer_reply()`, `behavior_for()`, `initial_message()`. Every fix in this design intercepts bad data **before** it reaches these functions, never inside them.

---

## 16. Test Plan

All new tests live in `tests/test_evaluator.py`, following the existing `unittest` style.

### Catalog
| Test | Proves |
|---|---|
| Valid record loads unchanged | Regression baseline |
| Malformed JSON line skipped, surrounding records still load | Requirement: malformed data doesn't destroy valid records |
| Blank line skipped silently | Same |
| Non-dict record skipped, warned | Same |
| Missing `parent_asin` → record skipped | `parent_asin` identity safety |
| Wrong-type `parent_asin` (int/null/list) → skipped, never coerced | Same |
| Empty `parent_asin` → skipped | Same |
| Whitespace `parent_asin` → stripped, matches a trimmed agent recommendation | Same |
| Duplicate `parent_asin` → first kept, second skipped, deterministic | Same |
| `categories` as list → unchanged (regression) | §9 |
| `categories` as string → wrapped as `[value]`, never char-split | §9 |
| Scalar `details` value → unchanged (regression) | §10 |
| Nested-dict `details` value → excluded from `intent_card()` output, never appears verbatim | §10 |
| Non-numeric price (`"—"`, `"from X.XX"`) → no budget candidate generated | §11 |
| Null price → still correctly suppressed (regression) | §11 |
| Valid numeric price (including `0`) → still correctly included (regression) | §11 |
| Sparse-but-valid metadata (empty `features`/`description`/`details`, missing `title`) → passes through unchanged, no exception, no invented content | **Negative control** — proves legitimate sparsity is untouched |

### Session
| Test | Proves |
|---|---|
| Valid session scores normally | Regression baseline |
| Missing `ground_truth` → excluded, recorded as error | §5/§6 |
| Malformed (non-dict) `ground_truth` → excluded, recorded | Same |
| Missing `ground_truth.parent_asin` → excluded, recorded | Same |
| Target `parent_asin` absent from catalog → excluded, recorded (not scored as a miss) | Same — the "unwinnable by construction" case |
| Missing `user_profile` → excluded, recorded | Same |
| Malformed `user_profile` (present, wrong-typed sub-field) → session still scored normally | §6 — distinguishes structural absence from imperfect-but-present data |
| Missing `scenario_type` → excluded, recorded | §6 |
| Invalid (present) `scenario_type` value → scored normally under existing fallback (regression) | §6 |
| `agent.reset()` raises on a valid session → counted as a miss, included in `N` | §12 — closes the reset() protection gap |
| `agent.respond()` raises on a valid session → counted as a miss (regression — already correct) | §12 |
| Invalid agent output shape → falls back safely (regression — already correct) | §12 |

### Critical regression
```
test_evaluate_isolates_malformed_session_between_valid_sessions:
    samples = [session_A(valid), session_B(valid), session_C(malformed), session_D(valid)]
    result = evaluate(agent, samples, catalog_ids, categories, products)
    assert {s["sample_id"] for s in result["sessions"]} == {"A", "B", "D"}
    assert len(result["errors"]) == 1 and result["errors"][0]["sample_id"] == "C"
```

### All-valid regression
Run the existing 200-session public set (or an equivalent all-valid fixture) through `evaluate()` before and after implementation; assert identical `hit_rate_at_10`, `mrr`, `mttc`, and per-scenario breakdowns. This is the primary guardrail against accidentally changing valid-data behavior.

**Not proposed:** a test per each of the 287 `details` keys, or for `average_rating`/`rating_number` validity — neither is read by any organizer code, so there is nothing to regress.

---

## 17. Files and Implementation Scope

### Files to modify

| File | Functions/areas | Reason |
|---|---|---|
| `evaluator/local_evaluator.py` | `catalog_index()` | Malformed-line tolerance, `parent_asin` validation/strip/duplicate rule, `categories` type guard, `catalog_report` return |
| | `intent_card()` / `_flatten_values()` | Non-scalar `details` exclusion (§10), non-numeric price suppression (§11) |
| | `evaluate()` | Split into `validate_session()` pre-check + `run_session()` execution wrapper (§12); collect `errors`; metrics computed over `sessions` only |
| | New: `validate_session()` helper | Implements the INVALID EVALUATION RECORD gate (§5/§6) |
| `tests/test_evaluator.py` | New tests per §16; minor update to existing `catalog_index()` call-sites if its return shape grows (adding `catalog_report`) | Regression coverage for every fix above |

### Files to add
None. All error-reporting structures are plain dicts returned from existing functions — no new module is needed.

### Files not to modify
- `data/catalog.jsonl`, `data/public_set.jsonl` — never touched; every fix operates on parsed/derived data, never the source files.
- `starter/agent.py` — see §14.
- `docs/competition_specification.md`, `docs/agent_api_contract.json`, `docs/evaluation_config.json`, `docs/submission_rules.md`, `docs/baseline_results.json` — organizer-owned competition rules, out of scope for a code-robustness branch.
- `normalize_recommendations()`, the FTS5/BM25 retrieval and ranking code, `metric_summary()`'s arithmetic, and every simulator prose function not named above (`coarse_category`, `classify_constraint`, `customer_reply`, `behavior_for`, `initial_message`) — confirmed already correct (ROBUST-2/3/4) or out of this fix's narrow scope.

---

## 18. Implementation Order

| Stage | Work | Success criterion |
|---|---|---|
| 1 | `validate_session()` helper + `run_session()` execution wrapper in `evaluate()` | Critical regression test (§16) passes: `[A, B, C(malformed), D]` → A/B/D scored, C isolated |
| 2 | `agent.reset()` brought under the same exception coverage as `agent.respond()` | A synthetic reset()-raising agent no longer crashes the batch; is scored as a miss on a valid session |
| 3 | Catalog line-level tolerance (malformed JSON/blank/non-dict) in `catalog_index()` | Synthetic malformed-line fixture loads surrounding good rows; all-valid regression (§16) unaffected |
| 4 | `parent_asin` validation + whitespace-strip + duplicate rule in `catalog_index()` | Synthetic fixtures for each of missing/wrong-type/empty/whitespace/duplicate behave per §8; all-valid regression unaffected |
| 5 | `categories` string-wrap fix | Synthetic string-`categories` fixture wraps instead of char-splitting; list-input regression unaffected |
| 6 | `_flatten_values()` nested-dict exclusion | The 19 real affected catalog rows no longer produce dict-repr text in `intent_card()` output; scalar-`details` regression unaffected |
| 7 | `intent_card()` price-type guard | The 117 real non-numeric-price rows no longer produce `"budget around $—"`; null/numeric-price regressions unaffected |
| 8 | `catalog_report`/`errors` plumbed into `evaluate()`'s and `main()`'s output | `results.json` contains both new keys; existing keys unchanged in shape |
| 9 | Full test suite (§16) + all-valid regression against the real 200-session public set | `hit_rate_at_10`/`mrr`/`mttc` identical to `docs/baseline_results.json` before/after |

Each stage is independently testable and independently revertible; stage 1–2 alone already resolves the only Critical-severity finding (BUG-3) and the reset() protection gap.

---

## 19. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Accidentally changing scoring on currently-valid data | All-valid regression test (§16, §18 stage 9) run before/after; every fix is gated to conditions confirmed absent from the current 50,000-row catalog and 200-session public set |
| Excluding legitimate sessions as "invalid" | `validate_session()` deliberately checks only **structural presence** (key exists), never value quality — an unrecognized-but-present `scenario_type` or a wrong-typed-but-present `user_profile` sub-field is explicitly NOT excluded (§5/§6) |
| Validation becoming too strict over time (scope creep) | §3/§4 explicitly enumerate every DO NOT FIX item and why; any future addition to `validate_session()`'s checks should require the same evidence bar (demonstrated downstream problem) used here |
| Changing `parent_asin` identity | Whitespace-stripping is the only transformation applied to a value that passes validation; type coercion is explicitly banned (§8) — a `parent_asin` that survives validation is never altered in meaning, only trimmed |
| Reducing candidate/catalog availability | Row-rejection rules (missing/wrong-type/empty `parent_asin`, malformed JSON/non-dict lines) all have 0 real occurrences today — confirmed by full-corpus Phase 1 scan — so no current product is at risk of being dropped |
| Altering simulator behaviour unintentionally | The `categories`/`details`/`price` fixes are scoped to the single function each defect lives in (`catalog_index()`'s list-comprehension, `_flatten_values()`, `intent_card()`'s price check) — no other simulator function is touched |
| Breaking `results.json` compatibility | Confirmed via repo-wide grep: no external consumer exists; only additive keys are introduced (§13) |
| Introducing new starter/evaluator inconsistency | Resolved by explicitly scoping duplicate/type fixes to the evaluator only and treating `starter/agent.py` as out of scope (§14) — no new asymmetry is created because no claim of parity is being made |
| Asymmetric `parent_asin` whitespace fix (fixing one read-site but not all) | Explicitly called out in §8/§15 as the one change requiring simultaneous application across `catalog_index()`, `Agent._build_index()` (if touched), and confirming `normalize_recommendations()`'s existing `.strip()` — implementation must not ship partially |

---

## 20. Final Recommendation

**Smallest comprehensive implementation that addresses the genuine issues without changing valid competition behaviour:** Implementation Order stages 1–2 (§18) — the `validate_session()`/`run_session()` split, covering both `reset()` and `respond()` — is the minimum change that eliminates the only Critical-severity, full-run-blast-radius finding (BUG-3) and its adjacent `reset()` protection gap, with **zero effect on the current public 200 sessions or `baseline_results.json`** (confirmed 0 real triggers today). Stages 3–7 (the four catalog/derived-text fixes) are independent, additive, evidence-backed improvements that can ship in the same PR or be sequenced afterward without any dependency on each other or on stages 1–2.

### Final approved-by-design scope (what Phase 4 implementation should do)
- Split `evaluate()`'s per-sample loop into `validate_session()` (structural INVALID EVALUATION RECORD gate) + `run_session()` (exception-wrapped `reset()` + turn loop, agent failures counted as misses).
- Add malformed-JSON/blank-line/non-dict tolerance to `catalog_index()`.
- Add `parent_asin` validation (type, non-empty, whitespace-strip, deterministic duplicate handling) to `catalog_index()`, applied consistently with the existing strip in `normalize_recommendations()`.
- Fix `categories`-as-string handling in `catalog_index()` (wrap, don't split).
- Exclude non-scalar `details` values from `_flatten_values()`'s constraint-candidate output.
- Suppress non-numeric-string prices in `intent_card()`'s budget-constraint generation.
- Add `catalog_report` and `errors` as new, additive keys to `evaluate()`'s return value and `results.json`.
- Add the full regression suite in §16, including the critical isolation test and the all-valid regression.

### Explicitly out of scope (what Phase 4 must NOT do)
- Modify `data/catalog.jsonl` or `data/public_set.jsonl` in any way.
- Modify `starter/agent.py` (participant-replaceable scaffold — §14).
- Modify BM25/FTS5 retrieval, ranking, or `metric_summary()`'s arithmetic.
- Modify `coarse_category()`, `classify_constraint()`, `customer_reply()`, `behavior_for()`, `initial_message()`.
- Introduce a timeout-enforcement mechanism (none exists today; out of scope for a data-error-handling branch).
- Force `details` into a fixed schema, or "clean" any confirmed-legitimate sparse field (null price, empty description/features/details, `details` key-casing variation).
- Silently decide the "count-as-miss vs. exclude" question for `parent_asin`-type-rejected catalog rows or invalid evaluation records beyond what §5/§6 already specify as the reconciled model — any further scoring-policy question that arises during implementation should be flagged, not assumed.
