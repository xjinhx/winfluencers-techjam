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

**Live score: `TechnicalScore = 0.942939`, HR@10 = 1.000, MRR 0.902464,
MTTC 2.390 — measured 2026-08-31 on the `arwen` + `investigation` merge with
the unmodified CLI evaluator over all 200 public sessions.** 168 of 200 at
rank 1, zero misses, 56/56 tests.

**The two recommendation-hold gates were built in parallel and compound.**
They read different signals and neither supersedes the other, so
`config/tuned.json` carries both and `agent.py` applies both:

| config | full 200 | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| neither gate | 0.909328 | 1.0000 | 0.756095 | 1.875 |
| `recommend_min_spans: 1` alone (arwen) | 0.928002 | 1.0000 | 0.833673 | 2.105 |
| `min_recommend_confidence: 0.054` alone (investigation) | 0.936614 | 1.0000 | 0.876048 | 2.310 |
| **both (merged, live)** | **0.942939** | **1.0000** | **0.902464** | 2.390 |

The combination is **+0.006325 over the better single gate for +0.08 turns of
MTTC** — they are not redundant: one asks whether the *customer* has said
anything concrete, the other whether the *ranker* has committed, and a turn can
fail either test independently.

**Read MRR 0.902464 against the ceiling.** This file's own span-rescoring
ceiling for full-card disclosure is MRR ~0.9025. The merged gates land on it,
which says the disclosure-*timing* lever is now essentially spent: further MRR
has to come from ranking the disclosed evidence better, not from waiting longer.

**Verification status, stated because it is mixed.** The full-200 figure is
measured on the unmodified evaluator. **The combination has NOT been fold-split**
— each gate was validated on folds independently (arwen fold B +0.0193;
investigation fold B +0.0259), but no held-out number exists for the pair, and
fold B has now been looked at six times across both branches. Treat 0.942939 as
in-sample and expect the private 800 lower.

Both lineages below are **superseded by the merged figure above** and kept
only for the measurement chain. Neither is the live score.

**Superseded lineage — `investigation` branch alone, `TechnicalScore = 0.936614`,
HR@10 = 1.000.** Measured without the `recommend_min_spans` gate. This is `0.909328` (below) plus the confidence-gated
recommendation hold (+0.027286, see the top "What was found" entry).
`config/tuned.json` now carries `min_recommend_confidence: 0.054` and
`recommend_turn_fallback: 3`; every other value unchanged. Gates: the
unmodified CLI evaluator reproduces it exactly; `tools.offline_eval`
against a fresh 95,665-row trace agrees on **200/200** session
`best_rank`s; `target_never_in_pool` **0**; 56/56 tests pass (51 + 5 added
for the gate). Default-off (`min_recommend_confidence: 0.0`) re-measures
**0.909328 byte-identically**, which is the rollback path.

Prior figures, kept for the chain: up from **0.909328** (synthetic span
normalisation on top of the injection) from **0.908578** (the conjunctive
injection, `46a6294` plus the injection change) from **0.903753** (that
tree with the old code) / **0.903604** (`d2f12ac`, the last
committed-trunk measurement).

**Superseded lineage — `arwen` branch alone, `TechnicalScore = 0.928002`,
HR@10 = 1.000.** Measured without the `min_recommend_confidence` gate, 2026-08-31,
by running the committed
`config/tuned.json` through the unmodified CLI evaluator on all 200 public
sessions, with `recommend_min_spans: 1` adopted (top "What was found" entry):
0.909328 → **0.928002**, MRR 0.756095 → 0.833673, MTTC 1.875 → 2.105, hit rate
unchanged at 1.000. 49/49 tests pass.

*The paragraph below records the prior measurement chain and is kept for the
lineage; 0.908578 and 0.909328 are earlier steps, not the live number.*
Measured 2026-08-31 on the `mega-fix` working tree (`46a6294`
plus the conjunctive-injection change, uncommitted at time of writing) by
running the committed `config/tuned.json` through the unmodified CLI
evaluator on all 200 public sessions. The injection is **always-on in code**
(no config flag — see the top "What was found" entry), so the committed
config produces this number as-is. Up from **0.903753** (this tree with the
old code) / **0.903604** (`d2f12ac`, the last committed-trunk measurement).
Gates: `tools.evalkit.Bench` reproduces it exactly; `tools.offline_eval`
against a fresh trace agrees on **200/200** session `best_rank`s;
`target_never_in_pool` **1 → 0**; 49/49 tests pass.

**Tree note (2026-08-31):** `mega-fix` at `46a6294` (the arwen and
maximising-101 merges) re-measured the committed config at **0.903753**
before the injection work — +0.000149 of drift from those merges that no
document had recorded, noise-level but written down per this file's own rule.
The test suite is 49, not the 38 recorded for trunk.

**0.900004 is one step further back, not the live number — read it as "trunk
before this session's depth change."** It is the merged-trunk figure this same
file already flagged as a correction to two stale parallel-branch numbers
(`span_all` 0.888187, title/coverage 0.892242) — that correction stands, and
this entry sits on top of it, not in tension with it.

**On 800 vs. 1000 (`per_field_depth`) — reopened and resolved 2026-08-31,
superseding the paragraph below.** The 2026-08-30 reconciliation kept `800`
because the two values were statistically indistinguishable *at that config* —
true at the time, but `span_all` (landed the same day) changed what depth
buys: it gave the ranker a feature that can actually use a target once depth
makes it visible, which the 39-rank-39 trace behind the old "structurally
unwinnable" verdict on `public_0092` never had. Re-tested with a pool-recall
measurement across the full depth grid (200 sessions, every `per_field_depth`
value 200 through 50,000 × every `candidate_depth` up to 800): retrieval
recall **saturates completely between 600 and 800** — 800→50,000 buys **0.0
percentage points**, so this is not "1000 is a little better," it is "800 was
already at the knee and 1000 is the next reachable point that has HR@10
headroom." Validated with `stratified_halves(seed=7)`: fold A **−0.0029**,
fold B **+0.0101** — held-out beats fitted, the signature this file already
trusts from `span_all`. `candidate_depth` widening was tested as the
alternative fix and rejected (see the new "What was found" entry) — it
converts the same misses but costs MRR through dilution and loses to depth on
fold B. **The 2026-08-30 "don't re-litigate" note was correct for the config
it was written against; it does not survive `span_all` shipping, and this is
the re-measurement that note itself called for should new evidence appear.**

| metric | value | (was, pre-gate) |
|---|---|---|
| HR@10 | **1.000** (0 misses) | 1.000 |
| MRR | 0.876048 | 0.756095 |
| MTTC | 2.310 | 1.875 |
| Efficiency | 0.869 | 0.9125 |
| **TechnicalScore** | **0.936614** | 0.909328 |

| scenario | n | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| buying | 80 | 1.0000 | 0.9056 | 1.825 |
| browsing | 80 | 1.0000 | 0.8549 | 2.2375 |
| intent_override | 30 | 1.0000 | 0.8806 | 3.70 |
| boundary | 10 | 1.0000 | 0.7950 | 2.60 |

**`intent_override` is byte-identical across the change** (MRR and MTTC both
unmoved to four decimals) and that is a correctness check, not a
coincidence: those sessions cannot register a hit before their override turn
(3 or 4), which is at or past `recommend_turn_fallback = 3`, so the gate can
never bind for them. If a future threshold change moves this row, the gate
is firing somewhere it should not.

**Rank distribution, superseded by the gate — recompute before quoting.**
The pre-gate distribution was 125 rank 1 / 31 rank 2 / 35 ranks 3-5 / 9
ranks 6-10. The gate moved **39 sessions up and 1 down, losing none**, so
those counts no longer describe the live build; the gated run's
per-session `best_rank`s are in `c:/tmp/out_conf_gate.json` (scratch,
regenerable) rather than restated here, because this file has twice carried
a distribution that a later change silently invalidated. With HR@10
saturated at 1.000, remaining headroom is still MRR plus efficiency, but
efficiency is now the *spent* term rather than the free one — see the gate
entry in "What was found" for the ceiling this leaves.

**Intent-conditional weighting is adopted and live.** `config/tuned.json` sets
`w_fused_buying: 0.0` and `w_fused_uncertain: 0.0` against `w_fused: 1.0` — this
is no longer an open decision, and any doc saying otherwise is stale.

**Stale artefacts — do not quote these as the current score:**
- **0.909328** is the pre-gate figure — honest, and still the number the
  build produces with `min_recommend_confidence: 0.0`, which is why it is
  the documented rollback target rather than simply an old number.
  Superseded 2026-08-31 by 0.936614.
- **0.903604 and 0.903753** are the pre-injection figures — `d2f12ac` and the
  `46a6294` working tree respectively, both honest, both superseded
  2026-08-31 by 0.908578 when the conjunctive injection became always-on.
