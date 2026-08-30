# CLAUDE.md — TechJam Conversational Search

This file is read automatically by Claude Code at the start of every session.
Do not delete it. Keep it accurate. **If the project changes materially, update
this file in the same commit** — see "Keeping this file current" at the bottom
for what counts as material and what an entry looks like.

Architecture and method are documented elsewhere — read those rather than
re-deriving them: `README.md`, `agent_architecture.md`, `docs/report.md`,
`docs/competition_specification.md`, `docs/submission_rules.md`. This file holds
what those do not: current state, operational facts, decisions and their
reasons, and measurements that cost time to discover.

---

## What this project is

A multi-turn shopping agent that finds a hidden target product in a 50,000-item
Amazon clothing catalog across 200 public dev sessions, asking clarifying
questions only when they are worth more than another retrieval call. Scored by
the organizer's local evaluator, unmodified.

**Repo:** github.com/xjinhx/winfluencers-techjam (`origin`, private)
**Upstream:** github.com/TechJam2026/techjam-conversational-search (`upstream`, read-only)
**Trunk:** `origin/main` — the real trunk, all work merges here via PR
**Local `main` is NOT the trunk.** It is still the organizer's untouched starter
(`3407835 "Publish conversational search challenge"`, no `config/` directory at
all). Never diff or branch against it thinking it is your work — use
`origin/main`.
**Active branch:** `maximise-mrr` — MRR headroom, ranks 2-5 recovery
**Other branches:** `jinhong`, `joey`, `intentions`, `arwen`, `dylan-data-error`,
`feat/shopping-copilot` — per-person work
**Session email:** xjinhx@gmail.com
**Environment:** Windows 11, Claude Code in VS Code. Primary shell is
**PowerShell**, not bash — see Critical rule 6.

---

## File structure

```
techjam-conversational-search/
├── shopping_copilot/          # the agent — all of our own logic lives here
│   ├── agent.py               # turn loop, trace emission (config.trace_path)
│   ├── config.py              # RankingConfig + all tunable weights
│   ├── ranking.py             # LinearModel scoring, unknown-penalty (post-hoc, line ~120)
│   ├── features.py            # feature vector construction
│   ├── fusion.py              # lexical + dense convex combination -> `fused`
│   ├── index.py / text.py / dense.py   # retrieval
│   ├── intent.py              # buying / browsing / uncertain classification
│   ├── clarify.py             # when to ask vs. retrieve again
│   ├── state.py / profile.py / structured.py / catalog.py
│   └── baselines/
├── starter/agent.py           # organizer entry point; reads SHOPPING_COPILOT_CONFIG
├── evaluator/local_evaluator.py   # READ-ONLY ground truth, never modify
├── config/tuned.json          # live tuned weights — the submission config
├── tools/
│   ├── offline_eval.py        # replay a trace, validate session by session
│   ├── why_lost.py            # rank diagnostic: which feature costs rank 1
│   ├── tune.py                # coordinate search + stratified_halves
│   ├── ablate.py / evalkit.py / measure_attribute_yield.py / demo.py
├── tests/                     # test_shopping_copilot.py, test_evaluator.py
├── data/                      # FROZEN. catalog.jsonl is 60 MB, gitignored
├── docs/                      # report.md, spec, submission rules, ablations,
│                              # tuning_report.json, baseline_results.json
├── results.json               # gitignored, no backup — the reported score
└── CLAUDE.md                  # this file
```

---

## Architecture in one paragraph

Each turn, the agent updates a structured profile from the dialogue, retrieves
candidates with a lexical + dense hybrid whose scores are fused into a single
`fused` signal, builds a feature vector per candidate, and scores it with a
linear model whose weights come from `config/tuned.json`. An unknown-penalty is
applied *outside* the model, after scoring, so `LinearModel` alone does not
reproduce the final ordering. Clarifying questions are asked only when their
expected value beats another retrieval call. Everything is stdlib-only Python —
no build step, no external dependencies (see Critical rule 5).

---

## Current state

**Live score: `TechnicalScore = 0.892242`** — measured 2026-08-30 by running the
adopted title/coverage interaction config through the unmodified evaluator on
all 200 public sessions. Up from 0.881716 — see "What was found" below.

**On 800 vs. 1000 (`per_field_depth`):** this was independently found twice —
Dylan tested and adopted `1000` (0.876336) before discovering He/Joey had
independently pushed `800` (0.876342) to `main` in the meantime. Reconciled by
keeping `800` (per Dylan's explicit choice, and it's already what `main` has)
— the two scores are statistically indistinguishable (+0.000006 apart, nowhere
near the ~0.05 MRR-scale noise floor), so this was a coordination call, not a
quality one. Don't re-litigate 1000 vs. 800 based on this tiny gap.

| metric | value |
|---|---|
| HR@10 | 0.990 (2 misses, down from 3) |
| MRR | 0.722139 |
| MTTC | 1.97 |
| Efficiency | 0.903 |
| **TechnicalScore** | **0.892242** |

| scenario | n | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| buying | 80 | 1.0000 | 0.7721 | 1.31 |
| browsing | 80 | 0.9750 | 0.6397 | 1.96 |
| intent_override | 30 | 1.0000 | 0.8261 | 3.70 |
| boundary | 10 | 1.0000 | 0.6700 | 2.10 |

**Intent-conditional weighting is adopted and live.** `config/tuned.json` sets
`w_fused_buying: 0.0` and `w_fused_uncertain: 0.0` against `w_fused: 1.0` — this
is no longer an open decision, and any doc saying otherwise is stale.

**Stale artefacts — do not quote these as the current score:**
- **0.881716** predates the title/coverage interaction adopted below.
- **0.862111, 0.863556, 0.876336, and 0.876342** (and the miss lists that went
  with them) all predate the `constraint_commonness_penalty` fix below.
  0.862111 is pre-merge-with-main; 0.863556 is post-merge-pre-fix;
  0.876336 was Dylan's own `per_field_depth=1000` before reconciling with
  Joey's `800` already on `main`; 0.876342 is the reconciled `800` value,
  current on `main` but superseded on this branch.
- `results.json` = **0.881716** and predates the current config. It was preserved
  rather than overwritten per Critical rule 1; the validated current output is
  `scratch/title_experiments/integrated_live.json`.
- `docs/pending.md` was written at the 0.7848 stage. Its P-item numbering is
  still useful, its measurements are not — see "Roadmap".

---

## Critical rules — do not break

1. **Never let the evaluator overwrite `results.json`.** It is gitignored and
   there is no committed backup — it is the only record of the reported score.
   Always pass `--output <scratch>/whatever.json` when experimenting.
2. **Never edit `config/tuned.json` to run an experiment.** Copy it to a
   scratchpad, edit the copy, point `SHOPPING_COPILOT_CONFIG` at it. The same
   applies to `python -m tools.tune` — its `--output` **defaults to
   `config/tuned.json`** and `--report` to `docs/tuning_report.json`, so a bare
   tune run silently overwrites the live submission config. Always pass both.
3. **Never modify `evaluator/`.** Explicitly forbidden by the submission rules.
   Read-only ground truth.
4. **Never write to `data/`.** The catalog is frozen per submission rules.
   `data/catalog.jsonl` is 60 MB, gitignored, downloaded from the participant
   release — a fresh clone will not have it.
5. **Stdlib only.** `requirements.txt` is deliberately empty, as a hedge against
   network restrictions at judging time. Adding a dependency (LightGBM, numpy,
   anything) is a decision to make explicitly with He Jinhong, not a side effect
   of an implementation — and anything added must degrade to a `LinearModel`
   fallback if the binary is missing.
6. **The shell here is PowerShell, not bash.** `VAR=x python -m ...` is a parse
   error, not an env var. Use `$env:VAR = "x"; python -m ...`, or run the POSIX
   form through the Bash tool. Same for `/dev/null`, `&&`, and `$(...)`.
7. **Never delete traces without validating offline first.** Traces
   (`features_*.jsonl`) are large but are the only way to replay and audit a
   change. Keep them until `offline_eval` confirms the result, then archive or
   delete deliberately. They regenerate in ~3 minutes if lost.
