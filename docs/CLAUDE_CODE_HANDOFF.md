# HANDOFF: TechJam 2026 — Data Robustness Work (branch: dylan-data-error)

You are picking up work on **winfluencers-techjam**, a submission for **TikTok TechJam 2026, Problem Statement 4 — Shopping Copilot: AI Conversational Search and Recommendations**. This handoff was written from a prior research conversation (not from PHASE1.md/PHASE2.md/PHASE3.md files — those do not exist). Treat everything below as claims to **verify against the actual repository**, not as ground truth. Where this handoff and the repo disagree, the repo and official competition docs win — say so explicitly and explain the discrepancy.

Current branch: `dylan-data-error`. You have repo access. Read before touching anything.

---

## 1. PROJECT CONTEXT

- **Competition**: TikTok TechJam 2026, `TechJam2026/techjam-conversational-search`.
- **Task**: build a Python agent implementing `reset(session_id, user_profile)` and `respond(session_id, user_message, turn, top_k) -> {message, ask_attribute, recommendations, usage}`. The agent must find a customer's hidden target product (`parent_asin`) within **10 conversational turns**, asking clarification questions and returning up to 10 ranked `parent_asin` values per turn.
- **Scoring**: `TechnicalScore = 0.50×HitRate@10 + 0.30×MRR + 0.20×Efficiency`, `Efficiency = clip((11-MTTC)/10, 0, 1)`. Only **exact `parent_asin` string equality** counts as a hit. `MTTC` = mean first-hit turn; a miss = turn 11.
- **Data**: a frozen 50,000-product catalog (`data/catalog.jsonl`) drawn from Amazon Reviews 2023 (McAuley Lab), `Clothing_Shoes_and_Jewelry` category. 200 labeled public sessions (`data/public_set.jsonl`) for local development; 800 additional private sessions held by organizers for final evaluation.
- **Behavior scenarios**: `buying` (40%), `browsing` (40%), `intent_override` (15%), `boundary` (5%).
- **`parent_asin`** is the scoring identity — it's the product-group ID (groups color/size/style variants), distinct from the review-level `asin`. This is what the evaluator compares against.
- **Our broader goal on this branch**: make catalog/session loading and evaluation genuinely robust to real-world data errors, WITHOUT over-engineering, aggressively "cleaning" legitimate Amazon metadata sparsity, or touching retrieval/ranking/conversation logic that's out of scope for this branch.

---

## 2. DATA RESEARCH — CONFIRMED FACTS

Everything below was measured against the **actual uploaded `catalog.jsonl` (50,000 rows) and `public_set.jsonl` (200 rows)** using full-corpus Python analysis, not sampling, in a prior session. I'm giving you exact numerators/denominators. This was a **Phase 1 structural pass only** — no coverage audit (details-key-level), no content-level duplicate/near-duplicate analysis, and no session-dialogue analysis were completed. Don't assume anything beyond what's listed as FACT here; treat the rest as open questions for you to investigate.

### catalog.jsonl — FACT

| Property | Result |
|---|---|
| Row count | 50,000 lines, 0 invalid JSON, 0 blank-line issues encountered |
| Field-set signature | Single uniform signature across all 50,000 rows: `parent_asin, title, features, description, price, categories, details, average_rating, rating_number, store` |
| `parent_asin` | str, 100% present, **50,000 unique values / 50,000 rows** — no duplicate parent_asin strings found |
| `title` | str, 100% present |
| `features` | list[str] always present as a list; **empty list on 5,219/50,000 rows (10.4%)** |
| `description` | list[str] always present as a list; **empty list on 23,887/50,000 rows (47.8%)** |
| `categories` | list[str] always present, **never empty (0/50,000)**; every element across the full corpus checked as `str` type — no dict/non-str elements found |
| `details` | dict, 100% present, **free-form / no fixed key schema**. Small 5-record sample showed keys like `Department`, `Manufacturer`, `Product Dimensions`, `Package Dimensions`, `Item model number`, `Date First Available`, `Is Discontinued By Manufacturer` — **no `Color`/`Size`/`Material`/`Style`/`Brand` seen in that sample**, but this is NOT a full-scale claim (only 5 of 50,000 records inspected) |
| `average_rating` | float, 100% present |
| `rating_number` | int, 100% present |
| `store` | str in 49,686/50,000 (99.37%); `None` in 314/50,000 (0.63%) |
| `price` | **mixed type**: `None` in 39,473/50,000 (78.9%), `float` in 10,410/50,000 (20.8%), `str` in 117/50,000 (0.23%). Of the 117 strings: 112 are the literal placeholder `"—"`, 5 are range strings like `"from 12.99"`. Of the 10,410 floats: min 0.0, max 4119.0, **exactly 1 record has price == 0.0** |

