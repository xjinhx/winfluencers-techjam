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

**Repo:** github.com/xjinhx/winfluencers-techjam (`origin`)
**Upstream:** github.com/TechJam2026/techjam-conversational-search (`upstream`, read-only)
**Main branch:** `main` — stable, evaluated, live submission candidate
**Active branch:** `maximise-mrr` (started 2026-08-30) — MRR headroom, ranks 2-5 recovery
**Other branches:** `jinhong`, `joey`, `intentions`, `arwen`, `feat/shopping-copilot` — per-person work, merged into `main` via PR
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

| item | score | status | date |
|---|---|---|---|
| baseline `config/tuned.json` | **0.784838** | committed, live | 2026-08-30 |
| intent-conditional `w_fused` | **0.8091** | verified, **not adopted** | 2026-08-03 |
| **max observed** | **0.8091** | (fold B: +0.0121 expected on private set) | — |

**Open decision:** intent-conditional weighting is coded and tested, but
`w_fused_buying` / `w_fused_uncertain` are not set in the production config.
See "What was found" for the tradeoff and "Measurement discipline" for why the
noise floor is what's holding it back.

**Uncommitted:** `README.md` modified (last sync 2026-08-30).

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
8. **Do not commit a change worth less than the noise floor.** ~+0.05 MRR on
   this 200-session set. See "Measurement discipline" — this is the single
   easiest way to ship a regression that looks like a win.

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
- **Unknown-penalty is applied outside the model** (`ranking.py:120-129`).
  Three of six non-zero under the tuned config: gender −0.01, category −0.005,
  price −0.005. Scoring with `LinearModel` alone will not reproduce ordering.
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
  Single-run SE ~0.029. A change worth less than ~+0.05 MRR is indistinguishable
  from noise on this set.
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

**Status:** coded, tested (0.8091 verified), **not adopted in production
config** — fold B only justifies +0.0121, below the noise floor in rule 8.

---

## Remaining headroom

**Ceiling: 0.970** (hard limit). Current best: 0.8091 (verified).

| component | ceiling | available | priority |
|-----------|---------|-----------|----------|
| **HR@10** | 0.970 | +0.020 | low (hard-capped; 6 targets unreachable) |
| **MRR** | 1.0 | +0.101 | **high** (93 of 186 hits at ranks 2-10; ranks 2-5 alone = +0.0623) |
| **Efficiency** | 0.930 | +0.020 | medium (misses cost 11 turns; capped by MTTC floor) |

**What's left on the board:** of 18 baseline misses, 12 had the target in the
pool but outside top-10 (best positions [1,11,11,15,16,16,17,21,28,39,39,87]),
9 of them within position 30. Only 6 are true retrieval failures.

**Next step:** run `why_lost` on the intent-conditional trace to see what costs
rank after the double-counting fix. (Trace: `features_intent.jsonl` if the
scratchpad survived; else regenerate in ~3 minutes.) Expect a smaller finding —
structural errors like the double-count are rare.

## Gradient boosting future work (LambdaRank / LightGBM)

**Assessed and deferred, not rejected.** In scope per the rules ("local scoring
logic" is explicitly in; "training base foundational LLMs" is out).

**Two blockers before building a GBDT:**
1. **The feature-weighting defect still exists.** A GBDT trained on current
   vectors would inherit the double-counted fused signal. Retrain only against
   corrected weights. Freeze the retriever first — negatives are mined from its
   own output, so a retriever change invalidates training data retroactively.
2. **The unknown-penalty is post-hoc.** It is a hand-tuned additive constant on
   the `LinearModel` output scale; a GBDT's raw output is not on that scale.
   Fold the penalty into the feature vector first, or the model will look
   underperforming when it is actually correct.

Also note Critical rule 5 — LightGBM breaks the stdlib-only guarantee.

---

## Roadmap (ordered by expected impact)

| priority | item | impact | notes |
|----------|------|--------|-------|
| **high** | Rank 2-5 recovery | +0.0623 MRR | 63 sessions stuck in 2-5; needs feature analysis |
| high | Full GBDT model | +0.05-0.15 (est.) | needs corrected features + frozen retriever first |
| medium | Retrieval refinement | +0.01-0.05 (est.) | similarity, query expansion, better intent use |
| medium | Context refinement for buying | +0.01 (est.) | buying intent logic could be tighter |
| low | Intent override handling | +0.005 (est.) | diminishing returns; only 4 sessions affected |

## Out of scope for current work

Noted so they don't creep into the roadmap:

- **Fine-tuning or training a foundational LLM.** Explicitly out per the
  submission rules. Local scoring logic is in; model training is not.
- **Adding runtime dependencies.** See Critical rule 5. Separate decision.
- **Modifying the evaluator or the catalog** to make a number move. Rules 3 and 4.
- **Chasing HR@10 past ~0.97.** Six targets are genuinely unreachable; the
  remaining headroom there is +0.020 against +0.101 in MRR.
- **Refactoring for its own sake.** The competition ends before the payoff does.

---

## Working with He Jinhong

- Direct and decision-focused. Prefers options plus a recommendation over
  open-ended questions.
- Pushes back when something is wrong — don't be defensive, just fix it.
- **Commits are authored by He Jinhong alone.** No `Co-Authored-By: Claude`
  trailer, ever.
- Work lands on a personal branch and merges into `main` by PR.
- Wants the reasoning recorded, not just the result — a change without its
  measurement and its tradeoff is not finished.

---

## Keeping this file current

**The mechanism is a convention, not a hook:** when a session produces
something material, the CLAUDE.md edit goes into the *same commit* as the change
itself. That is how this file stays true instead of drifting into fiction.

**Material means any of:**
- the score moved (either direction — regressions are the most valuable entries)
- `config/tuned.json` changed
- a finding that would cost time to rediscover
- a decision made and its reason (especially a decision *not* to adopt something)
- a new operational fact about the evaluator, the tooling, or the data

**Not material:** refactors that don't change behaviour, experiments abandoned
before measurement, anything already recorded in the git history alone.

**What an entry looks like:** date, what changed, what it measured, and *why*
the call went the way it did. The intent-conditional entry above is the
template — the useful part is "fold B only justifies +0.0121, below the noise
floor", not "0.8091".

**Where things go:** score/config → "Current state" table. Findings and
decisions → "What was found" (newest first). New risks → "Critical rules" or
"Critical evaluator facts". Everything else → "Roadmap" or "Out of scope".

Check `git log -- CLAUDE.md` to see when it was last synced.
