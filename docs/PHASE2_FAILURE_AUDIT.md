# Phase 2 — Deep Failure / Data Error Handling Audit

Branch: `dylan-data-error`
Builds on: [docs/PHASE1_DATA_AUDIT.md](PHASE1_DATA_AUDIT.md)
Scope: investigation only. No source code, tests, datasets, or configuration were modified. No commits were made.

**Methodology:** every claim below was produced by actually importing `starter.agent` and `evaluator.local_evaluator` (the real repo modules, unmodified) and running them, in-process, against small synthetic JSONL files crafted in a scratch temp directory outside the repo. This is empirical evidence (an observed exception type/message, or an observed return value), not speculation. Where a condition was also checked against the real 50,000-record catalog or the real 200-session public set, that is stated explicitly. Findings about the private 800-session holdout are explicitly marked **UNVERIFIABLE FROM PARTICIPANT DATA** — they are reasoned from code, never asserted as observed.

Classification legend used in every finding: **CONFIRMED BUG**, **CONFIRMED ROBUST**, **DATA QUALITY ISSUE**, **POTENTIAL RISK**, **NO ISSUE**.

---

## Executive Summary

The current implementation has exactly **one** deliberately defensive function in the whole pipeline — `normalize_recommendations()` — which correctly sanitizes the untrusted, agent-authored `recommendations` payload. Everything upstream of it (catalog line parsing, `parent_asin`/`ground_truth`/`user_profile` field access, hidden-field derivation) uses **bare dictionary/key access with no validation, and no exception handling**, which was empirically confirmed to convert *any* of the following into a hard crash of the entire process: a malformed catalog JSON line, a blank catalog line, a non-dict catalog line, a missing `parent_asin`, a missing `ground_truth.parent_asin`, a `ground_truth.parent_asin` absent from the catalog, a missing `user_profile`, or a missing `scenario_type`. Critically, this crash is not scoped to the one bad record — **it aborts `evaluate()` mid-loop, discarding every already-scored session in that run**, because `sessions` is a local list that is only returned after the loop completes normally.

Two genuine **silent-corruption bugs** (not crashes) were also empirically demonstrated: (1) `evaluator.local_evaluator.catalog_index()` iterates a wrong-typed `categories` string *character-by-character* if it is ever a string instead of a list, silently producing garbage category text (`"Clothing"` → `['C','l','o','t','h','i','n','g']`) — the parallel code path in `starter/agent.py` handles the identical input safely, so the two loaders are demonstrably inconsistent; and (2) a `details` value that is itself a nested dict (real example: `Best Sellers Rank`, present in 1,127 real catalog records, 2.25%) can surface as a raw Python-dict-repr string inside a simulated customer's clarification reply — confirmed to actually happen for 19 real catalog products today (not merely hypothetical), though none of the current 200 public targets happen to be among them.

Neither of the current 200 public sessions triggers any of the crash conditions above (Phase 1 already confirmed the public set is clean), so **the current public-set score (`baseline_results.json`) is not at risk**. The risk is entirely forward-looking: to the private 800-session holdout (which cannot be inspected from this repo) and to any future catalog refresh. The evaluator currently has **no defense whatsoever** against a single bad record in either file, and no test in `tests/test_evaluator.py` exercises any of these conditions.

---

## 1. Confirmed Bugs

### BUG-1 — `categories` as a wrong-typed string is silently char-split by `catalog_index()`, inconsistent with `starter/agent.py`

- **Data condition:** `categories` field is a `str` instead of a `list` (not observed in current catalog — Phase 1 confirmed 100% list type — but no type check enforces this).
- **Evidence (empirical):**
  ```
  categories as STRING 'Clothing' -> stored categories list: ['C', 'l', 'o', 't', 'h', 'i', 'n', 'g']
  coarse_category() on that garbled list: 'n g'
  starter._text() on the SAME string value (no char-split): 'Clothing'
  ```