### public_set.jsonl — FACT

| Property | Result |
|---|---|
| Row count | 200 lines, 0 invalid JSON |
| Field-set signature | Single uniform signature: `category_bucket, difficulty_bucket, ground_truth, sample_id, scenario_type, user_profile` |
| `ground_truth` | dict, **always exactly `{"parent_asin": <str>}`** — no other keys ever observed, 100% present |
| `ground_truth.parent_asin` cross-check | **All 200 target values found in catalog.jsonl's parent_asin set (0 missing)**; all 200 are distinct (no target reused across sessions) |
| `user_profile` | dict, uniform 5-key signature 100% of the time: `average_prior_rating (float), preference_tags (list[str]), purchase_frequency (str), rating_style (str), summary (str)` |
| `preference_tags` vocabulary | Closed set of only 9 distinct values: `fit, material, comfort, style, durability, performance, warmth, weather, general shopping` |
| `scenario_type` | Exactly 4 observed values with distribution: **buying=80, browsing=80, intent_override=30, boundary=10** — matches the documented 40/40/15/5 split exactly |
| `category_bucket` | `"clothing"` for all 200 rows |
| `difficulty_bucket` | easy=80, medium=90, hard=30 |
| `sample_id` | all 200 unique |

### ⚠️ Critical structural finding — NOT an error, but a scope gap in what we've seen

`public_set.jsonl` contains **no conversational/dialogue data whatsoever**. I grepped the raw file for `"message"`, `"conversation"`, `"turn"`, `"dialogue"`, `"customer_message"` — **zero occurrences of all five**. The file only holds a static `user_profile` summary + `ground_truth`. This means the actual customer dialogue (opening message, clarification-answer phrasing, per-turn text) is generated **at runtime by a simulator/evaluator component we have not inspected** — referenced in the repo README as `evaluator/local_evaluator.py` plus an unnamed simulator, neither of which we've read. **You must locate and read this code before making any claims about how sessions/turns/errors propagate at runtime** — this is explicitly your first job (Section 5 below).

---

## 3. LEGITIMATE SPARSITY vs. GENUINE ERROR CASES

This distinction matters a lot for scope discipline on this branch.

### Legitimate Amazon metadata sparsity (FACT, confirmed — DO NOT "fix" or aggressively clean these)
- `price = None` on 78.9% of rows — normal for scraped Amazon catalog data, not a bug.
- `features = []` on 10.4% of rows — normal.
- `description = []` on 47.8% of rows — normal.
- `store = None` on 0.63% of rows — normal.
- `details` having no fixed key schema, varying keys/casing across products — normal for this dataset; McAuley Lab docs describe `details` as an open dict of "materials, brand, sizes, etc." with no guaranteed structure.

None of the above should trigger warnings, skips, or "cleaning." They should be treated as expected missingness that downstream retrieval/ranking code handles as **soft signals**, not something this branch fixes.

### Genuine error / robustness cases (confirmed present, need defensive handling)
- **`price` as a non-numeric string**: 117/50,000 rows (0.23%) — confirmed present, confirmed exact values (`"—"` ×112, `"from X.XX"` ×5). A naive `float(row["price"])` will throw on these. This is real and needs handling.
- **`price = 0.0`**: 1 confirmed instance — ambiguous (could be a genuine free/promotional item or a data artifact); needs a policy decision, not a silent assumption either way.