8. **State the uncertainty next to every claimed gain.** Single-run SE is
   ~0.029 on this 200-session set, so a change smaller than roughly that cannot
   be *verified* here — but it is not thereby refused. The private set is 800
   sessions, where SE is about half, so a real +0.02 can show up there even
   though this set cannot prove it. What is forbidden is quoting a gain without
   saying whether it survives holdout: report the fold-B number alongside it,
   and say plainly when a result is below the verification threshold.

## Critical evaluator facts (silent-corruption risks)

Each of these will corrupt an offline analysis if got wrong, without erroring.

- **Sessions cannot be joined to labels by id.** The evaluator hands the agent a
  fresh `uuid4` (`local_evaluator.py:227`); the agent never sees `sample_id`.
  Join positionally — `evaluate()` iterates `for sample in samples`
  single-threaded, so the Nth distinct session id in a trace is the Nth sample.
- **Override turn is drawn per sample**, `rng.choice([3, 4])` seeded on
  `f"{sample_id}\0{scenario_type}"` — not fixed at 3. Reproduced in
  `offline_eval.override_turn_for`.
- **`best_rank` is first-hit-in-top-10, not full-pool rank.** For
  `intent_override` sessions, any hit before the override turn is ignored
  (`local_evaluator.py:234,252`).
- **Unknown-penalty is a first-class feature, not a post-hoc adjustment**
  (as of 2026-08-30 — folded into `features.py`'s `{dim}_unknown` columns and
  `ranking.py:build_linear_weights`, see "What was found" above). Three of
  six non-zero under the tuned config: gender −0.01, category −0.005, price
  −0.005. `LinearModel` alone now correctly reproduces ordering — this used
  to require a separate additive step, and any doc/tool assuming otherwise
  is stale.
- **MMR is disabled** (`enable_mmr = False`). Ties break on `parent_asin`.
- **`difficulty_bucket` is deterministic from `scenario_type`**
  (buying→easy, browsing→medium, intent_override→hard, boundary→medium).
  Carries no extra information; slicing on it is redundant.

## Score decomposition (optimize HR@10 first)

**Formula:** `TechnicalScore = 0.5·HR@10 + 0.3·MRR + 0.2·efficiency`
where `efficiency = clip((11 − MTTC)/10, 0, 1)` and a miss = 11 turns.

**Last decomposition (intent-conditional experiment, 2026-08-03):** when
`w_fused` was dropped for buying/uncertain intents, the gain split
HR@10 **50%** / MRR **17%** / efficiency **32%**.

**Key insight:** HR@10 carries the largest weight. A plan framed purely around
MRR optimizes only the smallest term. Always decompose before committing.

## Measurement discipline (critical)

- **Paired MRR SE ~0.024** across 200 sessions (bootstrap, 20k resamples).
  Single-run SE ~0.029. A change worth less than ~+0.05 MRR cannot be
  distinguished from noise **on this set** — a statement about what 200 sessions
  can measure, not about whether the change is real. Report such a result as
  unverified rather than discarding it; the 800-session private set has roughly
  half this noise.
- **Report the sign test and the CI together.** They often disagree; a solid
  direction with a non-significant CI is plausible but not proven. Report both,
  not whichever one looks better.
- **Selection-on-noise accumulates.** 16+ configs have now been evaluated
  against the same 200 sessions. Validate new changes with
  `tools.tune.stratified_halves` (seed 7) and quote **fold B** (the conservative
  half) — it is closer to private-test performance than fold A. For the
  intent-conditional change: fold A +0.0364, fold B +0.0121.
- **Offline replay must match live.** `tools/offline_eval.py` validates session
  by session — all 200 must agree on `best_rank`, not just aggregate MRR. This
  gate caught both join bugs in the evaluator understanding.

## Tooling

PowerShell (primary shell — see Critical rule 6):

```powershell
# full evaluator, ~3m (never target results.json)
$env:SHOPPING_COPILOT_CONFIG = "c:\tmp\cfg.json"
python -m evaluator.local_evaluator --output c:\tmp\out.json

# offline replay of a trace (~10s) — validates session by session
python -m tools.offline_eval --trace c:\tmp\features.jsonl --against c:\tmp\out.json

# rank diagnostic — which feature costs rank 1
python -m tools.why_lost --trace c:\tmp\features.jsonl --ranks 3,4,5 --top 30

# tuning — ALWAYS redirect both outputs (rule 2)
python -m tools.tune --output c:\tmp\cfg.json --report c:\tmp\tuning_report.json

# tests
python -m unittest discover -s tests    # 32 tests
```

**Tracing:** set `trace_path` in the config to emit `features.jsonl` (~115k
rows, ~45 MB per run). Tracing is passive and verified not to change results.

**Validation gate:** `offline_eval.py` reproduces the ranker exactly. All 200
sessions must agree on `best_rank` per session, not just on aggregate MRR.

---

## What was found

*Append-only. Newest entries at the top, each dated, each with the reasoning —
not just the outcome. This is the section that makes the file worth reading.*