- **Code location:** [evaluator/local_evaluator.py:121](../evaluator/local_evaluator.py#L121) — `categories[parent_asin] = [str(value) for value in product.get("categories") or []]`. A truthy string is iterable in Python, so `for value in "Clothing"` yields one character per iteration.
- **Current behaviour:** No exception. `categories` for that product becomes a list of single characters, silently, with no warning anywhere.
- **Failure mode:** **Silent data loss / incorrect retrieval input.** `coarse_category()` ([evaluator/local_evaluator.py:126-134](../evaluator/local_evaluator.py#L126)) consumes this garbled list to build the customer's opening message (e.g. `"I'm looking for {category}..."`) — the resulting category phrase becomes nonsense (`'n g'` in the test), which would visibly degrade the simulated conversation quality for that session without ever raising an error.
- **Impact:** Retrieval/conversation quality for the affected session only; not a crash; does not corrupt other sessions.
- **Severity:** Low (0 real occurrences currently; the two loaders already disagree, which is itself worth fixing for consistency even absent a live trigger).
- **Priority:** SHOULD FIX (cheap: an `isinstance(..., list)` guard before the comprehension; also worth reconciling with `starter/agent.py`'s already-safe `_text()` behaviour for the same input).

### BUG-2 — Nested-dict `details` values render as raw Python dict-repr text inside simulated customer messages

- **Data condition:** a `details` value is itself a `dict` (real example: `Best Sellers Rank`, present in 1,127 / 50,000 = 2.25% of the real catalog per Phase 1).
- **Evidence (empirical, against the REAL catalog, not synthetic data):**
  - 1,127 real catalog records have a nested-dict `details` value.
  - Of those, **19 records** (0.038% of the full catalog) have the stringified dict actually land inside the visible `hard_constraints`/`soft_preferences` slice returned by `intent_card()` (cut off at `cleaned[:2]` / `cleaned[2:4]`). Example (`B01HLOZ42G`):
    ```
    ['Item Weight: 0.96 ounces', 'Department: Womens', 'Item model number: Mikey Store',
     "Best Sellers Rank: {'Clothing, Shoes & Jewelry': 4079232, \"Women's Coin Purses & Pouches\": 3021}"]
    ```
  - Verified that **none of the current 200 public-set targets** are among the 19 exposed records (checked directly), so this has not yet altered `baseline_results.json`.
- **Code location:** `_flatten_values()` ([evaluator/local_evaluator.py:40-45](../evaluator/local_evaluator.py#L40)), consumed by `intent_card()` ([evaluator/local_evaluator.py:52-71](../evaluator/local_evaluator.py#L52)). The dict branch does `f"{key}: {item}"` with no check that `item` itself is a scalar.
- **Current behaviour:** No exception. The literal Python `repr()` of a nested dict becomes part of a `hard_constraint`/`soft_preference`, then potentially part of `customer_reply()`'s natural-language-styled response text ([evaluator/local_evaluator.py:166-185](../evaluator/local_evaluator.py#L166)).
- **Failure mode:** **Incorrect / non-conversational evaluation input.** A simulated customer would say something like *"For that, what matters is: Best Sellers Rank: {'Clothing, Shoes & Jewelry': 2082176, 'Women's Wrist Watches': 7345}."* — clearly not natural language, and liable to confuse any agent (participant or graders) reading transcripts. This affects the fairness/realism of the simulated conversation for the ~0.04% of catalog products where it surfaces, not the scoring mechanics (the target `parent_asin` matching itself is unaffected).
- **Impact:** Conversation realism / qualitative session quality, not hit-rate correctness; currently latent for the public 200, live risk for the private 800 (UNVERIFIABLE FROM PARTICIPANT DATA — 19/50,000 ≈ 0.038% per-session probability of exposure, so an expected ~0.3 sessions among 800 if the holdout is a similar random sample).
- **Severity:** Low.
- **Priority:** SHOULD FIX (cheap: skip or flatten non-scalar `details` values in `_flatten_values()`).

### BUG-3 — One malformed/unexpected public session aborts the *entire* evaluation run and discards all previously-scored sessions

- **Data condition:** any of: missing `ground_truth.parent_asin` key, `ground_truth.parent_asin` value not present in the catalog, malformed (non-dict) `ground_truth`, missing `user_profile` key, missing `scenario_type` key. (Confirmed clean for all 200 current public sessions per Phase 1 — this is a forward-looking / private-holdout risk.)
- **Evidence (empirical, running the real `evaluate()` against a 2-sample batch: one valid sample followed by one broken sample):**
  ```
  missing ground_truth.parent_asin      -> KeyError: 'parent_asin'
  ground_truth.parent_asin not in catalog -> KeyError: 'NOT_REAL_ASIN'
  missing user_profile                  -> KeyError: 'user_profile'
  missing scenario_type                 -> KeyError: 'scenario_type'
  ground_truth = None                   -> TypeError: 'NoneType' object is not subscriptable
  ground_truth = "B0000000" (string)    -> TypeError: string indices must be integers
  ground_truth = ["B0000000"] (list)    -> TypeError: list indices must be integers or slices, not str
  ```
  In every case, the exception propagated out of `evaluate()` uncaught. Because `sessions: list[dict]` ([evaluator/local_evaluator.py:223](../evaluator/local_evaluator.py#L223)) only gets returned at the very end of the function ([evaluator/local_evaluator.py:278-295](../evaluator/local_evaluator.py#L278)), **the previously-completed, correctly-scored session for the good sample in the same batch is discarded along with the crash** — confirmed by running `evaluate(DummyAgent(), [good_sample, bad_sample], ...)` and observing the whole call raise with zero results returned.
- **Code locations:**
  - `target = str(sample["ground_truth"]["parent_asin"])` — [evaluator/local_evaluator.py:229](../evaluator/local_evaluator.py#L229)
  - `agent.reset(session_id, sample["user_profile"])` — [evaluator/local_evaluator.py:228](../evaluator/local_evaluator.py#L228)
  - `products[target]` inside `materialize_hidden_fields()` — [evaluator/local_evaluator.py:207-209](../evaluator/local_evaluator.py#L207)
  - `sample["scenario_type"]` used throughout `behavior_for()`/`initial_message()`/`customer_reply()`, first bare-accessed at [evaluator/local_evaluator.py:234](../evaluator/local_evaluator.py#L234)
- **Current behaviour:** Total, unhandled crash of `evaluate()` and therefore of `main()` (`python3 -m evaluator.local_evaluator`) — no partial `results.json` is written (the `Path(args.output).write_text(...)` call at [evaluator/local_evaluator.py:307](../evaluator/local_evaluator.py#L307) never executes).
- **Failure mode:** **Crash + silent loss of all otherwise-valid scoring in the same run.** This is qualitatively different from the deliberate, narrow `try/except Exception` already wrapped around `agent.respond(...)` ([evaluator/local_evaluator.py:239-244](../evaluator/local_evaluator.py#L239)), which correctly isolates one bad *agent turn* without losing the rest of the session — no equivalent isolation exists at the *session* level.
- **Impact:** If this were to occur during official/final scoring against the private 800, a single malformed session (whether from an upstream data issue or an edge case the organizer's generation pipeline didn't anticipate) would zero out the *entire* competition run, not just that one session's contribution to `HitRate@10`/`MRR`/`MTTC`. This is the single highest-blast-radius failure mode found in this audit.
- **Severity:** **Critical** (blast radius = 100% of a scoring run; competition-outcome-affecting if it ever fires against the private holdout).
- **Priority:** **MUST FIX** (wrap each sample's processing in `evaluate()`'s loop in its own `try/except`, treating a broken sample as a miss for that sample while continuing the batch — mirroring the isolation pattern already used for `agent.respond()`).

### BUG-4 — Wrong-typed `parent_asin` is silently coerced to a nonsense-but-valid-looking string identity, not rejected

- **Data condition:** `parent_asin` present but of type `int`, `null`, or `list` (not observed in real catalog — Phase 1 confirmed 100% non-empty `str` — code-level latent risk only).
- **Evidence (empirical):**
  ```
  parent_asin=int(12345)  -> catalog_index OK, resulting key = '12345'
  parent_asin=null(None)  -> catalog_index OK, resulting key = 'None'
  parent_asin=list(['x']) -> catalog_index OK, resulting key = "['x']"
  ```
- **Code location:** `str(product["parent_asin"])` — [evaluator/local_evaluator.py:119](../evaluator/local_evaluator.py#L119) and [starter/agent.py:57](../starter/agent.py#L57) (same pattern in both).
- **Current behaviour:** No exception; the record is indexed under a syntactically valid but semantically meaningless ID (e.g. the literal string `"None"`).
- **Failure mode:** **Silent identity corruption**, not a crash. If it ever occurred, that catalog record would become effectively unreachable by any correctly-behaving agent (which would never guess `"None"` or `"['x']"` as a `parent_asin`), while still consuming a slot in the catalog and search index. It would not corrupt any *other* record.
- **Impact:** Retrievability of the single affected record only; not currently triggered.
- **Severity:** Medium (silent and undetectable if it occurred, but zero real occurrences and low likelihood given upstream ASINs are a controlled Amazon identifier format).
- **Priority:** SHOULD FIX (an explicit `isinstance(value, str) and value.strip()` check would let a validation pass flag this instead of silently absorbing it) — **not urgent given 0 real occurrences.**

---

## 2. Confirmed Robust Areas

### ROBUST-1 — Missing/wrong-type `title`, `features`, `description`, `details` do not crash indexing or intent-card generation

- **Evidence (empirical):** all of `title_missing`, `title_wrong_type_list`, `title_empty`, `features_missing`, `features_wrong_type_dict`, `features_wrong_type_string`, `description_missing`, `description_wrong_type_dict` ran through both `_text()` (starter) and `intent_card()` (evaluator) with **zero exceptions**, producing coerced (sometimes ugly, e.g. `"['not', 'a', 'string']"` for a list-typed title) but always-a-string output.
- **Code locations:** `_text()` ([starter/agent.py:17-24](../starter/agent.py#L17)), `searchable_text()`/`_flatten_values()` ([evaluator/local_evaluator.py:27-45](../evaluator/local_evaluator.py#L27)) — all three explicitly branch on `dict`/`list`/else and always fall through to `str(value)`, which cannot raise for any JSON-representable type.
- **Classification: CONFIRMED ROBUST** for crash-avoidance. (The *quality* of the coerced text for wrong-typed fields — e.g. a title rendered as a Python list literal — is a separate, minor cosmetic concern, not a crash risk; see DATA-QUALITY section.)

### ROBUST-2 — `normalize_recommendations()` is genuinely defensive

- Already exercised by the existing unit test `test_normalization_preserves_first_valid_unique_order`; independently re-confirmed to correctly handle non-list payloads, non-dict items, blank IDs, duplicate IDs, and catalog-invalid IDs. **CONFIRMED ROBUST.**

### ROBUST-3 — Invalid/unrecognized `scenario_type` value (not missing key, but an unexpected value) degrades gracefully

- **Evidence (empirical):** running `evaluate()` with a sample whose `scenario_type` was the string `"not_a_real_scenario"` (key present, value invalid) produced **no exception** — `initial_message()`, `customer_reply()`, and `behavior_for()` each have an `if/elif ... else` structure that falls through to a generic/browsing-like default for any unrecognized scenario string, and the session is grouped under its own key in `scenario_metrics` rather than being dropped.
- **Code locations:** [evaluator/local_evaluator.py:74-87](../evaluator/local_evaluator.py#L74), [154-163](../evaluator/local_evaluator.py#L154), [166-185](../evaluator/local_evaluator.py#L166).
- **Classification: CONFIRMED ROBUST.** This is the one "unexpected value" condition in the audit that degrades safely rather than crashing — contrast with a *missing* `scenario_type` key (BUG-3), which does crash.

### ROBUST-4 — Per-turn agent failures are isolated correctly

- `evaluate()`'s inner loop already wraps `agent.respond(...)` in `try/except Exception`, and independently validates the returned shape (`isinstance(response, dict)`, `isinstance(response.get("message"), str)`) before trusting it, substituting a safe empty response otherwise ([evaluator/local_evaluator.py:239-244](../evaluator/local_evaluator.py#L239)). **CONFIRMED ROBUST** — this is exactly the pattern BUG-3 recommends extending to the per-*sample* level.

### ROBUST-5 — `average_rating` / `rating_number` are inert in current code

- Grep-confirmed **zero references** to `average_rating` or `rating_number` anywhere in `starter/agent.py` or `evaluator/local_evaluator.py`. Whatever their type/validity, they cannot currently cause a failure because nothing reads them. **NO ISSUE** for the organizer-provided code (participants' own agents may use them and would need their own validation, but that is out of scope for this repo).

---

## 3. Data Quality Issues (imperfect but valid; not code bugs)

- **DQ-1:** 78.95% `null` price, 47.77% empty description, 10.44% empty features, 3.34% empty details, 0.63% null store, 2 empty titles — all structurally valid per-JSON-Schema-type, consistently typed, and safely handled by the existing coercion functions (ROBUST-1). This is genuine upstream sparsity in Amazon product metadata, not corruption (per Phase 1 §2).
- **DQ-2:** 117 `price` values are non-numeric strings (`"—"` ×112, `"from X.XX"` ×5) and 1 record has `price == 0.0`. These are legitimate alternate representations of "price unavailable" / "price varies by variant" from the upstream source, not JSON errors — Phase 1's question of whether these are "errors" is resolved here: **they are valid, parseable data that the current code does not treat as equivalent to `null`.** See RISK-1 below for the resulting behavioural consequence.
- **DQ-3:** 287 distinct `details` keys with casing inconsistencies (`Number of Items` / `Number Of Items`, etc.) and fragmented synonyms (19 material-related key spellings) are genuine upstream Amazon metadata variation, not something the current code mishandles — no code path currently attempts canonical-key lookups (e.g. `details["Material"]`) that this fragmentation would break silently. Grep-confirmed: **no code in `starter/` or `evaluator/` does a direct keyed lookup like `details.get("Brand")`** — all `details` access goes through the generic flattening functions, which are casing/key-agnostic (they iterate all keys regardless of name). So the casing fragmentation is a data quality observation with **no current code impact** — it would only matter if a future feature added attribute-specific lookups.
- **DQ-4:** Wrong-typed values coerced through `str()` (e.g. a list-typed title rendering as `"['not', 'a', 'string']"`) are cosmetically poor but not observed in the real catalog and not a crash risk — kept distinct from BUG-1/BUG-2 because those two produce actually-wrong *data* (char-split categories, dict-repr constraints) rather than just an ugly-but-faithful string rendering.

---

## 4. Potential Risks (code appears vulnerable; not demonstrated on current data)

### RISK-1 — Non-numeric `price` strings produce a nonsensical "budget" constraint, but do not crash

- **Evidence (empirical):**
  ```
  price='—'            -> ['budget around $—']
  price='from 12.99'   -> ['budget around $from 12.99']
  price=None           -> []                (correctly suppressed)
  price=0               -> ['budget around $0']
  price=19.99           -> ['budget around $19.99']
  ```
- **Code location:** [evaluator/local_evaluator.py:62-63](../evaluator/local_evaluator.py#L62) — `if product.get("price") not in (None, ""): candidates.append(f"budget around ${product['price']}")`.
- **Verified against real data:** none of the current 200 public targets have a non-numeric-string price (Phase 1 + Phase 2 cross-check), so this has not altered `baseline_results.json`. 117/50,000 = 0.234% of the catalog is exposed; if the private 800 sessions sample targets similarly, an expected ~1.9 sessions could be affected — **UNVERIFIABLE FROM PARTICIPANT DATA.**
- **Impact if triggered:** a simulated customer message would contain the literal text `"budget around $—"` — non-crashing but nonsensical, and (per `classify_constraint()`, [evaluator/local_evaluator.py:137-151](../evaluator/local_evaluator.py#L137)) the substring `"budget"` still causes it to be correctly *classified* as a budget-type constraint even though the number is missing, so an agent asking for `ask_attribute: budget` would receive this un-actionable text as its "answer."
- **Severity:** Low. **Priority:** SHOULD FIX if easy (treat non-numeric price strings equivalently to `None` in `intent_card()`), but not urgent — no observed impact on the graded public set.

### RISK-2 — `parent_asin` whitespace handling is asymmetric between catalog indexing and recommendation validation

- **Evidence (empirical, synthetic):** a catalog `parent_asin` of `"B000WS001 "` (trailing space, hypothetical — Phase 1 confirmed 0 such cases in real data) is stored verbatim (with the space) in `catalog_ids`. An agent returning the "clean" ID `"B000WS001"` (no space) is `.strip()`ped by `normalize_recommendations()` ([evaluator/local_evaluator.py:102](../evaluator/local_evaluator.py#L102)) to `"B000WS001"`, which is then checked against `catalog_ids` and **rejected as invalid** because the set contains the space-suffixed version. Confirmed by direct test: `normalize_recommendations([{"parent_asin": "B000WS001"}], {"B000WS001 "})` → `[]`.
- **Code locations:** [evaluator/local_evaluator.py:113-123](../evaluator/local_evaluator.py#L112) (`catalog_index`, no `.strip()`) vs. [evaluator/local_evaluator.py:102](../evaluator/local_evaluator.py#L102) (`normalize_recommendations`, does `.strip()`).
- **Impact if triggered:** a semantically-correct hit would be scored as a miss, purely due to whitespace asymmetry between the two code paths — this would silently and unfairly lower a participant's `HitRate@10`/`MRR` for that product with no diagnostic trail.
- **Severity:** Low given 0 real occurrences (Phase 1 confirmed 100% clean `parent_asin` strings), but structurally a real inconsistency worth closing before trusting an unseen private catalog.
- **Priority:** SHOULD FIX (`.strip()` `parent_asin` symmetrically at catalog-load time, or document that catalog IDs are guaranteed whitespace-free).

### RISK-3 — No runtime enforcement of `docs/agent_api_contract.json` for `user_profile` shape

- The JSON Schema in `docs/agent_api_contract.json` declares `user_profile` as `additionalProperties: false` with 5 required, typed fields. **No code in `evaluator/local_evaluator.py` validates a loaded sample's `user_profile` against this schema** — it is descriptive documentation only. The organizer-provided baseline `Agent.reset()` happens to ignore `user_profile` entirely ([starter/agent.py:73-75](../starter/agent.py#L73)), so a malformed `user_profile` currently cannot crash the *baseline* agent — but a participant's smarter agent that trusts the contract (e.g. calls `.get("preference_tags")` assuming a list) would be exposed to whatever the private-set generation pipeline actually produces, with no schema gate in front of it.
- **Impact:** Not currently demonstrable against participant-visible data (public set is 100% schema-conformant per Phase 1). **UNVERIFIABLE FROM PARTICIPANT DATA** for the private 800.
- **Severity:** Low/Medium (affects participant agents, not the organizer scoring path directly, but a schema violation in the private set would be indistinguishable from a participant agent bug when a team's score is unexpectedly low).
- **Priority:** SHOULD FIX at the evaluator level (validate `user_profile` shape once at load time and fail loudly/specifically, rather than let a violation surface as a confusing downstream `AttributeError` inside someone's agent).

### RISK-4 — `details`-as-list (wrong type) does not crash, but loses the "key: value" structure that dict-typed `details` provides

- **Evidence (empirical):** `details = ["Brand: Acme", "Color: Blue"]` (wrong type) still produces usable, if differently-shaped, constraint candidates via `_flatten_values()`'s list branch — **no crash**, but the `Best Sellers Rank`-style key/value semantics is lost (every list item is treated as an opaque pre-formatted string). Not observed in real data (Phase 1: `details` is 100% `dict` type, 0 wrong-type records).
- **Severity:** Low (0 real occurrences). **Priority:** DO NOT FIX proactively — no evidence this ever occurs; revisit only if a future catalog refresh changes the `details` type contract.

---

## 5. Deeper Underlying Issues (found via independent pipeline inspection)

- **DEEP-1 — There is no atomicity/integrity checkpoint anywhere in catalog loading.** Neither `catalog_index()` nor `Agent._build_index()` performs a final row-count check, a "did we process exactly N lines" assertion, or any post-load integrity verification. In practice this is *masked* by Python's own failure semantics — because both functions raise before returning/completing construction, a partial result is never actually assigned to a caller-visible variable (confirmed empirically: `catalog_index()` raising mid-loop means the destructuring assignment `catalog_ids, categories, products = catalog_index(...)` in `main()` simply never executes, so there is no reachable "half-populated dict" bug in practice). **The real problem is not silent partial-catalog *use* — it's that there is no way to know, after a crash, how many of the 50,000 records were good before the process died**, because the exception message carries no line number, no `parent_asin`, and no processed-count. See Error Observability section.
- **DEEP-2 — `Agent.__init__`'s SQLite transaction semantics were verified directly.** `executemany()` batches of 1000 rows are inserted without an intermediate `commit()` (commit only happens once, after the full file is read, at [starter/agent.py:71](../starter/agent.py#L71)). A manual replication of the loop (since the real constructor discards its state on exception) confirmed that **uncommitted batches remain query-visible on the same connection** even without `commit()` — this is standard SQLite same-connection transaction visibility, not a bug — but it is moot in practice because `Agent.__init__` raising means the connection object itself is never returned to any caller and becomes unreachable. **Net effect: crash-during-build is a clean total failure, not a silent partial-catalog agent being used downstream** — this is good news, but it is good by accident (Python object-lifetime semantics), not by design (there is no explicit atomicity guarantee, no rollback statement, no integrity check).
- **DEEP-3 — The two catalog loaders (`starter/agent.py` vs `evaluator/local_evaluator.py`) are maintained independently and already provably diverge** on: (a) duplicate `parent_asin` handling (FTS keeps both rows; dict keeps last, confirmed empirically in §"L" of the experiment log — `catalog_index` on two rows sharing `parent_asin="DUPLICATE1"` retains only `"Second Version"`, while `Agent`'s FTS table retains **both** `('DUPLICATE1', 'First Version')` and `('DUPLICATE1', 'Second Version')`), and (b) `categories`-as-string handling (BUG-1: evaluator char-splits, starter does not). Any future participant who trusts one loader's behavior as representative of "the catalog" risks being surprised by the other. Not currently triggered (0 duplicates in real data), but the divergence is real, demonstrated code, not a hypothetical.
- **DEEP-4 — Fatal-vs-recoverable classification is backwards in exactly one place: session-level failures in `evaluate()` are currently fatal (BUG-3) even though they are the most recoverable failure in the whole pipeline** — a single bad sample among 200 (or 800) is trivially isolable (skip it, count it as a miss, continue), yet it currently has the *widest* blast radius of any failure mode audited (it takes down the entire run). By contrast, per-turn agent failures — arguably a more "expected" failure given they depend on untrusted participant code — are already correctly isolated (ROBUST-4). This inversion (the *safer-to-isolate* failure is the one left unisolated) is the single most important structural finding of this audit.
- **DEEP-5 — No code path anywhere identifies *which* record caused a failure.** Every exception observed in this audit (`KeyError: 'parent_asin'`, `TypeError: string indices must be integers`, etc.) carries no `parent_asin`, no line number, and no `sample_id`. See Error Observability below.

---

## 6. Error Observability

For every confirmed failure mode above, the same question was asked: *if this happens on the real 50,000-record catalog or the real 200/800-session set, can a developer determine which record failed, on what field, with what value, and how many records were already processed?*

| Failure | Line/record identity in the exception? | Field/value identity? | Processed-count before failure? | Verdict |
|---|---|---|---|---|
| Malformed JSON line in catalog | Yes — `json.JSONDecodeError` includes `line`/`column`/`char`, but that's the **byte offset within one line**, not the line number in the file (neither `catalog_index` nor `Agent._build_index` tracks `lineno` in their `for line in handle` loops) | No | No | **Vague / impossible to trace to a file line without adding a counter** |
| Blank line in catalog | Same as above (reported as "Expecting value" with no file line number) | No | No | Same |
| Non-dict catalog line | `TypeError`, no context at all | No | No | **Impossible to trace** |
| Missing `parent_asin` | `KeyError: 'parent_asin'` — names the *field*, not the record | No (no other field of the bad record is shown) | No | **Partial** — field identity known, record identity not |
| Missing `ground_truth.parent_asin` / bad `user_profile` / bad `scenario_type` | `KeyError` names the field | The `sample_id` of the failing sample is **not included in the exception** (though it exists in the loop variable `sample`) | No (which position in the 200/800 loop) | **Vague — the very datum needed to locate the bad session, `sample_id`, is discarded** |
| Duplicate `parent_asin` | N/A — no exception, silent | N/A | N/A | **Silently swallowed, by design absence, not by a catch-all handler** |
| `categories`-as-string char-split | N/A — no exception, silent | N/A | N/A | **Silently swallowed** |
| Nested-dict `details` leaking into constraint text | N/A — no exception, silent | N/A | N/A | **Silently swallowed** |

**Summary judgement:** there is no `try/except` anywhere in the catalog- or session-loading paths that *catches and re-raises with context* — every exception is the raw, uncontextualized exception from the standard library or from a bare dict access. Every silent (non-crashing) bug identified in this audit (BUG-1, BUG-2, BUG-4) produces **zero observable signal** — no log line, no counter, no warning — that would let a developer discover it without an audit like this one.

---

## 7. Test Coverage Gaps

| Failure mode | Current behaviour | Existing test | Risk | Test needed? |
|---|---|---|---|---|
| Malformed JSON line (catalog) | Uncaught `JSONDecodeError`, whole load aborts | None | Medium (private-set/future-catalog risk) | Yes |
| Blank line (catalog) | Uncaught `JSONDecodeError` | None | Medium | Yes |
| Non-dict catalog line | Uncaught `TypeError` | None | Low | Yes |
| Missing `parent_asin` | Uncaught `KeyError` | None | Low (0 real occurrences) | Yes |
| Wrong-type `parent_asin` (int/null/list) | Silently coerced via `str()` (BUG-4) | None | Low | Yes |
| Duplicate `parent_asin` (starter vs evaluator divergence) | Diverges: FTS keeps both, dict keeps last (DEEP-3) | None | Low (0 real occurrences) but demonstrated real divergence | Yes |
| `categories` wrong type (string) → char-split (BUG-1) | Silent corruption | None | Low (0 real occurrences) | Yes |
| `details` nested-dict value → dict-repr leaks into constraint text (BUG-2) | Silent, confirmed live on 19 real catalog records | None | Low–Medium (confirmed real exposure, just not in current public 200) | Yes |
| Non-numeric `price` string → nonsensical budget constraint (RISK-1) | Silent, confirmed on 117 real catalog records | None | Low | Yes |
| `parent_asin` whitespace asymmetry (RISK-2) | Confirmed to reject a semantically valid hit | None | Low (0 real occurrences) | Yes |
| Missing `ground_truth.parent_asin` (public session) | Uncaught `KeyError`, aborts entire `evaluate()` run (BUG-3) | None | **High if it ever occurs — whole-run blast radius** | **Yes — highest priority** |
| `ground_truth.parent_asin` not in catalog | Uncaught `KeyError` from `materialize_hidden_fields`, same blast radius | None | **High** | **Yes** |
| Missing `user_profile` | Uncaught `KeyError`, same blast radius | None | **High** | **Yes** |
| Missing `scenario_type` | Uncaught `KeyError`, same blast radius | None | **High** | **Yes** |
| Invalid (but present) `scenario_type` value | Degrades gracefully (ROBUST-3) | None, but behaviour is already correct | Low | Nice to have (regression protection) |
| Per-turn agent exception | Correctly isolated (ROBUST-4) | Not directly tested, but existing `EchoTargetAgent` tests exercise the happy path | Low | Nice to have |
| `normalize_recommendations` malformed payloads | Correctly defensive (ROBUST-2) | **Yes** — `test_normalization_preserves_first_valid_unique_order` | None | Already covered |
| `metric_summary` miss → turn 11 | Correct | **Yes** — `test_metric_summary_assigns_turn_11_to_miss` | None | Already covered |
| Hidden-field derivation when public set omits `intent_card`/`behavior` | Correct (happy path only) | **Yes** — `test_evaluate_derives_hidden_fields_when_public_set_omits_them` | Low for the happy path; the *unhappy* path (bad `ground_truth`) is untested | Yes, for the unhappy path |

**Aggregate finding:** of the 3 existing tests, all cover **happy-path or already-defensive** behaviour. **Zero existing tests exercise any malformed-input condition** in catalog loading or session processing — every failure mode in §1 (Confirmed Bugs) and every risk in §4 is currently untested.

---

## 8. Private-Holdout Assumptions — UNVERIFIABLE FROM PARTICIPANT DATA

The following evaluator assumptions are currently guaranteed *only* by what was observed in the public 200 and the 50,000-row catalog. None of them are enforced by code, and none can be checked against the private 800 from this repository:

1. **Every session's `ground_truth.parent_asin` exists in the catalog.** Confirmed true for all 200 public sessions (Phase 1). If violated in the private set, BUG-3 fires (whole-run crash). *UNVERIFIABLE FROM PARTICIPANT DATA.*
2. **Every session has well-formed `ground_truth`, `user_profile`, and `scenario_type` keys, correctly typed.** Confirmed true for all 200 public sessions. If violated, BUG-3 fires. *UNVERIFIABLE FROM PARTICIPANT DATA.*
3. **The catalog contains no malformed JSON lines, no blank lines, no non-dict lines, no missing `parent_asin`.** Confirmed true for the current 50,000-row `catalog.jsonl` (it is frozen and shared with the private evaluation, per `README.md` — "frozen catalog of 50,000 products" used for both splits). Since the catalog is shared (not separately generated per holdout), this specific risk is lower than the session-level risks above, but it is still enforced by nothing in code.
4. **No `parent_asin` in the catalog has leading/trailing whitespace.** Confirmed true currently (Phase 1: 100% clean strings). RISK-2 would silently under-count hits if ever violated. Since the catalog is shared across public/private, this is the **same** catalog file, so this specific risk does not vary between public and private evaluation — but it is worth stating that "clean today" is not "enforced by code."
5. **`purchase_frequency` is constant (`"3-4 prior purchases"`) across all sessions** (Phase 1 finding). The evaluator's own code does not depend on this being constant or variable in any way (the baseline `Agent.reset()` ignores `user_profile` entirely) — so even if the private 800 vary this field, **no evaluator code path is at risk**; only participant agents that hard-code an assumption about its format would be exposed. *UNVERIFIABLE FROM PARTICIPANT DATA*, but also **NO ISSUE for organizer code specifically.**

**Recommendation for Phase 3 discussion (not implemented here):** assumptions 1 and 2 are the ones that matter — they are the direct triggers for BUG-3, the highest-severity finding in this audit — and they are exactly the ones the evaluator itself could and should enforce (fail loudly and specifically at load time, or isolate at the per-sample level) rather than silently trust.

---

## 9. Prioritized Fixes (nothing implemented — for Phase 3 planning)

| ID | Finding | Severity | Priority | Rationale |
|---|---|---|---|---|
| BUG-3 | One malformed session aborts the entire `evaluate()` run, discarding all prior scoring | Critical | **MUST FIX** | Only finding with full-run blast radius; directly threatens competition-outcome integrity if the private 800 ever contains one such session; the fix (per-sample `try/except`, mirroring the already-correct per-turn pattern) is small and low-risk. |
| DEEP-5 / §6 | No exception anywhere carries record identity (line number, `parent_asin`, `sample_id`) | High | **MUST FIX** (paired with BUG-3) | Without this, even after BUG-3 is fixed to isolate a bad sample, there is no way to report *which* sample or catalog record was skipped — turning a silent full-abort into an equally silent partial-skip is not an improvement without observability. |
| BUG-1 | `categories`-as-string silently char-split in `catalog_index()`, inconsistent with `starter/agent.py` | Low | SHOULD FIX | Cheap guard; currently 0 real occurrences but the two loaders' inconsistency is itself worth closing. |
| BUG-2 | Nested-dict `details` values leak as Python-repr text into simulated customer messages | Low | SHOULD FIX | Confirmed live on 19 real catalog records (not hypothetical); cheap to guard in `_flatten_values()`. |
| RISK-2 | `parent_asin` whitespace asymmetry between `catalog_index()` and `normalize_recommendations()` | Low | SHOULD FIX | 0 real occurrences, but a real, demonstrated inconsistency that would silently under-score a participant if it ever triggered. |
| RISK-1 | Non-numeric `price` strings produce nonsensical `"budget around $—"` constraint text | Low | SHOULD FIX | Non-crashing, cosmetic-but-confusing; affects ~0.23% of catalog if a target happens to land there. |
| BUG-4 | Wrong-typed `parent_asin` silently coerced to a nonsense string ID | Medium (impact) / Low (likelihood) | SHOULD FIX | 0 real occurrences; fix is a simple type/non-empty check at load time. |
| RISK-3 | `user_profile` schema from `agent_api_contract.json` not enforced at runtime | Low/Medium | NICE TO HAVE | Only matters if the private set ever violates the documented contract; would primarily protect participant agents, not organizer scoring. |
| RISK-4 | `details`-as-list (wrong type) loses key/value structure but does not crash | Low | DO NOT FIX | 0 real occurrences, no demonstrated impact; revisit only if triggered by a future catalog. |
| DQ-1/2/3/4 | Sparsity, non-numeric price strings, key-casing fragmentation, cosmetic wrong-type coercion | N/A (data, not code) | DO NOT FIX (as "bugs") | These are valid upstream data characteristics already handled without crashing; any change here belongs to a data-normalization design discussion, not an error-handling fix. |

---

## 10. Summary Table — Condition → Pipeline Stage → Outcome

| Condition | Parsing | Validation | Normalization | Storage/Index | Retrieval | Evaluation | Classification |
|---|---|---|---|---|---|---|---|
| Malformed JSON (catalog) | **Crash** (`JSONDecodeError`) | — | — | — | — | — | CONFIRMED BUG (BUG in observability terms; crash itself is arguably correct-but-uninformative) |
| Blank line (catalog) | **Crash** (`JSONDecodeError`) | — | — | — | — | — | CONFIRMED BUG (should be skipped, like `load_jsonl` already does for public set) |
| Non-dict line (catalog) | **Crash** (`TypeError`) | — | — | — | — | — | CONFIRMED BUG (observability) |
| Missing `parent_asin` | OK | **Crash** (`KeyError`) | — | — | — | — | CONFIRMED BUG (observability); 0 real occurrences |
| Wrong-type `parent_asin` | OK | No check | Silently coerced via `str()` | Indexed under nonsense ID | Unreachable by any real agent | N/A | CONFIRMED BUG (BUG-4); 0 real occurrences |
| Missing/empty `title` | OK | N/A | `_text`/`intent_card` fall back safely | OK | OK | OK | CONFIRMED ROBUST |
| Wrong-type `title` | OK | N/A | Coerced via `str()`, cosmetically ugly | OK | OK | OK | CONFIRMED ROBUST (crash-safe), DATA QUALITY (cosmetic) |
| Missing/empty `features`/`description` | OK | N/A | Coerced to `""` | OK | OK (less signal) | OK | CONFIRMED ROBUST / DATA QUALITY |
| Wrong-type `categories` (string) | OK | No check | **Silent char-split** (BUG-1) | Corrupted list stored | Garbled category text surfaces in customer message | Not score-affecting (categories isn't the scored field) | CONFIRMED BUG (BUG-1); 0 real occurrences |
| Missing/empty `details` | OK | N/A | Falls through safely | OK | OK (less signal) | OK | CONFIRMED ROBUST / DATA QUALITY |
| Nested-dict `details` value | OK | No check | Stringified dict-repr leaks through (BUG-2) | OK | Nonsensical text may reach simulated customer message | Not score-affecting | CONFIRMED BUG (BUG-2); confirmed live on 19/50,000 real records |
| Non-numeric `price` string | OK | No check | Leaks into `"budget around $—"` (RISK-1) | OK | Nonsensical but classifiable text | Not score-affecting | POTENTIAL RISK; confirmed live on 117/50,000 real records, 0/200 public targets |
| Duplicate `parent_asin` | OK | No check | — | **Diverges**: FTS keeps both, dict keeps last | Diverges accordingly | Diverges accordingly | CONFIRMED BUG (DEEP-3 inconsistency); 0 real occurrences |
| `parent_asin` whitespace | OK | No check (catalog side); `.strip()` (recommendation side) | — | Stored with whitespace | A correct hit can be silently rejected (RISK-2) | Under-counts `HitRate@10`/`MRR` for that ID | POTENTIAL RISK; 0 real occurrences |
| Missing `ground_truth.parent_asin` | OK | **Crash** (`KeyError`), aborts whole `evaluate()` run | — | — | — | — | CONFIRMED BUG (BUG-3); 0 real occurrences, Critical severity if triggered |
| `ground_truth.parent_asin` not in catalog | OK | **Crash** (`KeyError` from `products[target]`) | — | — | — | — | CONFIRMED BUG (BUG-3); 0 real occurrences |
| Missing `user_profile` | OK | **Crash** (`KeyError`) | — | — | — | — | CONFIRMED BUG (BUG-3) |
| Missing `scenario_type` | OK | **Crash** (`KeyError`) | — | — | — | — | CONFIRMED BUG (BUG-3) |
| Invalid (present) `scenario_type` value | OK | No check needed — safe default | Falls back to generic browsing-like behaviour | — | — | Scored under its own `scenario_metrics` key | CONFIRMED ROBUST |
| Malformed agent `recommendations` payload | — | — | — | — | **Correctly sanitized** | Correct | CONFIRMED ROBUST |
| Agent `respond()` raises | — | — | — | — | Correctly caught, substituted with empty response | Correct (counted as a miss for that turn) | CONFIRMED ROBUST |

---

## Appendix — Experiment Log Reference

All empirical claims above were produced by a single script executed against the real, unmodified `starter/agent.py` and `evaluator/local_evaluator.py` modules, using synthetic JSONL fixtures written to a scratch temp directory (never inside the repository). No repository file was read-modified; the real `data/catalog.jsonl` and `data/public_set.jsonl` were only ever opened for read-only cross-referencing (e.g. checking how many real catalog records have a nested-dict `details` value, and whether any of the 200 public targets are among them). The script and its full raw output are retained in the session's scratch directory for reproducibility but are not part of this repository.
