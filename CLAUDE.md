# CLAUDE.md

TechJam Conversational E-Commerce Search Challenge. A multi-turn shopping agent
that finds a hidden target product as early and as highly ranked as possible.

Architecture and method are documented already — read those rather than
re-deriving them: `README.md`, `agent_architecture.md`, `docs/report.md`,
`docs/competition_specification.md`, `docs/submission_rules.md`. This file holds
what those do not: current state, and the operational facts that cost time to
discover.

## Current state

| | score | status |
|---|---|---|
| `config/tuned.json` as committed | **0.784838** | live, recorded in `results.json` |
| intent-conditional `w_fused` | **0.8091** | live-confirmed, **not adopted** |

The code for the intent-conditional weight is in place, but no overrides are set
in `config/tuned.json`, so behaviour is unchanged and the baseline still
reproduces exactly. To adopt, add to the `ranking` block:

```json
"w_fused_buying": 0.0,
"w_fused_uncertain": 0.0
```

then re-run the evaluator to regenerate `results.json`. This is a deliberate
open decision, not an oversight — see the noise caveat below before adopting.

Uncommitted: `shopping_copilot/config.py`, `shopping_copilot/ranking.py`,
`tests/test_shopping_copilot.py` (modified); `tools/offline_eval.py`,
`tools/why_lost.py` (new).

The diagnostic working documents (`PRD_rank_diagnostic.md`,
`docs/rank_diagnostic.md`) are gitignored by choice and exist only on the
original machine. Everything load-bearing from them is reproduced below, so this
file stands alone.

## Never do these

- **Never let the evaluator overwrite `results.json`.** It is gitignored, so
  there is no committed copy, and it is the only record of the reported score.
  Always pass `--output <scratch>/whatever.json` when experimenting.
- **Never edit `config/tuned.json` to run an experiment.** Copy it to a
  scratchpad, edit the copy, and point `SHOPPING_COPILOT_CONFIG` at it.
- **Never write to `data/`.** The catalog is frozen; catalog mutation is out of
  scope per the rules. `data/catalog.jsonl` is 60 MB, gitignored, and downloaded
  from the participant release — a fresh clone will not have it.
- **Never modify `evaluator/`.** Explicitly forbidden by the submission rules.

## Evaluator facts that are not obvious from the code

Each of these will silently corrupt an offline analysis if got wrong.

- **Sessions cannot be joined to labels by id.** The evaluator hands the agent a
  fresh `uuid4` (`local_evaluator.py:227`); the agent never sees `sample_id`, by
  design. Join positionally — `evaluate()` iterates `for sample in samples`
  single-threaded, so the Nth distinct session id in a trace is the Nth sample.
- **The override turn is drawn per sample**, `rng.choice([3, 4])` seeded on
  `f"{sample_id}\0{scenario_type}"` — not fixed at 3. Reproduced in
  `offline_eval.override_turn_for`.
- **`best_rank` is not the rank in the full pool.** It is the first turn where
  the target appears in the returned top-10, and for `intent_override` sessions
  any hit before the override turn is ignored (`local_evaluator.py:234,252`).
- **The unknown-penalty is applied outside the model** (`ranking.py:120-129`).
  Three of six are non-zero under the tuned config: gender −0.01, category
  −0.005, price −0.005. Scoring with `LinearModel` alone will not reproduce the
  ordering.
- **MMR never fires.** `enable_mmr = False` in the tuned config, so the
  `diversify=` path is dead. Ties break on `parent_asin`.
- **`difficulty_bucket` is a deterministic function of `scenario_type`**
  (buying→easy, browsing→medium, intent_override→hard, boundary→medium). It
  carries no extra information; slicing on it is redundant.
- **The agent cannot exceed 10 turns.** The evaluator's own loop is
  `range(1, MAX_TURNS + 1)`, so the "zero score if exceeded" rule cannot trigger.

## The objective is not MRR

`TechnicalScore = 0.5·HR@10 + 0.3·MRR + 0.2·efficiency`, where
`efficiency = clip((11 − MTTC)/10, 0, 1)` and a miss counts as 11 turns.

HR@10 carries the largest weight. When `w_fused` was dropped, MRR contributed
only 17% of the resulting gain; HR was 50% and efficiency 32%. Any plan framed
purely around MRR is optimising the smallest term — check the decomposition
before committing to one.

## Measurement discipline

This is the single most important thing to carry forward.

- **Paired MRR SE is ~0.024** across 200 sessions (bootstrap, 20k resamples).
  The unpaired single-run SE is 0.029. A change worth less than roughly +0.05
  MRR cannot be distinguished from noise on this set.