**Title/coverage interaction adopted (2026-08-30, per He Jinhong: "try others,
can merge a few together"):**

The earlier blanket title-length and density fixes did not generalize. A fresh
36-column trace was therefore generated against the 0.881716 baseline and
validated at 200/200 live/offline session agreement before testing 52 surgical
single changes across score transforms, pool-relative clipping, intent-specific
weights, cross-field interactions, structured gates, and relevance-conditioned
popularity. Twelve singles improved both `stratified_halves(seed=7)` folds.

The adopted three-term combination is:

- buying-only `bm25_title` weight: 0.26 -> **0.18**
- `bm25_title * (1 - coverage)` weight: **-0.20**
- `popularity * (1 - coverage)` weight: **-0.40**

This is not a title-length penalty. Detailed titles retain their evidence when
they cover the query; only title and popularity confidence unsupported by query
coverage is reduced. The buying-only override limits the original BM25 title
term where disclosed constraints provide stronger evidence.

Seed-7 split result: train **+0.010138**, holdout **+0.010913**. Full public-set
result: `TechnicalScore` 0.881716 -> **0.892242** (+0.010526), MRR 0.687054 ->
**0.722139**, with HR@10 (0.990), MTTC (1.97), and efficiency (0.903) unchanged.
All scenario deltas are non-negative: browsing +0.010464, buying +0.012600,
intent override +0.008667, boundary unchanged.

The local 5x5x5 neighborhood has a broad plateau: multiple neighboring penalty
pairs produce the same ordering. Paired bootstrap over 20,000 resamples gives
a TechnicalScore delta CI of **[+0.005882, +0.015843]**; 29 sessions improve,
one regresses, and 170 are unchanged. Across 100 stratified seeds (200 halves),
the minimum half delta is +0.003317 and no half is negative.

Implementation adds the two interactions as explicit named features and extends
the existing per-intent override mechanism to `bm25_title`. Defaults are zero /
unset, preserving old configurations. The integrated full evaluator produces
0.892242 exactly; offline replay matches all 200 session ranks with zero MRR
drift. 34/34 unit tests pass. Experiment grids and validation reports live in
`scratch/title_experiments/`.

**`constraint_commonness_penalty` adopted — the deeper fix for `public_0100`,
generalizes to a second session (2026-08-30, Dylan Huang, branch
`fix/public-0100-candidate-depth`):**

**Follow-up to the entry below.** After `candidate_depth` was rejected,
traced `public_0100` turn-by-turn (not just turn 1) and found the real
mechanism: the target ranks **110th of 800 at turn 1** (fine), then
**vanishes from the candidate pool entirely from turn 2 onward**. Turn 2
discloses *"Manmade sole; Platform measures approximately 0.5\""* —
verbatim from the target's own listing — and "manmade"/"sole"/"platform"/
"approximately"/"measure" are near-universal shoe-listing boilerplate.
Added as high-weight query terms (0.975–1.15), they pull in thousands of
competing documents; the target's own `features`-field rank for this exact
query is 1196th of 14,176 matches, outside even `per_field_depth=800`, and
its overall fused rank collapses from ~110th to ~1049th.

**Deliberately not fixed with a hardcoded phrase list** (per direct
instruction — would overfit to phrases seen in the 200 public sessions,
won't generalize to the private 800). Instead: **measure how common each
disclosed term actually is across the catalog, and down-weight
proportionally** — the same IDF/document-frequency philosophy
`BM25Field.search()` already applies internally, but as an explicit,
continuous ramp at query-construction time, scoped only to
constraint-span terms (never the category phrase).

**Implementation, reusing existing infrastructure, no new indexing:**
- `LexicalIndex.commonness(term)` (`index.py`) — reuses the already-built
  per-field `doc_frequency()` postings, returns the max document-frequency
  ratio across title/features/categories.
- `RetrievalConfig.constraint_commonness_penalty` (`config.py`) — new
  field, default `0.0` (disabled, byte-identical to prior behaviour).
- `ShoppingState.query()` (`state.py`) — new optional params
  (`term_commonness`, `commonness_penalty_strength`, `max_df_ratio`);
  inside the constraint-term loop only: `damping = 1.0 -
  strength * max(0.0, 1.0 - df_ratio/max_df_ratio)`, ramping weight down
  continuously as a term's catalog frequency approaches the existing
  hard `max_df_ratio` cutoff, rather than all-or-nothing at it.
- `agent.py` — one call site updated to pass the three new arguments.

**Verified score-neutral at the default (mandatory gate, checked before
sweeping anything):** 32/32 tests pass, full live evaluator run reproduces
`TechnicalScore=0.876342` exactly, byte-identical.

**Grid swept against `stratified_halves(seed=7)`, based on the live tuned
config:**

| strength | train | holdout |
|---|---|---|
| 0.0 (baseline) | 0.8722 | 0.8805 |
| 0.05 | 0.8725 (+0.0003) | 0.8802 (−0.0002) |
| 0.10 | 0.8720 (−0.0002) | 0.8788 (−0.0017) |
| 0.15 | 0.8707 (−0.0015) | 0.8781 (−0.0024) |
| 0.20 | 0.8707 (−0.0015) | 0.8878 (+0.0073) |
| **0.30 (adopted)** | **0.8736 (+0.0014)** | **0.8898 (+0.0093)** |
| 0.35 | 0.8715 (−0.0007) | 0.8898 (+0.0093) |
| 0.40 | 0.8700 (−0.0022) | 0.8898 (+0.0093) |
| 1.00 | 0.8652 (−0.0070) | 0.8870 (+0.0065) |

**Holdout improves at every value from 0.20 upward, plateauing at
0.30-0.40 — the same non-overfitting signature `per_field_depth` showed,
and the opposite of `candidate_depth`'s.** This is structural for the same
reason: the damping only ever *reduces* noise proportional to *measured*
catalog frequency, it never invents a coefficient shaped to fit train-set
patterns. `0.30` was chosen over the higher plateau values as the more
conservative choice — it's simultaneously where train peaks and where
holdout's plateau begins, not an arbitrary pick from a flat region.

**Full 200-session result, adopted into `config/tuned.json`:**
`TechnicalScore` 0.876342 → **0.881716** (+0.0054). **HR@10 0.985 → 0.990
(3 misses → 2)**, MRR 0.679141 → 0.687054, MTTC 1.995 → 1.97,
`target_never_in_pool` 3 → 2. `offline_eval` confirms 200/200 session
agreement. 32/32 tests pass.

**`public_0100` itself: confirmed fixed, and cleanly** — turn 3, rank 1
(the best possible reciprocal rank). This is the specific session the fix
was designed around, and it worked exactly as diagnosed, not
coincidentally.

**Bonus check (not diagnostic):** `public_0092` and `public_0137` are
still misses at this value — unaffected, consistent with them not having
been diagnosed as sharing this mechanism.

**Not on `main` yet** — this branch (`fix/public-0100-candidate-depth`)
also carries the earlier, rejected `candidate_depth` experiment (see entry
below), documented rather than discarded since it's what motivated tracing
`public_0100` turn-by-turn in the first place, which is what actually found
this fix.

**`public_0100` diagnosed and a targeted fix tested — rejected, and the
rejection revealed a different, harder problem than `per_field_depth`
(2026-08-30, Dylan Huang, branch `fix/public-0100-candidate-depth`):**

**The request:** of the 3 misses remaining after the `per_field_depth`
fix, root-cause `public_0100` specifically (browsing scenario, target
`B002OHE4D6`, a men's Dockers leather loafer).

**Diagnosis, traced directly, not guessed:** turn 1's query ("I'm looking
for Shoes Loafers & Slip-Ons, but I'm still exploring.") already ranks the
target well inside every per-field cutoff — `categories` rank 23/808,
`title` rank 312/5355, both comfortably under `per_field_depth=800`. The
apparent bottleneck at turn 1 was the aggregate fusion cutoff instead:
uncapped fused rank 294/2417, just outside `candidate_depth=200`.
Unlike `per_field_depth`, this is squarely `RetrievalConfig.candidate_depth`
territory — a genuinely different lever from the earlier fix.

**A real code detail surfaced along the way:** `agent.py` does
`candidate_ids = top_n(fused, candidate_depth)` then
`candidate_ids[:rerank_depth]` — since `rerank_depth` currently equals
`candidate_depth` (200==200), raising one alone does nothing; both must
move together for the change to take effect at all.

**Tested exactly like the `per_field_depth` fix — grid swept against
`stratified_halves(seed=7)`, `candidate_depth`/`rerank_depth` moved
together:**

| depth | train | holdout | 100-session time |
|---|---|---|---|
| 200 (baseline) | 0.8722 | 0.8805 | ~9s |
| 250 | 0.8714 (−0.0008) | 0.8748 (−0.0057) | ~10.5s |
| 300 | 0.8696 (−0.0026) | 0.8721 (−0.0084) | ~11.3s |
| 400 | 0.8788 (+0.0067) | 0.8728 (−0.0077) | ~10.8s |
| 500 | 0.8788 (+0.0067) | 0.8719 (−0.0086) | ~13.1s |
| 800 | 0.8789 (+0.0067) | 0.8706 (−0.0099) | ~17.7s |

**Every single value regressed holdout, monotonically worse at higher
depths — the same signature as the rejected pairwise-LTR and weight-tuning
experiments, not the `per_field_depth` result.** Also, unlike
`per_field_depth`, this one carries a real, growing timing cost (9s → 17.7s
per 100-session fold at depth 800 — roughly double, since raising
`rerank_depth` scales the expensive per-candidate feature-extraction +
scoring path, not just a cheap pre-fusion BM25 cutoff).

**Confirmatory check — and this is the important part: `public_0100`
itself never flips, at any tested depth, including 800.** Traced the full
10-turn transcript at `candidate_depth=800`: by turn 8 the session's
top-5 is `Bruno Marc Men's Leather Lined Dress Loafers`, `Stacy Adams
Men's Flynn Moc-Toe Bit Slip-On Loafer`, `Go Tour Men's Premium Genuine
Leather Casual Slip-On Loafers` — several genuinely similar, legitimate
men's leather dress loafers the customer's disclosed constraints
(material, brand) don't distinguish from the actual target. **This is not
a pool-depth problem at all once enough turns pass — it's the
reranker failing to discriminate among several near-identical
competitors, the same class of problem as the already-investigated (and
already-rejected, see the LambdaMART/separability entry) rank-2
reranking problem.** The turn-1 candidate_depth analysis above was real
and correctly diagnosed turn 1, but doesn't explain why the miss persists
through turn 10 — a materially incomplete picture that only surfaced by
tracing the full transcript, not just turn 1.

**Rejected. `config/tuned.json` untouched.** Per the same discipline as
every other experiment this session: a regression on holdout is a reason
not to ship, not a reason to keep searching for a luckier value.

**Bonus check (not diagnostic):** at `candidate_depth=300`, `public_0092`
and `public_0137` are also still misses — observed in the same replay,
not separately diagnosed. Do not treat this as evidence about their root
cause; they have not been traced the way `public_0095`/`public_0100` were.

**What this changes about the remaining-misses picture:** `public_0100`
is now understood to need a *feature* (something that discriminates
between very similar men's leather loafers), not a *depth* parameter —
squarely in the same "needs new information in the vector, not a better
cutoff or weight" category the rank-2 finding already established.
`public_0092` and `public_0137` remain genuinely undiagnosed.

**Reconciled with Joey's independent `per_field_depth=800` + NQC confidence
change (2026-08-30, Dylan Huang):** `main` moved again after the entry below
was written — Joey independently landed `per_field_depth=800` (same lever,
found separately) plus a new NQC-based confidence formula in `clarify.py`
(Shtok et al. 2009, replacing the old margin-vs-mean-of-rest heuristic).
Merged, resolved the `per_field_depth` conflict to **800** (Dylan's explicit
choice, already what `main` had — the 800-vs-1000 score gap is +0.000006,
inside noise, not a quality signal either way). **Isolated the NQC change's
own effect: byte-identical `TechnicalScore` (0.876342) with the old and new
confidence formula, both at `per_field_depth=800`.** It's a reasonable, cited
idea, currently a no-op on the public 200 — never once flips a
confidence-gate decision differently than the old formula did. Whether it
diverges on the private 800 is untested and unknowable from here; it's a
latent behavior change riding along, not a verified improvement, and should
be described as such rather than credited for score movement it didn't
produce. Full validation at the reconciled state: offline_eval 200/200
agreement, `target_never_in_pool` 6 → 3 (not as strong as depth=1000's 6 → 1,
expected since 800 is a smaller recall increase), 32/32 tests pass. Miss set
shifted at the margin between the two depths, worth noting honestly: at 1000
the 3 misses were `public_0092`/`public_0137`/`public_0161`; at 800 they're
`public_0092`/`public_0100`/`public_0137` — `public_0161` gets fixed at 1000
but not 800, while `public_0100` is the reverse. Neither depth is a strict
superset of the other's wins.

**`public_0095`'s retrieval miss root-caused and fixed — a real recall bug,
not a reranking one: `per_field_depth` 220 → 1000, later reconciled to 800,
see entry above (2026-08-30, Dylan Huang):**

**The request:** investigate `public_0095` (buying scenario, target
`B09N78FT2W`, a "Free Leaper High Waisted Yoga Pants... Leggings" listing),
flagged externally as one of 3 true retrieval misses — "the target shares
almost no vocabulary with anything the customer says."

**That framing turned out to be wrong, and the actual mechanism is more
interesting and more general.** Traced the real turn-1 query
(`tools.demo --sample public_0095`): *"I'm looking for Women Leggings. A
key requirement is: polyester."* Confirmed via `offline_eval`-style direct
inspection that **the target scored ZERO on every BM25 field at every
turn (1-10)** — never entered the candidate pool once, despite its title
literally containing "Leggings" and its features literally containing
"Polyester."

**Root cause, traced mechanically, not guessed:**
1. `"women"` — the single most useful, correctly-matching term in the
   query — appears in 48-59% of catalog titles/features/categories,
   **above `max_df_ratio=0.35`**, so `BM25Field.search()` silently drops
   it from three of five fields (`index.py`'s own comment: "their IDF is
   near zero and their posting lists are the expensive ones" — true in
   aggregate, false for this specific query where it was the one
   discriminating term available).
2. With `women` gone, only `legging`/`polyester` remain. The target *does*
   match on these (confirmed by removing the `per_field_depth` limit
   entirely and re-scoring): full-field rank 394/732 (title), 278/384
   (categories), 2841/11207 (features) — genuinely present, just outside
   `per_field_depth=220`'s per-field cutoff before fusion. Its unusually
   long, marketing-heavy title (12 tokens vs. a typical short listing)
   loses ground under BM25 length-normalisation to shorter, keyword-denser
   competitors on the few surviving terms.
3. Only the dense (character n-gram) route saw it at all (score 0.145),
   nowhere near enough alone to clear the top-200 fused candidate pool
   (200th place needed 0.274).

**Fix tested the way everything else this session was: train/holdout, not
just "does the one target session flip."** Swept `per_field_depth`
`[300, 400, 600, 800, 1000, 1200, 1500, 2000]` against
`stratified_halves(seed=7)`. **This is qualitatively different from every
other experiment this session (the pairwise LTR reranker, the
`w_fused_browsing` sweep, the bm25↔phrase trade-off) — holdout improved
*as much or more than* train at every value ≥800**, the opposite of the
overfitting signature those all showed:

| per_field_depth | train | holdout |
|---|---|---|
| 220 (baseline) | 0.8526 | 0.8745 |
| 400 | 0.8631 (+0.0105) | 0.8686 (−0.0059) |
| 800 | 0.8722 (+0.0196) | 0.8805 (+0.0060) |
| **1000 (adopted)** | **0.8656 (+0.0130)** | **0.8871 (+0.0126)** |
| 1200 | 0.8630 (+0.0104) | 0.8861 (+0.0116) |
| 2000 | 0.8509 (−0.0017) | 0.8840 (+0.0095) |

**Why this doesn't carry the same overfitting risk as a weight retune:**
`per_field_depth` isn't a coefficient that can be shaped to fit noise in
100 sessions — it's a structural cutoff on how many real candidates each
field is *allowed to surface* before fusion. Raising it only adds
information (more genuinely-matching documents become visible); it
introduces no new degree of freedom for the fold to overfit against. That
structural difference, not luck, is the honest explanation for why this
result looks nothing like this session's other experiments.

**Full 200-session result, adopted into `config/tuned.json`:**
`TechnicalScore` 0.863556 → **0.876336** (+0.0128). **HR@10 0.960 → 0.985
(8 misses → 3)**, MTTC 2.25 → 2.01, Efficiency 0.875 → 0.899. MRR dipped
slightly (0.695185 → 0.680121) — expected and correct: newly-recovered
sessions convert at whatever rank they first appear, not rank 1, so they
add less to MRR per-session than a miss (reciprocal rank 0) did, while
adding the full amount to HR@10 (0.50 weight) and Efficiency (0.20
weight) — net positive by formula, not by cherry-picking a metric.
`offline_eval` confirms 200/200 session agreement, `target_never_in_pool`
dropped **6 → 1**. 32/32 tests pass. `public_0095` itself: now hits at
turn 3, rank 3.

**Runtime cost:** modest. 100-session fold scoring went from a few seconds
at depth 220 to ~8-9s at depth 1000-2000 with a warm agent (`Bench`); full
200-session live run (cold start, fresh process) completed in ~23s total.
Not a concern for the private 800-session set at this scale.

**Not investigated further, worth flagging:** the exact optimum among
{800, 1000, 1200} is close (holdout 0.8805/0.8871/0.8861) — 1000 was
chosen as the best of the tested values, not verified as a true peak. A
finer sweep could find a slightly better value, but the qualitative
finding (raising per-field recall depth helps, robustly, on both folds)
is the load-bearing result, not the third decimal place. Also unexplored:
whether `max_df_ratio` itself (currently 0.35, the reason `women` gets
dropped in the first place) has similar headroom — untested because
raising it changes what gets *scored*, not just how many survive a cutoff,
which is a different and less structurally-safe kind of change than this
one.

**3 misses remain** (`public_0092`, `public_0137`, `public_0161`) — not
diagnosed this pass.

**Intent-router disagreement check (PRD Phase 5, read-only, 2026-08-30, Dylan Huang):**

Measured how often `intent.route()`'s dominant per-session classification
disagrees with `scenario_type` (available offline only — never sent to the
live agent). Raw label disagreement looked large (39.4%, 63/160
buying/browsing sessions), but that number is misleading: under the
current config, `w_fused_buying` and `w_fused_uncertain` are both `0.0` —
**identical treatment** — so a buying↔uncertain mix-up has zero scoring
effect. Re-measured on the metric that actually matters, the effective
`w_fused` bucket (browsing vs. everything else): **4.4% disagreement
(7/160)**, and every single case is the same direction — a `browsing`
scenario session classified as `buying`/`uncertain`, meaning it wrongly
gets `w_fused=0.0` instead of the `1.0` browsing needs.

Not investigated further this pass (Phase 5 is explicitly informational,
not blocking) but worth flagging: this is a plausible small contributor to
browsing being the weakest scenario on every metric. A targeted next step,
not done here: inspect what pushes exactly these 7 sessions' constraint-
density/marker-score above the 0.65 buying threshold despite being labeled
browsing, rather than retuning the router generally.

**Pairwise learning-to-rank experiment: implemented, tested across a
hyperparameter grid, rejected with strong evidence (2026-08-30, Dylan Huang):**

**This independently reaches the same conclusion as He Jinhong's much more
thorough sweep below (sklearn + LightGBM, 7 methods) — read that entry
first for the definitive answer** (including *why* it fails: the 39 rank-2
sessions are linearly separable in isolation but not jointly with the 109
already at rank 1). This entry is kept as a second, independent
confirmation via a different, dependency-free method, not a duplicate.

Per `PRD-ML-reranker.md`'s staged plan, on branch `dylan-ltr-reranker`.

**Phase 2 (prerequisite, shipped):** folded the constraint unknown-penalty
into the feature vector as 6 first-class `{dimension}_unknown` columns
(`features.py`), removing the post-hoc additive adjustment in
`Ranker.score_candidate` (`ranking.py`). `FEATURE_NAMES` grew 30 → 36.
Verified score-neutral: 32/32 tests pass, full live evaluator run
reproduces `TechnicalScore = 0.862111` exactly. **Caught and fixed a
second-order bug while doing this:** `tools/offline_eval.py`'s
`ReplayScorer` independently reimplements the scoring path and still had
the old post-hoc penalty loop — left alone, it would have silently
double-counted the penalty on every future replay/`why_lost` run. Fixed
and reverified (200/200 session agreement, `why_lost --ranks 2` reproduces
identical numbers to before the fix).

**Phase 3 (the actual experiment): rejected.** Built `tools/train_pairwise.py`
— a stdlib-only pairwise-logistic fit (no numpy, hand-rolled SGD) over the
same 36 features minus `fused` (deliberately frozen — it's under the
already-validated intent-conditional mechanism and `ScoringModel.score()`
has no intent parameter to make a per-intent fused weight correct) and
minus `bm25_description`/`bm25_store` (not real tunable fields — hardcoded
to 0.0 in `build_linear_weights()`, so training a weight for them and
discarding it at config-write time would be a train/eval mismatch, caught
before it mattered). L2-regularized **toward the current tuned weights**,
not toward zero — a small correction, not a from-scratch fit. 1,038 pairs
mined from 100 train-fold sessions (target vs. every candidate currently
outranking it — same construction `tools.why_lost` already uses).

Tested across 6 hyperparameter settings, aggressive to conservative:

```
lr=0.02  l2=0.08 epochs=40  train +0.0000→-0.1183  holdout -0.0956  (initial run, before a minor fix)
lr=0.02  l2=0.08 epochs=40  train -0.1182           holdout -0.0955  (after excluding frozen bm25_description/store)
lr=0.003 l2=0.5  epochs=10  train +0.0087           holdout -0.0137
lr=0.005 l2=0.4  epochs=12  train +0.0048           holdout -0.0173
lr=0.003 l2=0.3  epochs=15  train +0.0042           holdout -0.0219
lr=0.002 l2=0.2  epochs=20  train -0.0108           holdout -0.0224
lr=0.001 l2=1.0  epochs=5   train +0.0101           holdout -0.0032  (gentlest tested)
```

**Every single setting regressed holdout, monotonically worse as more
freedom was given to the fit** — the more the weights were allowed to move
from the tuned baseline, the worse holdout got, even as train sometimes
looked slightly better. This is a clean, textbook overfitting signature
(not a bug — verified the gradient math is directionally correct via the
gentlest setting, and not noise — the pattern is monotonic across the
entire grid), and it is exactly the failure mode this session's earlier
`w_fused_browsing`/bm25-phrase experiments already demonstrated with far
fewer free parameters. **Stopped the hyperparameter search deliberately at
6 points rather than continuing to hunt for a lucky setting** — further
search would itself be exactly the selection-on-noise problem this file's
own measurement-discipline section warns about.

**Net result: 100 train-fold sessions do not support jointly refitting 33
linear weights, even with strong L2 shrinkage toward a known-good starting
point.** This is the same conclusion the research report's data-honesty
section (§7 of `ML-Research-Report.md`) predicted before any code was
written — this experiment is the empirical confirmation of that prediction,
not a new surprise. `config/tuned.json` untouched throughout — every trial
scored a scratch config copy via `evalkit.Bench`, never the live one.
Per the PRD's own stop criterion, Phase 6 (integration) does not happen.
`tools/train_pairwise.py` is kept in the repo as a reusable diagnostic —
useful again if the private 800-session set or a future data refresh ever
changes the "not enough data" conclusion.

**Learned reranking closed: seven attempts including LambdaMART, none beats the
incumbent on held-out data (2026-08-30, He Jinhong):**

Built the separability gate from `PRD_pairwise_rerank.md` Step 3 and ran the
whole comparison offline. **Conclusion: no linear reranker over the current
feature vector improves held-out performance, regardless of objective. Every
attempt regressed fold B — a direction failure, not a margin failure. Do not
rebuild this.** `config/tuned.json` and `results.json` untouched throughout.

**What was tested, and the numbers:**

| attempt | search procedure | fold A (fitted) | fold B (held out) |
|---|---|---|---|
| `w_fused_browsing` (Dylan H1) | grid over 7 values | monotonically worse | — |
| bm25→phrase delta (Dylan H2) | coordinate ascent, shared delta | +0.0117 | −0.0045 |
| generic pairwise | hand-rolled logistic, 855 pairs | +0.0116 | −0.0503 |
| payoff-weighted pairwise | hand-rolled, pairs weighted by TechnicalScore delta | +0.0044 | −0.0716 |
| CLiMF | smooth RR lower bound | +0.0010 | −0.0430 |
| **regularised pairwise** | **sklearn `LogisticRegression`, C swept 1e-3..1e2, selected by inner validation inside fold A** | **+0.0257** | **−0.0458** |
| **LambdaMART** | **LightGBM `lambdarank`, 8 configs (`num_leaves` 2-15, 15-200 trees), same inner validation** | **+0.0175** | **−0.0080** |

**The bound that matters: the best in-sample gain any method achieved is
+0.0257** — model fitted directly on the fold being scored, zero generalisation
loss. **Every one of the six regressed fold B**, by −0.0045 to −0.0716 — the
problem is direction, not margin. The in-sample ceiling is recorded so the next
person knows the size of the prize before spending anything on it, not as a
threshold anything failed to clear.

**LightGBM is the best of the seven, and still not a gain.** LambdaMART reached
fold B **−0.0080** — inside noise, i.e. a *tie* with the incumbent, against
−0.0430 to −0.0716 for every linear attempt. That is the predicted result of the
joint-infeasibility finding below: no single weight vector can win the 39 rank-2
pairs while keeping the 109 already at rank 1, but a tree ensemble is not
restricted to one global direction and can carve the space. So trees genuinely
help with the *conflict* — they just have no new information to exploit.
Across eight configurations spanning `num_leaves` 2-15 and 15-200 trees, **not
one beat the incumbent on inner validation** (best A2 0.7667 vs 0.8037), and the
score was flat-to-worse across the whole capacity range. That is the profile of
no signal left to extract, not of an undertuned model. Shipping it would also
mean a runtime dependency, or a tree-dump walker, for a result that is at best a
wash.

**The cleanest evidence needed no holdout at all.** In the sklearn run, fold A
was split again (A1/A2, seed 11) and every one of twelve configurations — six
regularisation strengths × two weightings — scored **below the incumbent** on
A2 (best 0.7820 vs incumbent 0.8037). The selection procedure never found a
model worth promoting, before fold B was looked at once. The sweep also selected
`C=100`, the *weakest* regularisation offered, which is the overfitting
signature rather than a cure for it.

**Why it fails, mechanically.** The rank-2 diagnostic (entry below) showed the
imposter genuinely out-BM25s the target and all 12 constraint columns contribute
exactly 0.0% of the gap. The separating information is not in the vector, so no
reweighting recovers it. Two useful sub-results from the gate:

- **The 39 rank-2 pairs *are* linearly separable in isolation** (perceptron, 4
  epochs), and this is real signal, not a dimensionality artifact — only 1/20
  randomly sign-flipped control sets were also separable.
- **But not jointly with the 109 sessions already at rank 1.** Best joint fit:
  30/39 rank-2 fixed while losing 6/109 rank-1, ≈ +0.018 in-sample. Winning the
  39 requires inverting `popularity` / `bm25_title` / `bm25_features`, which is
  precisely what destroys the sessions already won.

**Caveat, recorded honestly:** the payoff objective sat flat at 0.6080 across
five of six C values, suggesting its weights are so skewed toward the single
fold-A miss session that most pairs are effectively ignored. It is fairly
judged "not viable as specified", not "refuted in principle" — but it would have
to close a 3× gap and it starts furthest away.

**What this retires:** the GBDT / LambdaRank line for these cases too — and that
is now *measured*, not inferred, since LightGBM was actually run (row 7 above).
The blocker was never the learner.

**What to do instead:** a new feature. The unexplored next step is to read the
actual `title`/`features` text of the 39 rank-2 targets against their winners and
find what distinguishes them, rather than assuming a global weight fix is the
right shape of fix at all.

**Artifacts:** `tools/separability.py` (the gate, stdlib-only, reusable for any
rank bucket). The trainer, comparison harness and `ranking.model_path` config
field described in `PRD_pairwise_rerank.md` were **never built** — the gate
answered the question first, which was its purpose.

**Rank-2 diagnostic run; two well-motivated fixes tested, neither survives holdout (2026-08-30, Dylan Huang):**

Ran `why_lost --ranks 2` for the first time (previous diagnostics targeted
3-5, back when the distribution was flatter). 39 sessions, 39 (session,
winner) pairs. Feature-contribution breakdown of the score gap:

```
bm25_title      46.9% share, mean_gap +0.0667, against 67%
bm25_features   39.9% share, mean_gap +0.0567, against 56%
phrase_title    10.2% share
fused           10.1% share
dense            7.7% share
(all 12 constraint columns: exactly 0.0% share, every one)
popularity      -6.9% share (favors target, not enough)
has_price       -6.5% share (favors target, not enough)
coverage        -5.5% share (favors target, not enough)
```

Structured constraints are not the story here — confirms the earlier
finding still holds at rank 2. The imposter wins on raw BM25 title/features
match; nothing else is strong enough to overcome it in these 39 cases.

**Hypothesis 1 — `w_fused_browsing` partial reduction.** `fused` still
carries 10.1% of the gap and shows up as a top-3 contributor specifically
in browsing sessions (12/39). `w_fused_browsing` has been `None` (full
`w_fused=1.0`) this whole time — the intent-conditional fix only zeroed
buying/uncertain. A global zero-cut for browsing was already tested and
rejected (regresses browsing −0.0118), but no intermediate value had been
tried. Tested `[1.0, 0.85, 0.7, 0.5, 0.3, 0.15, 0.0]` against
`stratified_halves(seed=7)` train fold (100 sessions), live tuned config as
base: **every reduction made train strictly worse, monotonically**
(0.8496 → 0.8398 at full zero). No missing middle ground — browsing
genuinely needs the full signal, confirmed at finer granularity than
before. **Not adopted.**

**Hypothesis 2 — trade weight from `bm25_title`/`bm25_features` toward
`phrase_title`/`phrase_features`.** These interact and were only ever
tuned one-at-a-time by coordinate ascent, which can miss a jointly-better
point. Tested a shared delta `[0.0, 0.05, 0.10, 0.15, 0.20, 0.30]` moved
from the bm25 pair to the phrase pair. **Delta 0.15 improved train**
(0.8496 → 0.8613, +0.0117) **but regressed holdout** (0.8747 → 0.8702,
−0.0045) — the exact fold-disagreement `tools/tune.py` itself warns about
("the gain is fold-specific; ship the defaults rather than these values").
**Not adopted** — this is overfitting to the 100 train sessions, not a
real improvement.

**Net result: both well-evidenced hypotheses tested cleanly, neither
survives holdout. `config/tuned.json` untouched — no config-path changes
from this pass.** This is a legitimate outcome, not a failed session: it
rules out the two most obvious reweighting fixes with real evidence,
narrowing what's left. Recorded rather than silently dropped, per this
file's own rule that a negative result is worth writing down.

**Next things worth trying, unexplored:** (a) per-scenario or per-intent
`w_bm25_title`/`w_bm25_features` split, rather than a global one — the
buying-scenario share (21/39, `bm25_features +0.0826`) and the
intent_override share (5/39, dominated by `bm25_title` AND `popularity`)
look different enough that one global trade-off may be averaging away two
separate problems; (b) manual inspection of a handful of the 39 sessions'
actual title/features text (target vs. winner) to check whether there's a
discoverable pattern (e.g. winner is a near-duplicate listing, or the
customer's phrasing genuinely undersells the target) rather than assuming
a global weight fix is the right shape of fix at all.

**Dialogue block was untunable, and one dead method shipped (2026-08-30, Dylan Huang):**

`tools/tune.py`'s `SEARCH_SPACE` had 20 parameters and zero from
`DialogueConfig`, even though ablating clarification costs −0.4473 — the
single largest ablation effect in the system. Two values that clearly
matter were hardcoded default args, not config: `state.observe`'s
`override_decay=0.25` (`state.py:190`) and `state.query`'s
`recency_bonus=0.15` (`state.py:246`). Promoted both to
`DialogueConfig.override_decay` / `.recency_bonus`, wired them through
`agent.py`, and added both to `SEARCH_SPACE` under `dialogue.` so the
tuner can actually search this space — it never could before.

Separately, found `ShopperProfile.quality_bias()` (`profile.py`) was dead
code: defined, never called anywhere in `shopping_copilot/` (verified by
grep). Its only dependency, `is_critical`, was used by nothing else either.
Removed both rather than wiring `quality_bias()` in — `CLAUDE.md`'s own
roadmap already called it "still undecided, ±0.002," not worth the time
against the higher-value dialogue-tuning and rank-2 work in flight.

**Verification:** all defaults preserved exactly (`override_decay=0.25`,
`recency_bonus=0.15` — same numbers, just promoted to config), so this is
a pure refactor. Confirmed via `python -m unittest discover -s tests`
(32/32 pass) and a full live evaluator run: `TechnicalScore` unchanged at
**0.862111**, byte-identical to before. `config/tuned.json` was not
touched, so this commit does not move the score — it only makes the
dialogue block reachable by the tuner for the first time.

**Follow-up, same day: tuned the newly-exposed space, found no gain.** Ran
a scoped coordinate-ascent pass over just `dialogue.override_decay`
(`[0.0, 0.15, 0.25, 0.4, 0.6]`) and `dialogue.recency_bonus`
(`[0.0, 0.08, 0.15, 0.25, 0.4]`) against the `stratified_halves(seed=7)`
train fold (100 sessions). **Neither improved on the existing hand-picked
defaults** — `override_decay=0.25` and `recency_bonus=0.15` were already
at (or tied for) the best score in every candidate tested, on both train
(0.7644) and holdout (0.7725). `config/tuned.json` unchanged, nothing to
adopt. Worth recording as a negative result: the two hardcoded defaults
this branch promoted to config were already good, at least across this
grid — a finer grid or a joint search with other parameters might still
find something, but a lone coordinate-ascent pass over just these two
did not. One data point on tuner cost: ~12.4s per 100-session train-fold
score with a warm agent (`Bench.apply_config`, no index rebuild) — a full
~22-parameter pass at 4-5 candidates each would run roughly 15-20 minutes.

**State audit — the docs had drifted from the code (2026-08-30):**

Re-measured everything rather than trusting the written record, after the docs
and the code disagreed. Four corrections:

- **Live score is 0.862111, not 0.7848.** Nothing on disk recorded it — the
  newest saved artefact (`results.json`, 0.8476) was written eight hours before
  `config/tuned.json` was last edited, so the live config had never been
  evaluated into a file. Re-ran it to find out. **Lesson: after changing
  `config/tuned.json`, run the evaluator and record the number, or the next
  person inherits a config nobody has a score for.**
- **Intent-conditional weighting was already adopted.** CLAUDE.md called it an
  open decision; `config/tuned.json` had `w_fused_buying: 0.0` /
  `w_fused_uncertain: 0.0` set the whole time. The fold-B caution below was
  overtaken by events.
- **The rank distribution moved.** The old "93 hits at ranks 2-10, mass at 3-5"
  is gone. It is now 83 below rank 1 with the mass at **rank 2 (39 hits)** —
  which redirects the highest-value next step (see "Remaining headroom").
- **Misses fell 18 → 8.** The old miss analysis (12 in-pool, 6 true retrieval
  failures) describes a build that no longer exists.

**Double-counting in the fused signal (2026-08-03):**

`fused` is a convex combination of lexical and dense signals that also enter the
feature vector separately — text evidence was counted twice. At ranks 3-5 it
carried **68% of the score gap**, while constraint features carried **0.0%**
(target and candidates were identical on all constraints).

**The fix:** intent-conditional weighting.
- Global cut regresses browsing (−0.0118) — vague browsing has no constraints to
  suppress, so the fused score is its best available signal.
- Intent-scoped cut (buying/uncertain only): +0.0312, improves all 4 scenarios.
  The sign flip in the held-out slice is the real evidence, not the headline.

**Implementation:** `w_fused_{buying,browsing,uncertain}` on `RankingConfig`,
each falling back to `w_fused` when unset. `Ranker` builds one `LinearModel` per
intent, selected on `ctx.intent`. The per-intent map is only built when no
external `model` is supplied, so the `ScoringModel` seam stays free for GBDT
work later.

**Status:** adopted and live — see the 2026-08-30 entry above. (This entry
previously read "not adopted, fold B only justifies +0.0121". That caution was
written before the change shipped; it did ship, and the current 0.862111 is
measured with it in.)

---

## Remaining headroom

**Superseded 2026-09-XX by the `per_field_depth` fix above — the numbers below
predate it and are now wrong in the specifics.** Misses fell 8 → 3 and
`target_never_in_pool` fell 6 → 1, so "6 targets believed unreachable" and the
39/83/8-hit rank distribution this table is built on no longer describe the
live system. The *qualitative* lessons still hold (rank-2 is reranking-hard,
gated on new features not weights per the LightGBM finding below) — the
specific hit-counts do not. Kept rather than deleted per this file's own rule;
a fresh `why_lost` + rank-distribution pass against the new config is the
correct next step before trusting any number in this section again.

**Current 0.862111 (stale, see above). Practical ceiling ~0.970** (6 targets believed unreachable,
so HR@10 caps near 0.97, which caps MRR at 0.97 too). Headroom ≈ **+0.108**,
and **~78% of it is MRR**.

Recomputed 2026-08-30 from the live run's per-session `best_rank` distribution
(109 hits at rank 1, 83 below it, 8 misses):

| opportunity | hits | score available |
|---|---|---|
| **rank 2 → 1** | 39 | **+0.0292** ← single biggest bucket |
| ranks 2-5 → 1 | 64 | +0.0565 |
| ranks 6-10 → 1 | 19 | +0.0246 |
| perfect rerank of every hit we already find | 83 | **+0.0811** |
| converting all 8 misses | 8 | ~+0.035 (HR@10 +0.02, MRR +0.012, plus MTTC) |

**Rank 2 is the biggest bucket, but it is not reachable by reweighting.** 39
sessions retrieve the target and place it one position off. The table above is
what those swaps are *worth*, not what is *available* — seven reranking attempts
have now failed against them, LightGBM `lambdarank` included (see "What was
found"), so treat every row here as gated on the feature vector gaining new
information, not on a better weighting or a better learner.

The measured bound: any linear scorer that wins all 39 rank-2 pairs must invert
`popularity` / `bm25_title` / `bm25_features`, which loses sessions already at
rank 1. Best joint result was 30/39 fixed against 6/109 lost, ≈ +0.018 in
sample.

**The 8 misses** split browsing 5 / buying 2 / intent_override 1. Browsing is
the weakest track on every metric (HR@10 0.9375, MRR 0.5966) and is where the
remaining recall loss lives. **This is the least-explored row in the table** and
the one not yet ruled out by anything.

**Next step:** read the actual `title`/`features` text for the 39 rank-2 targets
against their winners and find what distinguishes them — a feature question, not
a weighting question. `tools/separability.py` will re-answer the bound for any
new feature set in seconds once a trace exists.

## Gradient boosting — TESTED 2026-08-30, does not help

**No longer future work: it was built and measured.** LightGBM `LGBMRanker`
with `objective="lambdarank"`, grouped by session, fitted on fold A with
hyperparameters selected by inner validation inside fold A (fold B untouched
until the final measurement).

```
              HR@10      MRR    MTTC     Score
fold A inc   0.9500   0.6745   2.390   0.8496
fold A lgbm  0.9500   0.7201   2.200   0.8670   +0.0175
fold B inc   0.9700   0.7049   2.090   0.8747
fold B lgbm  0.9500   0.7136   2.120   0.8667   -0.0080
```

**Result: a tie, not a gain.** −0.0080 on fold B is inside noise. Eight
configurations were tried (`num_leaves` 2-15, 15-200 trees, `min_child_samples`
10-80); **none beat the incumbent on inner validation** (best A2 0.7667 vs
0.8037), and the score was flat-to-worse across the entire capacity range —
the signature of no remaining signal, not of undertuning.

**Worth recording as a positive finding:** trees are the best learned result of
the seven attempts by a wide margin (−0.0080 against −0.0430 to −0.0716 for
every linear model). That is exactly what the joint-infeasibility result
predicts — no single weight vector can win the 39 rank-2 pairs while keeping the
109 already at rank 1, and a tree ensemble is not restricted to one global
direction. **Trees solve the conflict; they cannot manufacture the missing
information.**

**Two things a future attempt must not re-derive:**
1. **A pairwise setup does not transfer to trees.** For a linear model
   `w·(a−b) = w·a − w·b`, so training on difference vectors yields a pointwise
   scorer for free. For a tree `f(a−b) ≠ f(a) − f(b)` — LambdaRank proper is
   required (pointwise scorer, pairwise-derived gradients), which sklearn does
   not provide and LightGBM does.
2. **The unknown-penalty problem is solved.** `tools/separability.py:augment()`
   derives the six `{dim}_unknown` indicators from the existing vector inside the
   trainer, so `FEATURE_NAMES` never has to change. Reuse it.

**Reopen only if the feature vector gains real discriminating information** —
e.g. from the clarification work. Trees over the same 30 columns have been
measured and do not pay. Note also that shipping LightGBM means a runtime
dependency or a tree-dump walker, which is a real cost for a result that is at
best a wash.

---

## Roadmap (ordered by expected impact)

Measured against the live 0.862111, not `docs/pending.md`'s 0.7848-era figures.

| priority | item | impact | notes |
|----------|------|--------|-------|
| **high** | **New feature for the rank-2 cases** | ≤ +0.0292 | the only remaining route to those 39 sessions. Reweighting is closed (see "What was found"). Start by reading the actual `title`/`features` text of target vs. winner for the 39 |
| high | Browsing recall | ~+0.02 | 5 of 8 misses and the worst MRR (0.5966) are browsing. Untouched |
| medium | Ranks 6-10 → 1 | +0.0246 | 19 sessions. Same caveat as rank 2 — assume a feature problem until a diagnostic says otherwise |
| low | Learn `TAG_LEXICON` instead of hand-writing it (P4) | +0.0010 measured | `profile.py:22`; the feature it feeds ablates to inert |
| low | Decide the fate of the two inert components (P5) | ±0.002 | still undecided; both still shipped |
| ~~closed~~ | ~~Rank 2 → 1 by a learned reranker~~ | — | **seven attempts (5 linear, 1 sklearn, 1 LambdaMART). Best held-out result is LightGBM at −0.0080, a tie; every linear one regresses −0.0430 to −0.0716. Do not rebuild** |
| ~~closed~~ | ~~Tune the dialogue block (P0)~~ | — | done; both promoted parameters were already optimal on the grid tested |
| ~~closed~~ | ~~GBDT / LambdaRank (P2)~~ | — | **tested: LightGBM lambdarank, fold B −0.0080 (a tie), 8 configs, none beat the incumbent on inner validation. Reopen only if the features gain new information** |

**`docs/pending.md` status** (written at 0.7848, so treat its numbers as
historical): P0 **done, no gain** · P1 **done**, via per-intent ranking weights
rather than the retrieval multipliers it proposed · P2 **tested and closed**
(LightGBM lambdarank, fold B −0.0080, a tie) · P3 diagnosed but superseded (18 misses → 8) · P4 untouched ·
P5 undecided.

## RESOLVED — modified evaluator on `origin/main`

**Resolved 2026-08-30.** Reverted on `origin/main` by `fa8f9a9 "revert: restore
pre-Phase4 local_evaluator.py"` and `fb17283 "revert: restore pre-Phase4
tests/test_evaluator.py"`. Verified: `git diff upstream/main origin/main --
evaluator/local_evaluator.py` is empty, i.e. byte-identical to the organizer's
copy. No submission risk outstanding.

**Re-check this before submitting** — it is one command, and the failure is
silent:

```bash
git diff --stat upstream/main <your-branch> -- evaluator/
# any output at all = the evaluator has been modified again
```

The original entry is kept below rather than deleted, because the way it got in
matters more than the fix: it arrived under a commit message that said `docs:`.

---

### Original entry — modified evaluator on `origin/main`

**`origin/main` carries a modified `evaluator/local_evaluator.py`** (+237/−64
vs `upstream/main`), introduced in commit `7303cea`, whose message reads
*"docs: add phase 4 implementation report"*. `maximise-mrr`'s copy is clean and
byte-identical to the organizer's.

[docs/submission_rules.md:51](docs/submission_rules.md#L51) lists **"code that
modifies evaluator files"** among prohibited submissions.

The change itself is the `dylan-data-error` robustness work (`validate_session()`,
per-session failure isolation, 35 new tests) and
`docs/DATA_ROBUSTNESS_IMPLEMENTATION.md` states the public-set metrics come out
identical — so it is not score manipulation. But the rule has no exception for
well-tested changes, and it sat on the branch a submission would be cut from.
**Superseded by the resolution above — kept as the record of how it happened.**

## Out of scope for current work

Noted so they don't creep into the roadmap:

- **Fine-tuning or training a foundational LLM.** Explicitly out per the
  submission rules. Local scoring logic is in; model training is not.
- **Adding runtime dependencies.** See Critical rule 5. Separate decision.
- **Modifying the evaluator or the catalog** to make a number move. Rules 3 and 4.
- **Chasing HR@10 past ~0.97.** Six targets are genuinely unreachable; the
  remaining headroom there is +0.020 against +0.101 in MRR.
- **Popularity debiasing / calibrated recommendation.** That literature exists to
  correct a bias these labels contain *by construction*. Applying it means
  fighting the metric. The honest framing already in the README is the right
  response, not a technical fix. (`agent_architecture.md:250`)
- **The RL clarification policies** (SCPR, UNICORN). They assume clean per-item
  attribute sets this catalog does not have. The EAR gate is the part that
  transfers.
- **LLM reranking.** Order-sensitive, therefore non-deterministic for a graded
  submission, and official scoring may run without network access.
- **Refactoring for its own sake.** Out because it changes no measured outcome,
  not because of any deadline — restructuring that demonstrably enables a
  measurable gain is in scope like anything else.

---

## Working with He Jinhong

- Direct and decision-focused. Prefers options plus a recommendation over
  open-ended questions.
- Pushes back when something is wrong — don't be defensive, just fix it.
- **Authorship is the person who made the change** — whoever on the team did the
  work, not always He Jinhong. If someone else's work is going in, credit them
  (`Co-Authored-By:` with their name and email).
- **Never add Claude as an author or co-author.** No `Co-Authored-By: Claude`
  trailer, no "Generated with Claude Code" line, on any commit or PR.
- Work lands on a personal branch and merges into `main` by PR.
- Wants the reasoning recorded, not just the result — a change without its
  measurement and its tradeoff is not finished.
- **Do not factor timeline into recommendations.** Rank options by expected
  impact and by what the evidence supports, never by what fits before a
  deadline. Do not defer, shrink, or drop an option because it "takes too long",
  and do not write "not enough time before submission", "not this week", or
  "park it for later" — scheduling is He Jinhong's call, not yours. State what
  an option is worth and what it costs to build, then let him decide.
  Effort estimates are fine when asked for; using them to pre-reject an option
  is not.

---

## Keeping this file current

**The mechanism is a convention, not automation:** when a session produces
something material, the CLAUDE.md edit goes into the *same commit* as the change
itself. Nothing happens on push. This file stays true only because whoever made
the change wrote down why, while they still remembered.

**One mechanical guard exists.** `.githooks/pre-commit` refuses a commit that
stages `config/tuned.json` without also staging `CLAUDE.md`, since that is the
exact shape of the 2026-08-30 drift. It checks only that you touched the file,
never that the number is right, and `--no-verify` bypasses it.

**Every clone must run this once** — the hook script is tracked and travels with
the repo, but the setting that activates it is local config and does not:

```bash
git config core.hooksPath .githooks
```

Without it git keeps looking in `.git/hooks` and the guard silently never runs.
Worth pasting into the PR description when this lands, since nobody will read a
hook they don't know exists.

**Material means any of:**
- the score moved (either direction — regressions are the most valuable entries)
- `config/tuned.json` changed
- a finding that would cost time to rediscover
- a decision made and its reason (especially a decision *not* to adopt something)
- a new operational fact about the evaluator, the tooling, or the data

**Not material:** refactors that don't change behaviour, experiments abandoned
before measurement, anything already recorded in the git history alone.

**What an entry looks like:** date, what changed, what it measured, and *why*
the call went the way it did. The 2026-08-30 state-audit entry is the template —
the useful part is "the live config had never been evaluated into a file", not
"0.862111". A number without the reason it moved is a number the next person
cannot act on.

**The failure mode this file exists to prevent** is the one the 2026-08-30 audit
found: the score moved from 0.7848 to 0.8621 and no document knew. If you change
`config/tuned.json`, the same commit re-runs the evaluator and updates the
"Current state" table. No exceptions — an unrecorded score is worse than no
score, because it looks trustworthy.

**Where things go:** score/config → "Current state" table. Findings and
decisions → "What was found" (newest first). New risks → "Critical rules" or
"Critical evaluator facts". Everything else → "Roadmap" or "Out of scope".

### Three conventions that make entries readable months later

**1. Quote the request verbatim, with a date and who asked.**
`**Dropped MMR entirely (2026-09-02, per Joey: "it's doing nothing, why is it
still here")**`. A paraphrase records the decision; the quote records the
*reasoning behind* the decision, which is what you need when deciding whether
it still holds. Attribute to the person, not to "the team".

**2. Supersede, never delete.** When something is reversed, leave the original
entry and mark it: *"Superseded 2026-09-04 — see below"*, *"added 2026-09-01,
removed 2026-09-03 because…"*. A file that only shows what survived teaches
nothing about what was already tried and failed, and someone will retry it.
This includes your own errors — *"this entry claimed X; that was wrong, caught
by He Jinhong"* is one of the most useful lines the file can contain.

**3. Always state verification status, especially when it is bad.** *"Verified
end-to-end against all 200 sessions"* and *"not measured — no trace available
yet"* are both useful; an entry with no status silently reads as verified. Say
what you
ran, on what, and what you did not run. If a result came from a fold, a subset,
or a single seed, say so — see "Measurement discipline".

Check `git log -- CLAUDE.md` to see when it was last synced.