- **0.900004** was the merged-trunk figure *before* this session's
  `per_field_depth` 800→1000 change. It is a correct, honest measurement of
  `d2f12ac` at that config — kept as the reference point the depth change is
  measured against, not an error — but it is not the live score. Superseded
  2026-08-31 by 0.903604.
- **0.888187 and 0.892242** are each *one* of the two parallel branches, not the
  trunk. Both are honest measurements; neither describes `d2f12ac`. Superseded
  2026-08-31 by 0.900004, itself now superseded by 0.903604.
- **0.881716** predates the title/coverage interaction adopted below.
- **0.862111, 0.863556, 0.876336, and 0.876342** (and the miss lists that went
  with them) all predate the `constraint_commonness_penalty` fix below.
  0.862111 is pre-merge-with-main; 0.863556 is post-merge-pre-fix;
  0.876336 was Dylan's own `per_field_depth=1000` before reconciling with
  Joey's `800` already on `main`; 0.876342 is the reconciled `800` value,
  current on `main` but superseded on this branch.
- `results.json` = **0.895185** (checked 2026-08-31; the entry below said
  0.881716, which was already out of date). It predates the current config and
  was preserved rather than overwritten per Critical rule 1. It is *not* a
  trunk measurement of anything — it was written 19:30 on 2026-08-30, before
  `ab4e55c` landed at 22:20. `results_tuned.json` = **0.784838**, older still.
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
python -m unittest discover -s tests    # 38 tests (35 copilot + 3 evaluator)
```

**Tracing:** set `trace_path` in the config to emit `features.jsonl` (~115k
rows, ~45 MB per run). Tracing is passive and verified not to change results.

**Validation gate:** `offline_eval.py` reproduces the ranker exactly. All 200
sessions must agree on `best_rank` per session, not just on aggregate MRR.

---

## What was found

*Append-only. Newest entries at the top, each dated, each with the reasoning —
not just the outcome. This is the section that makes the file worth reading.*

**ADOPTED: confidence-gated recommendation hold. 0.909328 → 0.936614,
fold B +0.0259 (2026-08-31, per arwenalyssa: "can u do build the features
based on prd confidence gated recommend", then "adopt, don't commit" at
τ=0.054).** Built from `docs/PRD_confidence_gated_recommend.md`, which this
session did not author — read the PRD for the full evidence base; this entry
records what building it actually confirmed.

**The mechanism, in one line:** hold the recommendation list until the ranker
has committed (NQC over the ranked pool) or until turn 3, whichever comes
first, instead of recommending on every turn. Two `DialogueConfig` fields, one
gate in `agent.py`, no new feature, no new retrieval, no new dependency.

**Why holding pays.** `local_evaluator.py:252` breaks on first hit, so the
rank we are scored on is fixed at the moment we know least — and turn 1 is
near information-free *by evaluator construction*, not as a property of these
200 sessions: a browsing opener discloses zero spans and the category name
covers hundreds of near-identical listings. 60% of sub-rank-1 sessions locked
in at turn 1. One more turn improves **68%** of the sessions that are losing
and changes **98%** of the ones already won — that asymmetry is the whole fix.

**Measured, unmodified CLI evaluator, both runs to scratch paths:**

| | full 200 | HR@10 | MRR | MTTC | fold A | fold B |
|---|---|---|---|---|---|---|
| default-off (τ=0.0) | 0.909328 | 1.0000 | 0.756095 | 1.875 | 0.902707 | 0.915950 |
| **τ=0.054, fb=3** | **0.936614** | **1.0000** | 0.876048 | 2.310 | +0.028671 | **+0.025900** |

**39 sessions improved, 1 worsened, 0 lost.** Every published PRD number
reproduced to six decimals — full set, both folds, MRR and MTTC — which is
worth recording on its own: the PRD's replay harness is trustworthy for
recommend-timing policies, as it claimed.

**Why τ=0.054 and not τ=0.085, which scores higher (0.941314) on the public
200.** Fold B is *tied* (+0.0259 vs +0.0260), so the extra full-set gain lives
almost entirely in fold A — i.e. in the half that was fitted. τ=0.054 also
costs 0.4 fewer turns of MTTC and keeps HR@10 at 1.0000. The sweep is a broad
plateau (fold B flat at +0.021 to +0.026 across τ ∈ [0.052, 0.090]), the same
non-overfitting signature this file already trusts from `per_field_depth` and
`constraint_commonness_penalty` — so this is a shape choice on a plateau, not
a peak that was hunted for. **The low edge was chosen because the failure
modes are asymmetric: under-holding decays gracefully toward the live
baseline, over-holding falls off a cliff (fold B decays past τ=0.09, and both
`hold-4` and τ=0.15 go net negative).**

**The PRD's §6.4 warning was correct, and it is load-bearing rather than
defensive — verified by deliberately running it both ways.** `agent.py` writes
the trace *before* the recommendation decision, so a withheld turn still emits
its full feature rows, and `tools/offline_eval.py` had no notion of a withheld
turn. Replaying the gated trace with the gate off: **160 agree, 40 disagree** —
exactly the 39 improved + 1 worsened sessions, i.e. it fails on precisely the
sessions the gate exists to help. With the patch: **200/200 agree**, MRR
0.876048 matching live exactly. Anyone who had skipped that patch would have
seen a loud validation failure and could plausibly have "fixed" it by
weakening the gate rather than the tool.

**`nqc` has exactly one definition** (`clarify.py`, module level), delegated to
by both `ClarificationPolicy._confidence` and the new gate, and imported by
`tools/offline_eval.py`. This is deliberate and is the second-order lesson from
the `ReplayScorer` unknown-penalty bug already recorded below: a replay tool
that reimplements a formula silently drifts from it.

**Note the ask gate and the recommend gate share a statistic and nothing
else.** `ask_max_confidence = 0.82` sits above the entire observed NQC range
[0.011, 0.194], which is why this file records NQC as "a no-op that never once
flips a gate decision" — **that gate is not broken, it is unreachable.** The
new threshold is the same statistic calibrated to the range the system
actually produces. Do not tune the two together or reason from one to the
other.

**Two things measured while building that the PRD left open:**

1. **`_empty_response` fires 0 times in 200 sessions** (462 evaluator turns,
   462 turns emitting trace rows — exact). PRD §4.4 flagged it ungated with
   "nobody has measured how often this path fires"; on the public set it is
   dead code, so leaving it ungated costs nothing measurable here. Standard
   caveat: 0/200 does not prove it never fires on the private 800.
2. **`Agent.apply_config` does pick this up** (`agent.py:84` sets
   `self.config`), so the gate is live under `tools.evalkit.Bench` and a
   future tuning sweep will actually move it. Checked explicitly because this
   file records two separate build-time-vs-request-time no-op traps
   (`rerank_depth`, the brand gate) that were only caught by asking.

**Honesty, three ways.** (a) Roughly 20 configurations were scored against
these same 200 sessions producing the PRD, on top of the 16+ already recorded
— **quote fold B (+0.0259), not the full-set +0.0273**, in anything
downstream. The mitigating structural argument is that this is *one* parameter
over a smooth broad plateau, the safest shape of fit available, categorically
unlike the 33-free-parameter reranker work that failed here. (b) **HR@10
1.0000 at τ≤0.054 is a property of this sample, one session deep, not of the
threshold.** `public_0020` is visible on exactly one turn at NQC 0.0546 —
between τ=0.054 (show, keep it) and τ=0.056 (hold, lose it permanently).
Expect to lose ~0.5% of sessions to this at any τ in the plateau on the
private 800; it is already priced into fold B. (c) **Part of this gain is a
scoring artifact and should be described as such.** The reason holding pays is
that the session dies at first hit — real shoppers do not do that; shown a
mediocre list they keep talking and you get another attempt free. First-hit-
break is a measurement convention for time-to-first-success, not a model of
customer patience. This is optimising against the evaluator, which *is* the
task, but it is **not** evidence users would prefer it. The outside literature
splits the same way: EAR (Lei et al., WSDM 2020) supports acting only when the
recommender is confident, while three experiments on withheld information
(n = 1,811/905/801) found conversational withholding *worse* than in a normal
UI — mitigated specifically by showing results alongside the question, which
is what this policy stops doing. §5's preference for the shortest hold is
partly motivated by that.

**Rollback is one config value:** `min_recommend_confidence: 0.0`, verified
byte-identical at 0.909328. That is why the field is a threshold rather than a
boolean — no code path to remove.

**Status: adopted in `config/tuned.json`, NOT committed** (explicit
instruction). All seven PRD acceptance criteria pass: default-off byte-
identical ✓, 56/56 tests ✓, single `nqc` ✓, live 0.936614 ✓, offline_eval
200/200 ✓, fold B +0.0259 ≥ +0.020 ✓, `results.json` untouched ✓. Note the
`.githooks/pre-commit` guard will require `CLAUDE.md` staged alongside
`config/tuned.json` whenever this is committed — both are already modified
together.

**Gender hierarchy fix (live) + brand false-positive gate (adopted, config
now on) -- both correctness fixes with zero measured public-set effect,
adopted for the private 800 (2026-08-31, He Jinhong: "what are some
marketing persuasion tactics... i want to implement that in the agent",
then "it needs to be a generalised solution", then "brand_max_text_commonness:
0.01 in tuned.json").** Full writeup: `docs/PRD_merchandising_facets.md`.

**Origin.** Three merchandising tactics were proposed from `public_0199`
(a boys' briefs target losing rank 2 to a men's competitor): audience
gating, facet-first ordering, within-cell popularity. A 74-pair
instrumented capture (all sub-rank-1 sessions, reproduces 0.909328 exactly)
showed all three lack a precondition -- target and winner tie on every
live constraint span in 74/74 pairs, and on `evaluate_all` in 73/74. There
is no facet to partition on at the turn the score locks in. **All three
original tactics were killed on evidence, including a catalog-derived
audience-inference design that inverted on the originating session itself**
(confidently inferred `men` for `public_0199` at 0.73, because "Underwear
Briefs" is 73% men's catalog-wide). None of this is in code; the PRD is
the record.

**What survived: two structural bugs found by generalising the same
own-goal test** (evaluate a target against constraints drawn from its own
listing, catalog-wide -- not from the 200 public sessions, per He
Jinhong's explicit generalisation requirement) **across all six
constraint dimensions:**

| dimension | own-goal rate before fix |
|---|---|
| gender | 506/50,000 rows (1.01%) |
| brand | 114/200 public targets VIOLATED by their own listing |

**1) Gender hierarchy -- `structured.py`, no config flag, live now.**
`check_gender` treated `kids` as a sibling of `boys`/`girls` rather than
their parent, so a customer saying "toddler"/"baby" (-> `constraints.gender
= "kids"`) scored every boys'/girls' listing VIOLATED at -0.23 -- including
the listing whose own category path produced the word "kids" in the first
place. Compounded by `ConstraintExtractor.update`'s first-match gender
scan: "Baby Girls Bodysuits" hit "baby" before "girls" and discarded the
more specific word. Fixed both: `kids` is now a supertype (SATISFIED
against boys/girls, not VIOLATED; the reverse is UNKNOWN, not VIOLATED --
same asymmetry the existing unisex/adult pair already uses), and a
specific child audience now outranks the generic one during extraction.
Siblings (`boys` vs `girls`) still VIOLATED; every adult rule untouched.
**Measured catalog-wide (all 50k rows, not the 200-session sample) against
the exact opening line each row would generate: own-goal rate 506 -> 180
rows (1.01% -> 0.36%)**, all 313 hierarchy cases cured, the 180 remaining
are genuine catalog mislabels (e.g. a men's item filed under a women's
listing) this fix correctly leaves alone. Public-set exposure was 0/200 --
observing zero is consistent with a ~0.6%-of-rows defect by chance, not
with it being unreachable, which is exactly what a public-200-only view
cannot distinguish. Full evaluator: **0.909328, byte-identical** (this is
the intended, pre-registered result -- the fix's value is entirely on the
private 800 and is unverifiable from this set by construction). 51/51
tests (49 + 2 new).

**2) Brand false positives -- `RetrievalConfig.brand_max_text_commonness`,
now 0.01 in `config/tuned.json` (was 0.0/disabled).** `BrandVocabulary`
matches single ordinary words that happen to be store names somewhere in
a 19,855-brand catalog; `BRAND_BLOCKLIST` is hand-written and missed the
words the simulator actually quotes. **Measured live at the lock-in turn,
all 200 sessions: a brand was extracted in 66 sessions and 62 were wrong
(94% false-positive rate)**, dominated by `wash` (20), `sole` (15),
`hand` (15), `machine` (11) out of "Machine Wash" / "Rubber sole" listing
boilerplate. Fix, in the same spirit as `constraint_commonness_penalty`
(a hardcoded phrase list was explicitly rejected there too): gate
single-word brand matches by measured catalog text-commonness rather than
a curated list. Real brands and boilerplate separate two orders of
magnitude (`sole` 0.206, `wash` 0.317 vs `hanes` 0.0021, `skechers`
0.0077), so 0.01 is not a delicate cut -- it catches 14/15 observed
offenders and keeps all 13 real single-word brands tested. Applied at
*match* time, not build time: `Agent.apply_config` deliberately does not
rebuild the extractor, so a build-time gate would have been a silent
no-op under any future tuning sweep -- the same trap `rerank_depth`
already set once.

**Measured score effect: exactly zero, at every threshold tested
(0.005-0.20 on fold A; 0.01 on the full 200) -- and the reason is
mechanical, not a fitting failure.** `check_brand` returns VIOLATED for
every candidate whose store isn't the extracted brand, so a spurious
brand applies -0.06 **uniformly** across ~99.4% of any pool, and a
uniform offset cannot reorder anything. Only two classes of candidate
differ, both negligible: 8 products in 50,000 (0.016%) are literally
stored as `Sole`/`Wash`/`Hand`/`Machine`/etc. and would wrongly earn
`brand_satisfied` +0.18 if one were ever drawn into a 200-candidate pool
(essentially never); 314 rows (0.63%) have no store and score UNKNOWN
(0.0) rather than -0.06. **An earlier verbal estimate this session of "a
0.24-point swing" was corrected before being written into code or this
file** -- arithmetically right in isolation, practically wrong because it
assumed the literally-named product reaches the pool, which the 0.016%
figure rules out. Full evaluator with the flag on: **0.909328, byte-for-
byte identical to off.** `results.json` untouched; every run scratch-
pathed per Critical rule 1.

**Adopted anyway, on the same basis as the gender fix.** Both changes are
justified by catalog-wide measurement (50k rows), not by any public-set
score movement, which is deliberate: he Jinhong flagged mid-session that
the private 800 is the real target and a fix tailored to the 200 public
sessions is worth negative value. The public set proving 0.000000 is the
pre-registered pass condition for that kind of change, not a null result
to be disappointed by. Reverting the brand gate instead of shipping it
disabled-by-default would have been equally defensible (it is ~40 lines
buying no *measured* score) -- adopted at 0.01 because the false-positive
rate it removes is real, the fix is cheap, and the failure mode it
guards against (a spurious brand constraint from ordinary boilerplate) is
structural to any catalog this large, not specific to these 200 rows.

**Two other tactics from the same session, DO NOT BUILD, evidence in the
PRD:** catalog-derived audience inference (T1b) fires 43/74 times with 2
correct-and-decisive against 3 wrong; facet-first sort ordering (T2) has
a non-constant sort key in only 1/74 pairs. Within-cell popularity (T3)
is not ruled out structurally but the cross-cell comparison it would
correct occurs 1/74 times -- optional, not pursued further.

**THE RANK-2 TEXT READ IS DONE, and it closes the question the roadmap has
carried since 2026-08-30: 0.97 is NOT reachable. The measured hard ceiling is
~0.95 (2026-08-31, He Jinhong: "can help me run this").** All 30 rank-2 pairs
read by hand against what the customer had *actually said at the lock-in
turn* — captured from an instrumented live run (reproduces 0.909328 exactly),
not reconstructed.

**Verdicts, 30 pairs, three buckets:**

| verdict | meaning | n |
|---|---|---|
| **A** | disclosed info already separated them -> ranker got it wrong | **0** |
| **B** | separable only after more disclosure -> timing | **27** |
| **C** | not separable even with the full card -> structural tie | **3** |

**Zero A cases is the headline.** There is not one rank-2 session where the
agent held enough information and still mis-ordered. **The ranker is not
making avoidable mistakes at rank 2** — which retires, on evidence, the
theory floated earlier the same day that popularity was drowning constraint
evidence. Winner-is-more-popular is **15/30**, a coin flip, and in several
pairs the target is far more popular and loses anyway (`public_0006`: 3042
ratings vs 41; `public_0058`: 1032 vs 231). Popularity is not the villain.

**The actual state of the world at lock-in: median 1 span live out of a
median 4-span card. 8 of 30 had ZERO spans — the customer had said nothing
but a category label. In 0 of 30 was the full card disclosed.** Asking the
ranker to pick one product out of a category from a bare category name is a
lottery; rank 2 there is already a good outcome, not a defect.

**The 3 structural ties are real and worth knowing by name.** `public_0058`
is the cleanest: target JTANIB vs winner Rokka&Rolla, both *"100% Polyester
Imported Zipper closure"* women's lightweight hooded packable rain jackets,
both matching 4/4 of the card. No feature, no weighting and no human can
separate them from the listing text — the evaluator's answer key is arbitrary
between them. Also `public_0120` and `public_0175`.

**The ceiling, with MTTC priced in — this is the number that kills 0.97.**
MRR wants more disclosure; MTTC wants fewer turns; the evaluator breaks on
first hit, so they are in *direct* opposition and you cannot have both:

| disclosure reached by | MTTC | ceiling |
|---|---|---|
| turn 1 (free, impossible) | 1.875 | 0.9585 |
| turn 2 | 2.100 | 0.9540 |
| **turn 3 (realistic: 4-span card at <=2 spans/ask)** | **2.410** | **0.9478** |
| turn 4 | 2.760 | 0.9408 |

Even the physically-impossible free-disclosure row is 0.9585. **Realistic
perfect play on this lever is ~0.947-0.954.** 0.97 would need MRR ~0.97 *and*
MTTC ~1.5 simultaneously, which the first-hit-break rule forbids.

**What this means for planning.** The remaining work is worth roughly
**+0.038 (0.9093 -> ~0.947)**, all of it in disclosure timing, and it is
gated on a policy change (confidence-gated withholding / ask-before-recommend)
rather than any new feature or learner. Set expectations at **~0.95, not
0.97**, and treat 0.97 as out of reach on the public set. Artifacts:
`READING_PACK.txt` (the 30 pairs with dialogue), `capture.py` (instrumented
run), `reading_rows.json` (the classification) — all in scratch, regenerable.

**Injection gates swept, nothing adopted — `min_spans` is inert and
`max_survivors` should not be raised (2026-08-31).** Both `RetrievalConfig`
fields existed only as code defaults and had never been tuned. Swept
`injection_min_spans ∈ {1,2,3}` × `injection_max_survivors ∈
{50,100,200,400,800}` on fold A, 15 configs, 727s. **Live default reproduced
exactly (0.929679), and HR@10 stayed 1.000 in all 15** — no variant is unsafe,
none is better. Fold B was deliberately **not** spent: fold A plus mechanism
answered it.

| fold A | 50 | 100 | 200 | 400 | 800 |
|---|---|---|---|---|---|
| min_spans=1 | 0.929829 | 0.929679 | 0.929679 | 0.922779 | 0.922779 |
| min_spans=2 *(live)* | 0.929829 | 0.929679 | **0.929679** | 0.924578 | 0.924578 |
| min_spans=3 | 0.929829 | 0.929679 | 0.929679 | 0.929679 | 0.929679 |

**`min_spans` 1/2/3 are byte-identical at any survivor cap ≤ 200.** The
hypothesis going in was that the gate is redundant: a probe over all 200
sessions found 138 turn-observations with exactly one span, of which **112 are
rejected by `max_survivors` anyway**, 1 has zero survivors, and only **25 are
blocked by `min_spans` alone — with the target among the survivors in 24 of
them (96%)**. All true, and all irrelevant: those 24 targets were *already*
reachable through ordinary BM25, so injecting them again adds a candidate that
was already in the pool. **The reasoning error is worth keeping: "the gate
blocks X" and "unblocking X changes an outcome" are different claims, and the
probe only measured the first.** There is also an interaction — the
`recommend_min_spans: 1` withholding adopted the same day means sessions now
hold 2+ spans by the turn that scores, so the gate stopped binding.

**The real finding is the other axis: raising `max_survivors` to 400 costs
0.005–0.007** (0.929679 → 0.922779 at min_spans=1). Looser conjunctions dilute
the pool. Note it only hurts when `min_spans` is low — at 3 the conjunctions
are precise enough that the cap never binds, which is why the bottom row is
flat. **200 is well-placed; this table is the evidence against loosening it.**
`max_survivors=50` is +0.00015, a hundredth of the noise floor, not a result.

Survivor counts are extraordinarily bimodal — p25 = **1 product**, p50 = 372,
p90 = 9,804, and 56% of span-sets exceed 200 — so there is almost no mass near
any threshold, which is why this axis is flat until it falls off a cliff.

**Evidence-gated recommendation withholding: 0.909328 → 0.928002 (+0.018674),
HR@10 held at 1.000 (2026-08-31).** The disclosure-timing lever this file
recorded as *"not yet run; the prior rejection stands until it is"* — now run.
`config/tuned.json` gains `recommend_min_spans: 1`, `recommend_max_wait: 4`.

**Mechanism.** `local_evaluator` breaks the session loop the first turn the
target appears in the returned top ten, so **that rank is final**. D2
("recommend on ask-turns") is right for HR and MTTC and wrong for MRR: a weak
early list does not merely miss a better rank later, it forecloses it. On the
public set **45 of the 75 sub-rank-1 sessions locked in on turn 1**, carrying
15.5 of a possible 45 reciprocal rank. Replaying those past their hit turn,
every one reached rank 1 by turn 2 or 3 — the better rank was sitting there
unclaimed. The exchange rate favours waiting **7.5×**: one turn of delay costs
`0.2 × 0.1 / 200 = 0.0001`, one rank-2 → rank-1 pays `0.3 × 0.5 / 200 =
0.00075`.

**Why gated on evidence rather than turn number.** Measured at turn 1, split by
what the customer has actually disclosed: with **one** constraint span the
target is already at rank 1 in **57 of 110** sessions (52%); with **none** it is
**13 of 90** (14%). A 3.7× separation, observable without labels and with no
fitted threshold — `1` is simply "the customer has said something concrete",
set on principle, not swept. The blunt `recommend_min_turn = 3` silences all
200 sessions including the 70 already holding the target at rank 1: pure MTTC
cost for no rank gain.

**Measured.** Fold A / fold B (`stratified_halves`, seed 7) / full 200:

| config | fold A | fold B | full 200 | HR@10 | MTTC |
|---|---|---|---|---|---|
| baseline | 0.902707 | 0.915950 | 0.909328 | 1.000 | 1.875 |
| **`spans ≥ 1` (adopted)** | 0.929679 | **0.926325** | **0.928002** | **1.000** | 2.105 |
| `min_turn = 3` (rejected) | 0.932829 | 0.935200 | 0.934014 | **0.995** | 3.150 |
| `spans ≥ 3` (rejected) | 0.933778 | 0.929500 | 0.931639 | **0.995** | 2.955 |

Rank-1 sessions **125 → 147**; 26 improved, 1 regressed (`public_0140`, rank 1
turn 1 → rank 3 turn 2 — already correct and delayed anyway, the residual waste
the gate does not catch). Effect is confined to the scenarios whose openers
disclose nothing, which is the strongest generalisation evidence here: buying
**0.7961 → 0.7961** and intent_override **0.8806 → 0.8806**, both untouched,
against browsing **0.6833 → 0.8485** and boundary **0.6450 → 0.8750**.

**Why the two higher-scoring variants were rejected.** Both cost the perfect
hit rate — `public_0020` (rank 6 at turn 2, gone by turn 3) becomes a miss
under either. HR carries the 0.5 weight, the 0.006 gap to `min_turn = 3` is
smaller than the fold-A/fold-B disagreement this set routinely shows, and on
800 unseen sessions the same ~0.5% rate implies 4–8 outright misses.
`min_turn = 3` also silences every session for two turns — legal under the
contract, but it optimises the metric against the product.

**Caveats, stated because they weaken the result.** (1) `spans ≥ 3` won fold A
at 0.933778 and fell to 0.929500 held out, while `min_turn = 3` rose — the
swept threshold did not survive and the mechanism-justified one did; treat any
fold-A-only win on this set accordingly. (2) Fold B was looked at **four
times** across this work, so it is no longer a pristine holdout. (3)
`recommend_max_wait` is not a real parameter: 3 and 4 gave byte-identical
results at every threshold, so the cap never fires on this set — it exists so a
customer who discloses nothing is not met with silence forever. (4)
`recommend_min_turn` and `recommend_min_confidence` also ship, both inert; the
confidence gate is **unusable as scaled** — NQC is `std(top-10)/|top|`, sits far
below 0.2 in practice, and gating at 0.20 collapsed the agent to HR 0.010. The
same scale bug is likely latent in `ask_max_confidence = 0.82`.

**Synthetic span normalisation: +0.00075 alone, +0.010 on the ceiling
(2026-08-31).** Named for the cause, not the colour: `intent_card` does not
only quote the listing — at `local_evaluator.py:57,61` it regex-matches a
colour word and *inserts a manufactured span* `f"color: {colour}"`. No
product contains that literal string, so `span_all` failed **for the target
itself**: 46 of 791 card spans (5.8%) across **42/200 sessions**, 39 of them
this template, payload present in the blob every time. `state.active_spans()`
now unwraps the template to its payload (`_SYNTHETIC_SPAN_RE`). This reads
the simulator's own construction — it is *not* the stemming/punctuation
tolerance the injection design ruled out of scope. `material` is inserted by
the same code path and escapes the bug only because it is emitted as a bare
word; a second template would be caught by the same regex hook.

**Measured, full public 200:** 0.908578 -> **0.909328** (+0.00075). HR@10
1.000 and MTTC 1.875 both unchanged; MRR 0.753595 -> 0.756095. **Exactly one
session moves** — `public_0192`, rank 2 -> 1 — and nothing regresses.
Folds: A **+0.000000**, B **+0.001500** (the moved session is in B). Zero
fitted parameters, so nothing to overfit; but +0.00075 is two orders of
magnitude under single-run SE (~0.029) and is **not a verified gain on its
own**. 49/49 tests.

**Why it is worth keeping anyway — the value is latent, not standalone.**
The fix is gated behind the same defect as everything else: 45/75
sub-rank-1 sessions lock in at turn 1, before the colour is ever disclosed,
so making a span matchable buys nothing until the disclosure arrives. That
is independent confirmation of the disclosure-timing diagnosis, not a
disappointing result. Where it pays is the ceiling — and note the previously
recorded ceiling was **understated**, because those colour spans were failing
for the target in that calculation too:

| disclosure | ceiling without fix | ceiling with fix |
|---|---|---|
| hard constraints only | 0.9449 | **0.9544** |
| full card | 0.9533 | **0.9638** |

**0.9638 is the first measured number in this project within reach of the
0.97 target.** Treat this fix as a prerequisite/multiplier for the
withholding work, not as a gain in its own right. Ceilings hold all other
features fixed and assume disclosure actually happens, so they bound the
lever rather than forecast it.

**ADOPTED, always-on: conjunctive exact-substring injection. 0.903753 →
0.908578, HR@10 1.000 — every public session hits (2026-08-31, per He
Jinhong: "code out both option A and B and see which one increases the
technical score", then "keep option B in the codebase", then "make it
default on permanently, integrate it into the code as a permanent fix").**

The mechanism: a product whose `search_blob` contains **every live
constraint span verbatim** enters the candidate pool regardless of BM25 rank
— the route into the pool that `per_field_depth` (measured flat past 1000)
cannot provide. Full design, feasibility scans, and per-criterion results
lived in `PRD_conjunctive_injection.md`, which was **not committed and no
longer exists** — this entry is now the only record, so treat it as the
source rather than a summary.

**What's in the code** (`config.py`, `agent.py`): two `RetrievalConfig`
fields — `injection_min_spans: 2`, `injection_max_survivors: 200` — plus a
survivor-scan cache keyed by the `active_spans()` tuple (~0.2s/scan, +28s on
a full 200-session pass). **The injection is unconditional — there is no
enable flag — and only the additive strategy exists.** The two gates are the
mechanism's selectivity, not an off-switch: below `min_spans` a single
boilerplate span matches a fifth of the catalog (10,918 survivors for
"polyester" alone), and an insufficiently-selective conjunction is skipped
outright that turn rather than truncated. The scaffolding history, so nobody
reconstructs it: both strategies were first built behind a mode switch for
the head-to-head below; prepend was then deleted (per He Jinhong: "get rid of
option A entirely. the purpose of the experiment is to delete the useless one
and keep the other") and the switch collapsed to a default-off boolean,
re-verified byte-identical (off = 0.903753, on = 0.908578); the boolean was
then deleted too (per He Jinhong: "make it default on permanently") — each
step re-verified, 49/49 tests at every stage.
Two latent integration bugs were caught **before** any scored run, either of
which would have silently invalidated the experiment: `agent.py`'s
`ctx.fused` construction had no `if i in fused` guard (KeyError on exactly
the candidates injection exists to add), and the `rerank_depth` slice would
have discarded tail-appended survivors entirely — the same
`candidate_depth`/`rerank_depth` coupling trap recorded on 2026-08-31,
recurring. A naive append would have scored **byte-identical to the
incumbent** and read as "mechanism doesn't help" when it never ran.

**The head-to-head, on `mega-fix` `46a6294` (uncommitted working tree),
seed-7 folds, scratch configs throughout:**

| | full 200 | HR@10 | MRR | MTTC | fold A | fold B |
|---|---|---|---|---|---|---|
| incumbent (off) | 0.903753 | 0.995 | 0.748845 | 1.920 | 0.893257 | 0.914250 |
| A: prepend, slice to 200 | 0.899903 | 0.990 | 0.747345 | 1.965 | 0.885357 | 0.914450 |
| **B: keep 200, rerank all** | **0.908578** | **1.000** | **0.753595** | **1.875** | **0.902707** | **0.914450** |

**Option A (prepend survivors, let `rerank_depth` cut the fused tail) is
rejected on evidence, not preference.** It converts `public_0137` (the one
remaining miss) but destroys two sessions doing it: `public_0126` **rank 1 →
miss** and `public_0161` **rank 4 → miss**. The mechanism is displacement:
the gate admits conjunctions up to 200 survivors, and one like
`"buckle closure imported"` (172 survivors) leaves only 28 slots for the
fused pool — mid-pool targets fall out entirely. The median case ("20
injected + 180 original") is harmless; the tail case is not, and the tail
case occurred twice in 200 public sessions.

**Option B (keep the full fused slice, append survivors, rerank 200+N) wins
cleanly.** `public_0137` converts at **rank 1** — `span_all` plus the
title/coverage penalties lift it immediately once it is visible, the same
pattern `public_0092` showed at the depth change. HR@10 **0.995 → 1.000,
every public session hits.** MRR *rose* — no dilution, the failure mode that
killed the category union did not appear, because nothing is displaced and
survivors are few. Only collateral in 200 sessions: `public_0146` rank 4 → 5.
The gate audit (PRD acceptance criterion 4): of 136 distinct span tuples in
the full run, **89 fired** (≤200 survivors), **34 correctly skipped** (e.g.
`"cotton imported"`, 4,348), **13 empty**. It gates; it is not inert.

**Honesty:** full-set +0.004825 is well under single-run SE (~0.029) —
unverified by this set's noise floor. Fold B is +0.0002, a tie, and that is
the *correct* reading rather than a warning: the one public miss sits in
fold A under the seed-7 split, so fold B measures only collateral damage on
100 sessions that needed no help — and measures zero (MRR byte-identical at
0.7695). The structural case carries the result: a targeted defect converted
at rank 1, no held-out damage, no fitted parameters, an audited gate. Also
note the PRD's original feasibility claim was corrected during this work: "≥2
spans collapses survivors to single digits" is only true at 4 spans — at
exactly 2 it runs 300-1,000+, which is why `injection_max_survivors` does
real work rather than guarding a corner case.

**Status: ADOPTED and live — the injection is unconditional in code, so the
committed `config/tuned.json` produces 0.908578 with no config change.** All
adoption gates passed the same day: unmodified CLI evaluator reproduces
**0.908578** exactly (HR@10 1.000, MRR 0.753595, MTTC 1.875);
`tools.offline_eval` against a fresh 76,701-row trace agrees on **200/200**
session `best_rank`s; `target_never_in_pool` **1 → 0** — for the first time,
every public target reaches the candidate pool; 49/49 tests. Scenario
movement vs 0.903604: browsing HR@10 0.9875 → 1.0000 and MTTC 1.85 → 1.7375
(the converted miss was browsing), everything else within noise.
`results.json` untouched throughout; every run went to scratch paths per
Critical rule 1. Note for tuning work: because the mechanism has no enable
flag, the incumbent-without-injection is no longer reachable by config — an
ablation of it requires reverting the `agent.py` block, which is deliberate
per the "permanent fix" instruction quoted above.

**Fresh `why_lost` against the adopted build's trace (same day), because the
old diagnostics predate span_all, title/coverage, and the injection:** at
rank 2 (31 sessions) and ranks 3-10 (44 sessions), **every constraint and
span column carries exactly 0.0% of the score gap.** The gap is all
text-evidence volume: rank 2 splits bm25_features 28% / bm25_title 22% /
popularity 19% / dense 19%, ranks 3-10 split bm25_features 37% / fused 34% /
bm25_title 18%.

**CORRECTION, same day, before anything was built on it — this entry first
read the 0.0% as "the winner also matches every span, so the conjunction
cannot separate them," and that was WRONG.** The inference skipped a step:
a 0.0% contribution share is `delta x weight`, and `w_span_all = 0.4` is
non-zero, so the zero is in the *delta*. Reading the actual listing text
(the roadmap's own long-deferred rank-2 item) shows why, and says the
opposite of the original claim:

- **`span_all` ties in 100% of pairs at the hit turn** — 0/31 and 0/44 ever
  differ. At rank 2 it is 22/31 both-1 and 9/31 both-0.
- **Because the sessions lock in at turn 1.** `first_hit_turn` is 1 for
  45 of the 75 sub-rank-1 sessions (20/31 at rank 2, 25/44 at ranks 3-10),
  median 1. By turn 1 the customer has said almost nothing, so the
  conjunction is trivially satisfied by both.
- **The discriminating information exists — it just arrives too late.**
  Against the *full* `intent_card` (hard + soft), the target matches every
  span and the winner does not in **52 of 75** sessions (21/31 at rank 2,
  31/44 at ranks 3-10). The evaluator breaks on first hit, so that evidence
  never gets to be used.

**Ceiling, measured not guessed** (rescore each hit turn's real pool with
`span_all`/`span_coverage` recomputed against the card, all other features
held fixed — optimistic by construction, so read as an upper bound):

| disclosure | MRR | rank-1 sessions | HR@10 | score (MTTC fixed) |
|---|---|---|---|---|
| live today | 0.753595 | 125 | 1.000 | 0.908578 |
| hard constraints only | 0.874589 | 159 | 1.000 | 0.944877 |
| hard + 1 soft | 0.894589 | 165 | 1.000 | 0.950877 |
| full card | 0.902506 | 168 | 1.000 | 0.953252 |

**No session is lost or worsened in any variant** — 20 of the 2->1 moves are
the entire rank-2 bucket's separable half. **Implication, replacing the wrong
one above: the remaining headroom is a disclosure-timing problem, not a
feature-invention problem.** The right experiment is a re-run of the
withholding/ask-more policy, which this file already records as tested and
rejected (blanket, budget 2-8, net -0.0233) — but that test ran at HR@10
0.960 with `target_never_in_pool` 6, and its dominant cost was HR@10 -0.0300
from "more disclosed information makes retrieval worse." **The conjunctive
injection adopted today removes exactly that mechanism** (a target matching
every disclosed span now enters the pool regardless of BM25 rank;
`target_never_in_pool` is 0). The two compose, and the arithmetic now favours
waiting: one extra turn costs 0.0001 of score per session via MTTC, while
rank 2 -> 1 pays 0.00075 — **7.5x**. Not yet run; the prior rejection stands
until it is.

**Second finding from the same text-read, independent of the first and much
cheaper: 39 constraint spans can never match anything, because the evaluator
manufactures them.** `intent_card` does not only quote the product — at
`local_evaluator.py:57,61` it runs `COLOR_RE` over the corpus and *inserts a
synthesised span* `f"color: {colour}"`. That literal string exists in no
product's text, so `span_all` fails **for the target itself**. Measured over
all 200 public targets: **46 of 791 card spans (5.8%) are not contained in
the target's own `search_blob`, affecting 42/200 sessions — 39 of the 46 are
the `color: X` template, and all 39 have their payload (`X`) present in the
blob.** The other 7 are genuine (an `item model number`, a couple of
unicode-heavy marketing blocks).

This is why 8 of the 31 rank-2 targets score below 4/4 on their own card and
so cannot be separated from their winner by the conjunction — not because the
evidence is absent, but because the matcher is looking for a string the
simulator invented. **The fix is to normalise the known synthetic template to
its payload before matching (`^color:\s*(.+)$` -> ``), which is reading the
evaluator's own construction, not fuzzy matching** — the thing
the injection design ruled out of scope is stemming/punctuation tolerance,
a different and unmeasured change. Not yet implemented. Expected to
compound with the disclosure-timing work above rather than duplicate it: it
raises how often `span_all` can fire at all, which is the feature that work
depends on.

**ADOPTED: `per_field_depth` 800 → 1000, reopening a decision this file said
not to re-litigate. 0.900004 → 0.903604 (2026-08-31).**

**`public_0092`'s "structurally unwinnable" verdict was wrong — it was a
retrieval failure, not a ranking one.** That verdict (see the entry lower in
this file) traced the target with its candidacy *force-injected*, and never
checked whether the target reaches the ranker unassisted. It doesn't. Turn-by-
turn tracing of the live 200-candidate pool shows the target's fused rank
getting **worse** as constraints are disclosed (267 → 634 → 1213 across turns
1-3) and its per-field BM25 rank on `features` — the one field holding all
four disclosed constraints — sitting at **831, thirty-one places past the
800-deep cutoff**, at every turn. It is never visible to `span_all` or to
anything else in the feature vector, because it is never in the candidate set.

**Why rank 831 for a listing whose features contain every disclosed string.**
BM25 length normalisation. The target's `features` field is 171 tokens — five
marketing blocks (❤ SIZE / DESIGN / LAUNDRY / GIFT / SERVE) — 2.6× the catalog
average and longer than 94% of the catalog on this field. At `b_features =
0.75` its score is cut to **46%** of what identical term matches would earn on
an average-length listing. It is punished for verbosity on the one field where
it holds the customer's entire disclosed intent.

**Why `per_field_depth` was reconciled to 800 just one day before this, and
why that call was right at the time.** The 2026-08-30 reconciliation entry
(below) measured 800 vs. 1000 as **+0.000006** — indistinguishable — and that
was true *for the config being tested*. `span_all` (same day, later commit)
changed the payoff of depth without anyone re-measuring the interaction: it
gave the ranker a feature that separates "matches all N disclosed constraints"
from "matches some," which is exactly what a target needs once depth makes it
visible. Before `span_all`, raising depth to 1000 put this same target in the
pool at **rank 39** and left it there — visible, but with nothing in the
vector able to lift it. `span_all` is that missing lever. **The two changes
interact; neither one's isolated measurement predicts the pair.**

**Measured properly this time, not by inspecting one session.** Built a
pool-recall harness: for all 200 sessions, one uncapped BM25 pass per turn,
then the real `top_n`/`convex_combine` fusion re-run at every
`per_field_depth` × `candidate_depth` combination by truncating exactly the
way `BM25Field.search`'s `limit` does (sorted `(-score, doc_id)`, then slice —
confirmed against the source). Cross-checked against the live agent's actual
candidate set at the live config: **0 mismatches across all 200 sessions**, so
the simulation is trustworthy.

```
TARGET RECALL INTO THE CANDIDATE POOL  (rows: per_field_depth, cols: candidate_depth)
    depth |    C=50   C=100   C=200   C=400   C=800
      200 |   90.5%   93.5%   97.0%   98.5%   99.5%
      800 |   92.0%   96.5%   99.0%  100.0%  100.0%   <- live (pre-change)
     1000 |   91.5%   96.0%   99.5%  100.0%  100.0%
     2000 |   91.0%   96.5%   98.5%  100.0%  100.0%
    50000 |   90.5%   95.5%   99.0%   99.5%  100.0%
