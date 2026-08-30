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

**Live score: `TechnicalScore = 0.862111`** — measured 2026-08-30 by running the
committed `config/tuned.json` through the unmodified evaluator on all 200 public
sessions.

| metric | value |
|---|---|
| HR@10 | 0.960 (8 misses) |
| MRR | 0.689704 |
| MTTC | 2.24 |
| Efficiency | 0.876 |
| **TechnicalScore** | **0.862111** |

| scenario | n | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| buying | 80 | 0.9750 | 0.7176 | 1.51 |
| browsing | 80 | 0.9375 | 0.5966 | 2.29 |
| intent_override | 30 | 0.9667 | 0.8317 | 3.93 |
| boundary | 10 | 1.0000 | 0.7850 | 2.60 |

**Intent-conditional weighting is adopted and live.** `config/tuned.json` sets
`w_fused_buying: 0.0` and `w_fused_uncertain: 0.0` against `w_fused: 1.0` — this
is no longer an open decision, and any doc saying otherwise is stale.

**Stale artefacts — do not quote these as the current score:**
- `results.json` = **0.847625** (run 2026-08-30 01:03, eight hours before
  `config/tuned.json` was last edited) and `results_tuned.json` = **0.784838**.
  Both predate the current config. `README.md` still headlines 0.8476.
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

**Current 0.862111. Practical ceiling ~0.970** (6 targets believed unreachable,
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

**Rank 2 is where the work is.** 39 sessions retrieve the target and place it
one position off. That is 20% of the whole set losing half its reciprocal rank
to a single swap — a bigger, more concentrated target than anything else on the
board, and it is a pure reranking problem, not a retrieval one.

**The 8 misses** split browsing 5 / buying 2 / intent_override 1. Browsing is
the weakest track on every metric (HR@10 0.9375, MRR 0.5966) and is where the
remaining recall loss lives.

**Next step:** run `why_lost --ranks 2` against a fresh trace. The old
diagnostic targeted ranks 3,4,5 when the distribution was flatter; rank 2 now
dominates and has never been diagnosed on its own.

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

Measured against the live 0.862111, not `docs/pending.md`'s 0.7848-era figures.

| priority | item | impact | notes |
|----------|------|--------|-------|
| **high** | **Rank 2 → 1 recovery** | **+0.0292** | 39 sessions, one swap each. Pure reranking. Never diagnosed on its own — `why_lost --ranks 2` |
| high | Tune the dialogue block (`pending.md` P0) | unmeasured | still true and still untouched: `SEARCH_SPACE` in `tools/tune.py` has 20 params, **zero** from `dialogue`, though clarification ablates to −0.4473. Prerequisite: promote `state.observe(override_decay=)` and `state.query(recency_bonus=)` to `DialogueConfig` |
| high | Full GBDT / LambdaRank (P2) | up to +0.0811 | that is the perfect-rerank bound. Needs frozen retriever + penalty folded into features first — see below |
| medium | Browsing recall | ~+0.02 | 5 of 8 misses and the worst MRR (0.5966) are browsing |
| medium | Ranks 6-10 → 1 | +0.0246 | 19 sessions, likely the same fix as rank 2 |
| low | Learn `TAG_LEXICON` instead of hand-writing it (P4) | +0.0010 measured | `profile.py:22`; the feature it feeds ablates to inert |
| low | Decide the fate of the two inert components (P5) | ±0.002 | still undecided; both still shipped |

**`docs/pending.md` status** (written at 0.7848, so treat its numbers as
historical): P0 untouched · P1 **done**, though via per-intent ranking weights
rather than the retrieval multipliers it proposed · P2 untouched · P3 diagnosed
but superseded (18 misses → 8) · P4 untouched · P5 undecided.

## Open risk — modified evaluator on `origin/main`

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
well-tested changes, and it currently sits on the branch a submission would be
cut from. **Unresolved. Decide before submitting; do not let it be discovered
at judging.**

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
- **Refactoring for its own sake.** The competition ends before the payoff does.

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
end-to-end against all 200 sessions"* and *"not measured — ran out of time"* are
both useful; an entry with no status silently reads as verified. Say what you
ran, on what, and what you did not run. If a result came from a fold, a subset,
or a single seed, say so — see "Measurement discipline".

Check `git log -- CLAUDE.md` to see when it was last synced.