- **Report the sign test and the CI together.** They routinely disagree: the
  adopted change gives 64 sessions up / 25 down (p = 4.3e-05) while its MRR CI
  is [−0.017, +0.071] and contains zero. Direction can be solid while magnitude
  is not. Saying only one of those overstates the result.
- **Sixteen configurations have now been evaluated against the same 200
  sessions.** Selection-on-noise is real and accumulating. Validate with
  `tools.tune.stratified_halves` (seed 7) and quote fold B, the conservative
  half — for the adopted change, fold A said +0.0364 and fold B said +0.0121.
  Expect the private 800 nearer the fold-B figure.

## Tooling

```bash
# full evaluator, 3m08s  (never target results.json)
SHOPPING_COPILOT_CONFIG=<scratch>/cfg.json \
  python -m evaluator.local_evaluator --output <scratch>/out.json

# offline replay of a trace, 9.8s -- has predicted the live score exactly twice
python -m tools.offline_eval --trace <scratch>/features.jsonl \
  --expect-mrr 0.564792 --against <scratch>/out.json

# which feature is costing rank 1
python -m tools.why_lost --trace <scratch>/features.jsonl --ranks 3,4,5 --top 30

python -m unittest discover -s tests    # 32 tests
```

Set `trace_path` in a scratchpad config to emit `features.jsonl` (~115k rows per
run, ~45 MB). Tracing is passive and verified not to change results. Keep traces
out of git; they regenerate in 3 minutes.

`tools/offline_eval.py` reproduces the live ranker exactly — validated by all 200
sessions agreeing on `best_rank`, not just by matching aggregate MRR. Keep that
gate. It is what caught both join bugs above.

## What was found

`fused` is a convex combination of the same lexical and dense signals that also
enter the feature vector separately, so text evidence was counted twice. Across
the rank 3–5 band it carried **68% of the score gap** while every structured
constraint feature carried **exactly 0.0** — the target and the candidates
beating it were identical on every constraint dimension.

Cutting `fused` globally regresses browsing (−0.0118), because a vague browsing
turn has no disclosed constraints to drown and the fused score is the best
evidence available there. Cutting it only for `buying`/`uncertain` turns that
into +0.0312 and improves all four scenarios. That sign flip in a held-out slice
is the real evidence, more than the headline number.

Implementation: `w_fused_{buying,browsing,uncertain}` on `RankingConfig`, each
falling back to `w_fused` when unset; `Ranker` builds one `LinearModel` per
overridden intent and selects on `ctx.intent`. The per-intent map is built only
when no `model` was supplied, so the `ScoringModel` seam stays free for a GBDT.

## Where the remaining headroom is

Ceiling is **0.970**; current live-confirmed best is 0.8091.

- **MRR: +0.101 available** — 93 of 186 hits still sit at ranks 2–10. Ranks 2–5
  alone are 63 sessions worth **+0.0623**. This is the main prize.
- **HR: only +0.020 available.** Hard-capped at 0.970 — 6 targets never enter
  the candidate pool under any turn and are unreachable by reranking.
- **Efficiency: +0.020.** Capped at 0.930, not 1.0, because misses always cost
  11 turns in MTTC.

Of the 18 baseline misses, 12 had the target in the pool but never reached
top-10, at best full-pool positions [1, 11, 11, 15, 16, 16, 17, 21, 28, 39, 39,
87]; nine are within position 30. Only 6 are true retrieval failures.

Next step is to run `why_lost` against a trace from the intent-conditional config
and see what tops the table now that `fused` is fixed. That trace already exists
if the scratchpad survived (`features_intent.jsonl`, 104,800 rows); otherwise
regenerate it in 3 minutes. Expect a smaller finding — double-counting was a
structural error and those are rare.

The intent-conditional config was verified deterministic: a traced and an
untraced run produce identical metrics and identical 200 session records
(0.809074 both times).

## On LambdaRank / LightGBM

Assessed and deferred, not rejected. In scope per the official rules ("training
or full-parameter fine-tuning of base foundational LLMs" is what is excluded; a
GBDT over handcrafted features is "local scoring logic", which is explicitly in
scope). Two things to know:

- The diagnostic found a **feature-weighting** defect. A GBDT trained on those
  vectors would have inherited the same double-counted signal. Retrain only
  against corrected weights, and freeze the retriever first — negatives are
  mined from its own output.
- **Fold the unknown-penalty into the feature vector first.** It is a hand-tuned
  additive constant on `LinearModel`'s output scale; a GBDT's raw output is not
  on that scale, and the failure looks like the model underperforming.

`requirements.txt` is deliberately empty — the system is stdlib-only, which the
README sells as a hedge against organiser network restrictions. Adding LightGBM
trades that away. Note it is currently installed in **global Python 3.12, not
the project `.venv`** that runs the agent. Keep a `LinearModel` fallback so a
missing binary degrades instead of failing.