```

**Recall saturates completely between 600 and 800 — 800 → 50,000 is a 62×
increase for 0.0 percentage points.** `per_field_depth` was never an
open-ended dial; 800 sat almost exactly at the knee. The only real headroom
left on the row axis was 800 → ~1000, which the grid shows recovering the last
two sessions (`public_0092`, `public_0137`) at `candidate_depth ≈ 300`. Do not
read this as "raise depth further next time" — the curve is flat past 1000 in
every column tested, up to 50,000.

**`candidate_depth` widening was the alternative fix, and it was tested and
rejected.** Holding `per_field_depth=800`, `candidate_depth` 200→300 (with
`rerank_depth` moved to 300 in lockstep — see the trap below) reaches 100%
pool recall and converts both remaining misses, but on `stratified_halves`:

```
                         foldA       foldB       full     HR@10    MRR      MTTC
incumbent (C=200)      0.887970   0.902400   0.895185   0.9900  0.730950  1.955
C=300 R=300            0.892607   0.893208   0.892908   0.9950  0.709692  1.875
per_field_depth=1000   0.884936   0.910950   0.897943   0.9950  0.729476  1.920
1000 + C=300 R=300     0.886761   0.905725   0.896243   1.0000  0.710476  1.845
```

(Measured pre-merge, on `e1d3054`, before Joey's title/coverage weights — the
pattern reproduced on `d2f12ac`, see below.) `candidate_depth=300` alone
**loses fold B** (−0.0092): the wider pool dilutes MRR on sessions already
winning at rank 1 by the same mechanism the category-union experiment
recorded — more candidates surfaced by raw score, not by relevance, crowd out
correct answers elsewhere even as they fix the targeted misses. `depth=1000`
alone **wins fold B** (+0.0086) with HR@10 and MRR both flat-to-positive.
Combining both **underperforms depth alone** on fold B (+0.0033 vs. +0.0086) —
the dilution cost is still there, just partly masked by HR@10 hitting 1.0000.
**`per_field_depth` only adds documents that genuinely match; `candidate_depth`
adds whatever ranks next by raw score. That is the entire reason one
generalises and the other doesn't**, and it is the same distinction this file
already draws for `per_field_depth` vs. the category-union candidate
injection.

**A coupling trap, caught before it cost anything.** `rerank_depth` is applied
*after* `candidate_depth` (`agent.py:174`, `candidate_ids[:rerank_depth]`) and
both default to 200 — so raising `candidate_depth` alone without also raising
`rerank_depth` is a silent no-op, verified directly: `C=300, R=200` scored
byte-identical to the incumbent on fold B (0.902400 vs. 0.902400). Anyone
testing `candidate_depth` in isolation without knowing this would conclude the
lever does nothing. It isn't dead code — the slice executes every turn — but
at the current values it never binds unless both move together.

**Re-verified on `d2f12ac`, after this file's own trunk-remeasurement
correction (above), because the tree changed under the original measurement.**
Ranking weights added on trunk (`w_bm25_title_buying`, `w_title_low_coverage`,
`w_popularity_low_coverage` — see the title/coverage entry below) specifically
penalise low-coverage candidates riding popularity or title match, which is
the exact dilution mechanism that made `candidate_depth` widening fail. Worth
testing whether that changes the verdict: it doesn't reverse it, but it
narrows the gap —

```
                         foldA       foldB       full     HR@10    MRR      MTTC