### NOT YET TESTED — genuinely unknown, must be verified by you against the full corpus and/or the repo's own loading code (do not assume either way)
- Malformed JSON lines / blank lines in the catalog or session files: none were observed in Phase 1, but Phase 1 used a permissive loader (`strip()` + skip empty + `json.loads`) rather than the repo's actual production loader. **Check what `evaluator/local_evaluator.py` and any `data/` loading module actually do**, since their behavior on such input is the real question, not what our throwaway analysis script did.
- Non-dict top-level records (e.g., a line that's valid JSON but a list or scalar, not an object): not explicitly checked.
- Empty-string or whitespace-only `parent_asin` values: not explicitly checked (we confirmed uniqueness and 100% str type, not non-emptiness).
- Nested dicts/lists as *values inside* `details` (as opposed to `details` itself, which is always a dict): only 5 sample records were visually inspected, all had flat string values. This was **not scanned at full scale**.
- Duplicate `parent_asin` at a semantic/content level (different rows, same real product) — Phase 1 only confirmed no duplicate parent_asin *strings*. Content-level duplicate/near-duplicate title or description analysis was planned for Phase 3 and was **never executed**.
- Anything about session-level malformed records, since all 200 public sessions turned out well-formed — we have **zero real examples** of a malformed session to test recovery behavior against. The 800 private sessions might contain edge cases we've never seen.
- How the evaluator currently behaves when it hits a bad record/session (does it crash the whole run? skip and continue? no observation of this — this is your Section 5 job).

Do not present any of the "not yet tested" items as confirmed facts. Investigate them in the actual repo/code before designing handling for them.

---

## 4. OUR IDEOLOGY FOR THIS BRANCH

**Goal: robustness, validation, safe normalization, failure isolation, and observability.**
**Not: aggressive data cleaning.**

Concretely:
- Validate inputs; normalize only when the transformation is unambiguous and lossless.
- Warn when something is off but recoverable.
- Skip individual bad *records* (catalog rows) when recovery isn't possible, without aborting the whole load.
- Isolate bad *sessions* so one malformed session doesn't destroy valid results from other sessions in the same evaluation run.
- Preserve useful error context (which record/session, what was wrong, what action was taken) — not just a silent failure or a bare exception.
- Do **not** rewrite retrieval, ranking, or conversation logic. Do **not** change agent behavior just because some inputs look imperfect. Do **not** aggressively "fix" Amazon's inherently sparse metadata (see Section 3).
- Never paper over bad data with fabricated values. Concretely, do NOT: convert `None` to the string `"None"` for `parent_asin`; convert `"Clothing"` (a string where a list was expected) into `["C","l","o",...]`; convert `"—"` price into `"$—"`; or let a raw nested dict leak into customer-facing text.

---

## 5. YOUR FIRST JOB — INVESTIGATE, DO NOT IMPLEMENT

Before writing any code, inspect the actual repository state on `dylan-data-error` and answer these, citing the actual files/functions you read:

1. How is `catalog.jsonl` actually loaded today (which module/function)?
2. How is `public_set.jsonl` actually loaded today?
3. How does the evaluator process sessions — read `evaluator/local_evaluator.py` in full. Does one bad session currently abort the whole run, or is there already some isolation?
4. How do errors currently propagate — exceptions, return codes, logged warnings?
5. What is the exact structure of `results.json` as currently produced? Would adding fields like `errors`, `catalog_report`, `session_report`, `validation_summary` be compatible with whatever consumes `results.json` (organizer scoring script, `docs/agent_api_contract.json`, etc.), or would it break the expected output contract? If incompatible, design a separate reporting mechanism rather than polluting `results.json`.
6. What tests already exist under `tests/`?
7. Does `starter/agent.py` need any changes for this branch, or is it out of scope? (It's participant-editable scaffold — don't touch it for cosmetic reasons alone; only if there's real value tied to this branch's robustness goal.)
8. Do the evaluator's and starter's data-loading behaviors differ from each other?
9. Read `README.md`, `DATA_ATTRIBUTION.md`, `docs/competition_specification.md`, `docs/agent_api_contract.json`, `docs/evaluation_config.json`, and `docs/submission_rules.md` for the official word on how malformed input should be scored/handled.

Do not implement anything in this step.

---

## 6. UNRESOLVED QUESTIONS YOU MUST VERIFY (do not invent answers)

**Q1 — Malformed session scoring policy.** If a session is malformed, should it be skipped, counted as a miss, excluded from the denominator, or produce an explicit error record? Check the actual competition documentation and evaluator contract for an official answer before choosing a policy. Do not invent one.

**Q2 — `results.json` output contract.** Verify whether new fields are safe to add, or whether a separate report file/log is the right mechanism. Compatibility with the real judging pipeline matters more than our preference.

**Q3 — `starter/agent.py` necessity.** Determine, from the actual repo, whether this branch has any real reason to touch it.

---

## 7. PROPOSED ROBUSTNESS AREAS TO DESIGN (after investigation, before implementation)

**Catalog-side**: malformed JSON, blank lines, non-dict records, missing/invalid-type/empty/whitespace `parent_asin`, duplicate `parent_asin`, `categories` provided as a string instead of a list, nested dicts appearing inside `details` values, non-numeric price, zero price, missing or wrong-type `title`/`features`/`description`/`details`.

**Session-side**: missing `ground_truth`, malformed `ground_truth`, missing `ground_truth.parent_asin`, target `parent_asin` absent from the catalog, missing/malformed `user_profile`, missing/invalid `scenario_type`, a malformed session occurring *after* otherwise-valid sessions in the file.

**Evaluation-side**: one bad session must not silently or catastrophically destroy valid results from other sessions in the same run; every error needs a clear record/session identity attached; every error should state what happened and what action was taken (skip / normalize / warn / count-as-miss), not just a stack trace.

For each issue you design handling for, classify it as: **ACCEPT / NORMALIZE / WARN / SKIP RECORD / SKIP SESSION / COUNT AS MISS / FAIL FAST** — and justify the choice. Do not default to FAIL FAST. Do not silently coerce values in ways that hide the original problem (see the explicit anti-patterns in Section 4).

Classify each issue's priority as: **Critical / High / Lower priority / Not actually a problem (legitimate sparse metadata)** — using Section 3's FACT/legitimate-vs-error distinction as your evidence base, not intuition.

---

## 8. TESTING

At minimum, cover: malformed JSON, blank line, non-dict catalog record, missing `parent_asin`, invalid `parent_asin` type, empty `parent_asin`, whitespace-only `parent_asin`, duplicate `parent_asin`, `categories` as a string, nested `details` values, invalid price representations, valid-but-sparse metadata (should pass through untouched — this is the negative-control case), missing `ground_truth`, missing `ground_truth.parent_asin`, target not in catalog, malformed `user_profile`, missing `scenario_type`, invalid `scenario_type`, and a malformed session appearing after valid ones.

Specifically include a test proving:
**VALID SESSION → VALID SESSION → MALFORMED SESSION → VALID SESSION** results in the malformed session being isolated/handled per the chosen policy, WITHOUT losing or corrupting the results already computed for the surrounding valid sessions.

---

## 9. STRICT WORKFLOW — DO NOT SKIP STEPS

1. Read this handoff.
2. Inspect the actual repository (Section 5).
3. Verify every claim in this handoff against actual code/docs — flag anything that doesn't match.
4. Identify discrepancies between this handoff and the repository/competition documentation. The repository and official docs are the authority, not this handoff.
5. Produce a final implementation plan.
6. Identify exact files/functions to change.
7. Identify tests to add.
8. Identify competition-scoring risks introduced by the proposed changes.
9. **STOP.**

**Do not implement. Do not commit. Do not modify files in this pass.** After investigation, report back:

A. What you confirmed
B. What differed from this handoff
C. Complete data-error issue inventory (from repo evidence, not assumption)
D. Proposed handling for every issue (with ACCEPT/NORMALIZE/WARN/SKIP/etc. classification)
E. Exact files/functions to change
F. Test plan
G. Competition scoring risks
H. Final recommended implementation order

Then write exactly:

**"PHASE 3 HANDOFF VERIFIED — WAITING FOR USER APPROVAL BEFORE IMPLEMENTATION."**

And stop there.