incumbent (live, d800) 0.893608   0.906400   0.900004   0.9900  0.747014  1.955
depth1000 C200 R200    0.890757   0.916450   0.903604   0.9950  0.748345  1.920
depth1000 C300 R300    0.892582   0.911225   0.901904   1.0000  0.729345  1.845
```

`per_field_depth=1000` alone: fold A **−0.0029**, fold B **+0.0101**, full
**+0.0036** — held-out beats fitted, the signature this file already trusts
from `span_all`. `+C300 R300`: still HR@10 1.0000, but fold B **drops** to
+0.0048 and MRR falls 0.748→0.729 — Joey's penalties reduced the dilution
cost, not eliminated it. **`per_field_depth` alone is the adopted change;
`candidate_depth` stays at 200.**

**Adopted and validated end-to-end.** `config/tuned.json`:
`per_field_depth: 800 → 1000`. Full 200-session evaluator: **0.900004 →
0.903604**. HR@10 0.990→0.995 (2 misses → 1: only `public_0137` remains —
diagnosis below is unaffected by this change and still open). MRR
0.747014→0.748345, MTTC 1.955→1.920. `public_0092` now hits at **turn 3, rank
1** on this config (Joey's low-coverage penalties lift it past rank 2, which
is what depth alone produced on `e1d3054`). 38/38 tests pass.
`tools.offline_eval` against a fresh trace: **200 agree, 0 disagree** on
`best_rank`, `target_never_in_pool` 1 (down from — see caveat below on what
that "1" was pre-fix). `results.json` untouched throughout; every run went to
a scratch path per Critical rule 1.

**Still unverified by this set's own noise floor** (full-set +0.0036 is well
under single-run SE ≈0.029), but carries the fold-B-beats-fold-A signature
this file treats as trustworthy rather than the reverse. Report it as such,
not as proven.

**What this leaves open.** `public_0137` is untouched by this change — its
fused rank gets **worse** with more depth (263 → 279 → 344 → 419 → 464 as
`per_field_depth` rises from 800 to 50,000), the opposite mechanism from
`public_0092`, and needs its own turn-by-turn trace rather than assuming the
same fix applies. The rank-2 bucket (31 sessions in the new distribution) is
unchanged by this fix, by construction — depth only affects sessions that
weren't retrieved at all, not sessions already retrieved and merely
mis-ordered. `candidate_depth` remains closed as a lever pending a feature
that fixes what's actually diluting it, not a wider net.

**Trunk measures 0.900004, and the two branch numbers that disagreed with it
were both right (2026-08-31).** Re-ran the unmodified evaluator against
`d2f12ac` because no document held a number for the *merged* trunk — the same
failure mode the 2026-08-30 state audit found, recurring inside a day.

**Result: 0.900004** — HR@10 0.990, MRR 0.747014, MTTC 1.955, Efficiency 0.9045.
Reproduced twice on a clean tree, byte-identical. Misses are down to two,
`public_0092` and `public_0137`; `public_0100` and `public_0161` are both
recovered. Every scenario is at or above its previous value.

**Why the docs disagreed, and why neither was wrong.** `span_all` and the
title/coverage interaction were developed **in parallel off different parents**,
so each was measured against a baseline that lacked the other:

```
36652f1  commonness penalty adopted                       0.876342
 ├─ e1d3054  + span_all           (PR #8)                 0.888187   (+0.011845)
 └─ ab4e55c  + title/coverage     (PR #9, off 36652f1)    0.892242   (+0.015900)
d2f12ac  merge of both                                    0.900004   (+0.023662)
```

`ab4e55c`'s parent is `36652f1`, **not** `e1d3054` — confirmed with
`git merge-base --is-ancestor`. So 0.888187 and 0.892242 are each an honest
measurement of one change alone, and neither describes the trunk. Both are now
listed under "Stale artefacts" for that reason, not because either was an error.

**The two changes are sub-additive: +0.0237 combined against +0.0277 summed, so
about 85% of the individual gains survive composition.** That is the expected
direction — both reduce text-evidence confidence that disclosed constraints do
not support, so they partly address the same failure — but the overlap is small
enough to be worth recording. **Do not assume parallel gains add.** Two branches
that each look like +0.015 can land anywhere between +0.015 and +0.030 together.
Re-measure the merge, always.

**Operational lesson, recorded because it cost real confusion this session:** a
branch checkout silently changes `FEATURE_NAMES`, `config/tuned.json` and the
test count underneath any analysis in flight, with no error and no warning.
Mid-session, `maximising-101` was 38 features and 36 tests while `main` was 40
and 38, and the same command produced different answers twenty minutes apart.
**Record the commit next to every number you report**, and run
`git log HEAD..main` before concluding that anything regressed.

Verified: 38/38 tests pass. `results.json` was not overwritten — both runs went
to scratch paths per Critical rule 1.

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

**The defect.** The customer quotes the target's own copy verbatim, and the
agent dissolved those quotes into tokens. `"95% Polyester, 5% Spandex"` became
`{95, polyester, 5, spandex}`, which BM25 matches against 41% of a category —
where the intact phrase matches **5.3%**. The only survivor of the span
structure was `phrase_*` (ordered-bigram *overlap*, weight 0.13), and overlap
**cannot distinguish "3 of 4 spans matched" from "all 4"** because both can
produce identical bigram sets. The conjunction was not representable at all.

**Why the conjunction is the whole game — `public_0092`, disclosed in order:**

```
after           constraint                     survivors   target still in?
turn 2          imported                              86         True
turn 2          button closure                        14         True
turn 3          polyester                              9         True
turn 3          95% Polyester, 5% Spandex              2         True
```

284 → 2. **The agent held all four strings by turn 3 and ranked the target
39th.** Each constraint alone is boilerplate (13-41% of the category); together
they are nearly unique.

**The fix.** `state.active_spans()` keeps live constraint text whole;
`Product.search_blob` is a lowercased match surface built once at load;
`features.py` gains `span_coverage` (fraction matched) and `span_all` (1.0 iff
*every* span matches). `FEATURE_NAMES` 36 → 38.

**Measured, fold A only for selection, fold B once:**

```
fold A  0.872192 -> 0.883120   (+0.010928)
fold B  0.880493 -> 0.893253   (+0.012760)     <- gains MORE held-out than fitted
full    0.876342 -> 0.888187   (+0.011845)
```

| scenario | MRR before | after | Δ |
|---|---|---|---|
| intent_override | 0.7972 | 0.8722 | **+0.075** |
| browsing | 0.5907 | 0.6418 | **+0.051** |
| buying | 0.7245 | 0.7516 | +0.027 |
| boundary (n=10) | 0.6700 | 0.5950 | **−0.075** |

The spread follows the mechanism: browsing and override disclose constraints
across several turns, so there are spans to conjoin; buying discloses one up
front and a lone span has no conjunction to exploit.

**Why this is believed despite +0.0128 being under single-run SE (~0.029).**
Four independent signatures, none of which the twelve failed attempts had:
(1) monotone response saturating — 0.05→0.4 improves, 0.8 identical, not a
jagged peak; (2) fold B > fold A; (3) the metric signature was **predicted in
writing before the run** (HR@10 flat, MRR up) and came out exact — HR@10 moved
+0.0000 in *every* scenario; (4) `span_coverage` measures **zero** at every
weight, so the entire effect is the conjunctive bit, which is precisely the
information that was previously unrepresentable. **Still labelled unverified,
not proven** — the margin is under SE.

**Honest negatives.** Boundary regressed −0.075 MRR, which on n=10 is one
session falling from about rank 1 to rank 4 — noise, but recorded.
`w_span_all = 0.4` was selected on fold A, so this carries one fitted
parameter. `w_span_coverage` stays 0.0.

**Why twelve other attempts failed and this did not.** Every one of them
redistributed weight among features that all measured the same thing — text
overlap under different names. This added a bit of information the vector did
not contain: *does this candidate satisfy all of the customer's constraints at
once*. **When a change fails on holdout, ask whether it was moving weight or
adding information.**

**`public_0092` diagnosed: structurally unwinnable, not a retrieval or ranking
failure (2026-08-30, He Jinhong).** Closes the "3 misses remain — not diagnosed
this pass" gap below.

Traced turn by turn with the target forced into the candidate pool:

```
turn   pool   target in pool   target rank
   1    280            True            206
   2    320            True             91
   3    354            True             39
 4-10   354            True             39      <- frozen
```

Rank improves while constraints are disclosed, then **freezes for seven turns**.
The cause is that the customer runs out of things to say. `intent_card()` gives
this target exactly four constraints, and `customer_reply` discloses at most two
per ask:

```
[material] polyester                     appears in 41.2% of the category
[material] 95% Polyester, 5% Spandex     (spandex: 27.5%)
[feature ] Imported                      appears in 30.3% of the category
[feature ] Button closure                appears in 13.0% of the category
```

Two productive questions exhaust the customer; every other attribute returns
"I don't have an additional preference for X". And what *is* disclosed narrows
284 pajama sets to roughly a third of them. **The information needed to identify
this product does not exist anywhere in the session** — the target carries no
colour, no brand signal, no price, and describes itself in the same words as a
hundred others.

**Why this matters beyond one session:** both `per_field_depth=800` and the
category union (below) correctly leave `public_0092` a miss. Neither is broken.
It belongs in the genuinely-undecidable bucket this file already sizes at ~6
targets, and it sets a floor on miss-conversion work that is not a tuning
problem. **Do not spend further effort on it.**

**Category-union candidate injection: mechanism total, score null (2026-08-30,
He Jinhong).** Measured against the **0.862111** baseline, *before* the
`per_field_depth` merge — do not compare these deltas to 0.876342.

Hypothesis: the customer names a taxonomy node verbatim in turn 1
(`coarse_category`, 1,115 distinct values, median pool 8), and the agent was
treating it as a bag of words. Built `catalog.by_coarse_category` and unioned
the node's members into the candidate pool.

**The mechanism worked completely** — turn-1 pool membership **80% → 100%**,
every scenario, 4 real misses converted, MTTC −0.25. **The score did not
follow:**

| variant | fold A | fold B vs incumbent |
|---|---|---|
| union, incumbent weights | +0.0167 | **−0.000271** |
| union, retuned on fold A (8 params) | +0.0051 | **−0.013863** |

Per-session sign test: 5 better, 11 worse, 184 unchanged. The aggregate is
positive only because converting 4 misses is worth a lot in HR@10 while the
dilution cost is spread thinly across more sessions.

**Retuning made it worse, and legibly so.** The tuner cut
`w_log_rating_number` 0.88 → 0.4, because the union injects candidates *ordered
by popularity* and weighting popularity again double-counts it. Correct on fold
A; on fold B that weight was doing real work and MRR collapsed 0.7049 → 0.6420.
The same double-counting trap as the `fused` signal, arriving by a new route —
except here the "fix" was the overfit.

**Not adopted.** `category_union_size` defaults to 0. Superseded by
`per_field_depth: 220 → 800`, which fixes the same defect (target absent from
the pool) with the same signature (HR@10 up, MRR down, MTTC down) for +0.0142
via a one-line config change instead of ~115 lines. **Untested:** the union on
top of `per_field_depth=800`. Expected to fail worse — its failure mode is MRR
dilution and 800 already widens the pool — but that is a prediction, not a
measurement.

**Withholding recommendations while asking: mechanism real, cost dominates
(2026-08-30, He Jinhong).** Also measured against **0.862111**.

`best_rank` is first-hit-in-top-10 and the evaluator **breaks on first hit**, so
recommending early locks in whatever rank you have. `recommend_on_ask_turns`
was `True` and had never been tuned (`SEARCH_SPACE` holds only floats).

| config | HR@10 | MRR | MTTC | Score |
|---|---|---|---|---|
| baseline | 0.960 | 0.6897 | 2.24 | **0.862111** |
| withhold, budget=2 | 0.810 | 0.6588 | 3.92 | 0.744241 |
| withhold, budget=3 | 0.900 | 0.8217 | 3.885 | 0.838800 |
| withhold, budget=4 | 0.910 | 0.8355 | 4.63 | 0.833039 |
| withhold, budget=8 | 0.925 | 0.8525 | 8.225 | 0.773764 |

**MRR rises by up to +0.163** — ranks really are being locked in badly. But
HR@10 falls and MTTC blows out, and the best variant is −0.0233. Decomposed at
budget=3: MRR +0.0396, HR@10 −0.0300, MTTC −0.0329.

**The HR@10 loss is the surprising part** and worth remembering: withholding
loses 12 sessions *even with seven turns left to recommend*. More disclosed
information makes retrieval worse on some sessions — consistent with 53% of
what a customer can state being boilerplate (`Package Dimensions`,
`Date First Available`). **Asking more is not free, and not only in turns.**

**Not adopted.** Early recommendation is a hedge that is paying.

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
diagnosed in that pass. **`public_0092` has since been diagnosed** and is
structurally unwinnable; see the top entry. `public_0137` / `public_0161` /
`public_0100` remain undiagnosed.

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

**Superseded twice — read the "Current state" table and the rank-2 text-read
entry in "What was found" instead of anything below.** First by the
`per_field_depth` fix, then decisively on 2026-08-31 by the conjunctive
injection: **HR@10 is 1.000 and `target_never_in_pool` is 0**, so "6 targets
believed unreachable" and the 39/83/8-hit distribution this section is built
on describe a system that no longer exists. The live distribution is
125/31/35/9 across rank 1 / rank 2 / ranks 3-5 / ranks 6-10, 0 misses.

**The qualitative lesson here is also now wrong, not just the counts.** This
section said rank-2 was "gated on new features, not weights." The text-read
finding shows it is gated on *neither*: the separating feature already exists
(`span_all`, w=0.4) and already works — it simply has nothing to act on,
because 45 of the 75 sub-rank-1 sessions lock in at turn 1, before the
customer has disclosed the spans that separate target from winner. With the
full card the target beats its winner on the conjunction in 52 of 75. The
measured ceiling from disclosure timing alone is **0.9449 (hard constraints
only) to 0.9533 (full card)** with MTTC held fixed, no session lost. The
seven failed reranker attempts were all trying to solve the wrong problem.

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
**The impact column below is stale wherever it predates the 2026-08-31 gate;
the "Current state" table is authoritative.**

| priority | item | impact | notes |
|----------|------|--------|-------|
| ~~high~~ | ~~Selective withholding / ask-one-more before recommending~~ | **+0.027 banked** | **DONE 2026-08-31 — shipped as the confidence-gated hold, 0.909328 → 0.936614, fold B +0.0259.** It landed confidence-gated and fold-B-measured exactly as this row required. Predicted "≤ +0.036 to +0.045"; delivered +0.027, and the shortfall is the deliberate choice of the plateau's low edge over its high end (τ=0.085 reaches 0.9413) because fold B is tied there. See "What was found" |
| **high** | **Push the hold past the plateau's low edge — only with new evidence** | ≤ +0.005 | the remaining gap between τ=0.054 (0.9366) and the sweep's best public-set point (τ=0.085, 0.9413) is real but sits entirely in fold A. Not worth taking on the public set's say-so; would need either the private-set feedback or a per-intent variant. `buying` is the only scenario carrying HR risk, so an intent-conditional threshold is the obvious shape |
| ~~high~~ | ~~New feature for the rank-2 cases~~ | — | **superseded 2026-08-31.** The separating feature already exists and already has weight; it has nothing to act on at turn 1. Read the text-read entry before building any new column |
| high | Browsing recall | ~+0.02 | was 5 of 8 misses and the worst MRR. **HR@10 is now 1.000 across every scenario**, so the recall half is closed; browsing MRR (0.6833) is still the weakest and is squarely in the disclosure-timing bucket above |
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
