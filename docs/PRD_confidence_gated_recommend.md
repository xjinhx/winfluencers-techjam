# PRD — Confidence-gated recommendation hold

**Status:** proposed, not implemented. All measurements below are verified
against the unmodified evaluator; no repo file has been changed.
**Date:** 2026-08-31
**Baseline:** `0.909328` (live `config/tuned.json`, post synthetic-span-normalisation)
**Proposed:** `0.936614` full-set, **fold B `+0.0259`**
**Provenance:** every number here came from a scratch config or an in-process
patch. `config/tuned.json` and `results.json` were never touched.

**Baseline re-verified 2026-08-31 14:50**, after a parallel branch landed
`brand_max_text_commonness`, the `ConstraintExtractor` commonness wiring
(`agent.py`), and the `kids`/`boys`/`girls` audience fix (`structured.py`) in
the working tree. Full evaluator on that tree: **0.909328, delta exactly
0.000000** — HR@10 1.0000, MRR 0.756095, MTTC 1.875, and all four scenario MRRs
identical to six decimals. Those changes are **score-neutral on the public
200**, the same latent-behaviour-change signature CLAUDE.md already records for
the NQC confidence formula. **All deltas in §3 therefore stand as measured** and
do not need rebasing.

---

## 1. Summary

Hold recommendations until the ranker has actually separated its candidates —
measured by the NQC confidence statistic `clarify.py` already computes — instead
of recommending on every turn.

Two new `DialogueConfig` fields, one gate in `agent.py`, no new features, no new
retrieval, no new dependency. Default-off and byte-identical when disabled.

**Worth `+0.027` full-set / `+0.026` on the held-out fold.** That is the largest
single measured gain available on the current build, and roughly two-thirds of
the total remaining headroom this codebase has identified.

---

## 2. The problem

Three rules in the evaluator interact badly, and the agent is currently on the
wrong side of all three.

**Rule 1 — the evaluator stops at the first hit** (`local_evaluator.py:252`):

```python
if override_applied and target in ranked:
    best_rank = ranked.index(target) + 1
    hit_turn = turn
    break
```

MRR is not the best rank achieved over a session. It is the rank at the
*earliest* turn the target enters the top 10, after which the session ends.

**Rule 2 — we recommend on every turn.** `recommend_on_ask_turns: True`
(`config.py:220`), applied at `agent.py:242`. We are permanently exposed to
Rule 1.

**Rule 3 — the customer discloses at most 2 spans per ask**
(`customer_reply`, `matches = [...][:2]`), against a card of at most 4 spans
(`intent_card`: `hard_constraints = cleaned[:2]`, `soft_preferences = cleaned[2:4]`).

Consequence: **the rank we are scored on is fixed at the moment we know least.**
Rule 1 does not reward finding the target — it rewards finding it *late enough
to rank it well*.

### 2.1 Turn 1 is close to information-free, by construction

| scenario | opening line (`initial_message`) | spans disclosed |
|---|---|---|
| buying | `I'm looking for {cat}. A key requirement is: {c}.` | 1 |
| browsing | `I'm looking for {cat}, but I'm still exploring.` | **0** |
| intent_override | `I'm looking for {cat}. {old_value}` | 1 — and it is about to be retracted |

`{cat}` is `coarse_category` — "Sleep & Lounge Sets", "Accessories Belts" —
naming hundreds of near-identical listings. **This is evaluator construction,
not a property of the public 200**, which is the primary reason this fix is
expected to transfer to the private set.

### 2.2 Measured: where the score is actually lost

From an instrumented replay of all 200 sessions with the first-hit `break`
removed, recording the target's rank at every turn:

```
              T1     T2     T3     T4     T5    total
rank 1        53     34     22     16     1      126
rank 2        20      6      4      0     0       30
rank 3-5      18     10      4      3     0       35
rank 6-10      7      1      0      1     0        9
```

**60% of the 75 sub-rank-1 sessions lock in at turn 1.** Card disclosed at
lock-in: 52% for rank-1 sessions, 33% for everything below.

What **one more turn** does:

| | n | improves | unchanged | worse | falls out of top 10 |
|---|---|---|---|---|---|
| locked in below rank 1 | 74 | **68%** | 30% | 1% | 1% |
| locked in at rank 1 | 126 | 0% | **98%** | 2% | 0% |

Median improvement where it improves: **2 places**. This asymmetry — large gain
where we are losing, near-zero cost where we are winning — is the entire basis
of the fix.

### 2.3 Why this is not the withholding experiment already rejected

CLAUDE.md records blanket withholding at **−0.0233**. That test ran at
HR@10 0.960 with `target_never_in_pool` 6, and its dominant cost was
**HR@10 −0.030** — targets falling out of the pool as boilerplate accumulated
(the `public_0100` mechanism). The conjunctive injection removed that:
`target_never_in_pool` is now **0** and HR@10 is **1.000**.

It also withheld until *the clarifier stopped asking* (budget 2–8), which lands
anywhere from turn 2 to turn 8. The measurements below show the payoff curve
turns over at turn 4, so that policy was structurally past the optimum.

**The prior rejection was correct for the system it was measured on. This is a
different mechanism on a different build, measured fresh.**

---

## 3. Evidence

### 3.1 Turn-gated baseline (the simpler alternative, measured first)

Verified against the **unmodified evaluator**, three full passes:

| policy | replay predicted | evaluator measured | HR@10 | MRR | MTTC |
|---|---|---|---|---|---|
| live | 0.909329 | 0.909328 | 1.0000 | 0.756 | 1.875 |
| hold until turn 2 | 0.926254 | **0.926254** | 1.0000 | 0.845 | 2.365 |
| hold until turn 3 | 0.934014 | **0.934014** | 0.9950 | 0.932 | 3.150 |
| hold until turn 4 | 0.920339 | **0.920339** | 0.9950 | 0.945 | 4.040 |

The replay harness reproduces the evaluator exactly. It is faithful because
withholding provably does not perturb the dialogue: `recommend_on_ask_turns`
gates only the recommendations list, and `customer_reply` reads only
`ask_attribute`.

### 3.2 Confidence-gated (proposed)

Same harness, gate on NQC instead of turn number. Verified live at two points:

| policy | replay | evaluator | HR@10 | MRR | MTTC |
|---|---|---|---|---|---|
| τ=0.05, fallback 4 | 0.934077 | **0.934077** | 1.0000 | 0.866 | 2.290 |
| τ=0.08, fallback 4 | 0.941314 | **0.941314** | 0.9950 | 0.932 | 2.785 |

**τ=0.05 matches `hold-3`'s score while keeping HR@10 at 1.0000 and MTTC
0.86 turns lower.** Same result, cheaper, no miss — a strictly better shape.

### 3.3 The threshold sweep, and why the plateau is what matters

Fallback turn 3 throughout (fb=3 beats fb=4 on fold B at every τ tested):

| τ | full | HR@10 | MRR | MTTC | fold A | **fold B** |
|---|---|---|---|---|---|---|
| 0.050 | 0.933552 | 1.0000 | 0.863 | 2.260 | +0.0278 | +0.0207 |
| 0.052 | 0.935052 | 1.0000 | 0.869 | 2.285 | +0.0289 | +0.0226 |
| **0.054** | **0.936614** | **1.0000** | 0.876 | **2.310** | +0.0287 | **+0.0259** |
| 0.056 | 0.935564 | 0.9950 | 0.886 | 2.390 | +0.0303 | +0.0222 |
| 0.060 | 0.938064 | 0.9950 | 0.898 | 2.440 | +0.0352 | +0.0223 |
| 0.070 | 0.939364 | 0.9950 | 0.911 | 2.570 | +0.0365 | +0.0236 |
| 0.080 | 0.940639 | 0.9950 | 0.920 | 2.650 | +0.0368 | +0.0259 |
| 0.085 | 0.941314 | 0.9950 | 0.927 | 2.710 | +0.0380 | +0.0260 |
| 0.090 | 0.940614 | 0.9950 | 0.927 | 2.745 | +0.0378 | +0.0248 |

**Read this as a plateau, not a peak.** Fold B is flat at +0.021 to +0.026
across the whole range τ ∈ [0.052, 0.090]. That is the non-overfitting signature
this repo already trusts from `per_field_depth` and
`constraint_commonness_penalty`, and the opposite of `candidate_depth`'s.

**The fine structure is one session.** The dip at τ=0.056 and the recovery by
τ=0.080 are both `public_0020` (which sits in fold B) being lost and then repaid
by MRR gains elsewhere. Do not read 0.054 or 0.085 as optima.

### 3.4 Held-out selection and bootstrap

Selecting on fold A and scoring on fold B — the honest generalisation test:

| | selected on A | **fold B** |
|---|---|---|
| turn-gate | hold 3 | +0.019250 |
| **confidence-gate** | τ=0.08, fb=4 | **+0.023450** |

Paired bootstrap, 20,000 resamples of the 200 sessions:

| policy | mean | 95% CI | P(Δ ≤ 0) |
|---|---|---|---|
| hold 3 | +0.024700 | [+0.010621, +0.038046] | 0.1% |
| conf τ=0.08 fb=4 | +0.032000 | [+0.018248, +0.045150] | **0.0%** |

Confidence-gating beats turn-gating on the full set, on fold B, and on CI width.

### 3.5 The statistic has real resolution

NQC at turn 1, split by whether the target is already at rank 1:

```
target at rank 1 (n=70)   median NQC = 0.0679
target elsewhere (n=130)  median NQC = 0.0376
```

Roughly 1.8×. Modest but real, and it is the signal the gate runs on.

**Critical operational fact:** the entire observed NQC range is
**[0.0107, 0.1935]**, median 0.0431. The existing `ask_max_confidence = 0.82`
sits far above anything this system ever produces — which is exactly why
CLAUDE.md records NQC as "a no-op on the public 200 that never once flips a
gate decision." **That gate is not broken; it is unreachable.** The new
threshold is the same statistic calibrated to the observed distribution.

---

## 4. Design

### 4.1 Config (`shopping_copilot/config.py`, `DialogueConfig`)

```python
# Recommendation gate -- SEPARATE from the ask gate above, and on a
# DIFFERENT SCALE. `ask_max_confidence` (0.82) sits above the entire
# observed NQC range [0.011, 0.194] and therefore never fires; this one is
# calibrated to that range. Same statistic, unrelated numbers -- do not
# tune them together or reason from one to the other.
#
# 0.0 disables the gate entirely (byte-identical to prior behaviour).
min_recommend_confidence: float = 0.0
recommend_turn_fallback: int = 3
```

### 4.2 Single source of truth for NQC (`shopping_copilot/clarify.py`)

`ClarificationPolicy._confidence` currently owns the formula privately. The gate
needs the same number. **Do not reimplement it** — this repo has already been
bitten once by exactly that (`tools/offline_eval.py`'s `ReplayScorer` silently
double-counting the unknown-penalty after `features.py` changed).

Promote to a module-level function and have both callers use it:

```python
def nqc(scores: list[float]) -> float:
    """Normalized Query Commitment (Shtok et al. 2009) -- how far the ranker
    has committed to separating good candidates from bad, with no ground
    truth available. High spread relative to the top score means the ranker
    clearly pulled some candidates ahead; bunched scores mean it did not."""
    if len(scores) < 2:
        return 1.0
    top = scores[0]
    if top <= 0:
        return 0.0
    window = scores[:10]
    mean = sum(window) / len(window)
    variance = sum((s - mean) ** 2 for s in window) / len(window)
    return max(0.0, min(1.0, (variance ** 0.5) / abs(top)))
```

`ClarificationPolicy._confidence` becomes a one-line delegation, preserving its
existing behaviour exactly.

### 4.3 The gate (`shopping_copilot/agent.py`, after `clarification = ...`)

```python
recommendations = (
    [{"parent_asin": asin} for asin in ordered]
    if (clarification.attribute is None or self.config.dialogue.recommend_on_ask_turns)
    else []
)

# Recommendation gate. The evaluator breaks on first hit, so an early list
# permanently locks in whatever rank it has -- and 60% of sub-rank-1
# sessions lock in at turn 1, before the customer has disclosed the spans
# that separate the target from its imposter. Hold until the ranker has
# actually committed, or until the fallback turn, whichever comes first.
dialogue = self.config.dialogue
if recommendations and dialogue.min_recommend_confidence > 0.0:
    if (turn < dialogue.recommend_turn_fallback
            and nqc([s for _, s in ranked]) < dialogue.min_recommend_confidence):
        recommendations = []
```

Composes with `recommend_on_ask_turns` as an AND — either can suppress.

### 4.4 Deliberately not gated: `_empty_response`

`agent.py:_empty_response` fires when there is no lexical or dense match at all
and returns `state.last_ranking[:top_k]` — the *previous* turn's ordering. There
are no scores to compute NQC from, and it is already a degraded path. Leave it
ungated and document it. **Flag for review:** nobody has measured how often this
path fires; if it is non-trivial it deserves its own decision.

---

## 5. Recommended operating point

**`min_recommend_confidence = 0.054`, `recommend_turn_fallback = 3`.**

| | value |
|---|---|
| full 200 | **0.936614** (+0.027286) |
| HR@10 | 1.0000 |
| MRR | 0.876 |
| MTTC | 2.310 |
| fold A | +0.028671 |
| **fold B** | **+0.025900** |

**Why the low edge of the plateau rather than the high end**, given fold B is
flat and τ=0.085 scores higher on the full set:

1. **Fold B is tied** (+0.0259 vs +0.0260). There is no held-out argument for
   holding longer — the full-set advantage at τ=0.085 lives almost entirely in
   fold A.
2. **MTTC is 0.4 turns lower** (2.310 vs 2.710) — worth ~+0.008 of efficiency,
   and it is the term that decays if the private set's disclosure is slower.
3. **The failure modes are asymmetric.** Under-holding degrades gracefully
   toward the live baseline; over-holding falls off a cliff (fold B decays past
   τ=0.09, and both `hold-4` and τ=0.15 go net negative).
4. **Shorter withholding is the defensible-UX direction** — see §7.

---

## 6. Risks

### 6.1 `public_0020` — the HR@10 cliff is one session deep

This is the single session that breaks HR@10 anywhere in the sweep, and its
trajectory explains the entire cliff:

```
turn   rank    NQC
   1   None   0.0258
   2      6   0.0546   <- the ONLY turn it is ever visible
   3+  None   0.0205
```

Its confidence at its sole visible turn is **0.0546** — between τ=0.054 (show,
keep the hit) and τ=0.056 (hold, lose it permanently).

**Therefore: do not treat "HR@10 = 1.0000 at τ ≤ 0.054" as a property of the
threshold. It is a property of this sample, one session deep.** On the private
800 expect to lose roughly 0.5% of sessions to this at any τ in the plateau —
already priced into the fold-B figure. Reporting "no HR@10 cost" as a durable
guarantee would be over-claiming.

### 6.2 Selection-on-noise

Roughly 20 configurations were scored against these same 200 sessions in the
session that produced this document, on top of the 16+ CLAUDE.md already
records. **Quote fold B, not the full-set number**, in anything downstream.

The mitigating structural argument: this is **one parameter over a smooth, broad
plateau**, the safest shape of fit available — categorically unlike the learned
reranker work that failed here with 33 free parameters on 100 sessions.

### 6.3 The gain is partly a scoring artifact, not a customer behaviour

Stated plainly because it affects how this should be *described*, not whether to
ship it. The mechanism that makes holding pay is `local_evaluator.py:252` —
**the session dies at first hit.** Real shoppers do not do that; a real customer
shown a mediocre list keeps talking and you get another attempt for free.
First-hit-break is a measurement convention for time-to-first-success, not a
model of customer patience.

The gain therefore decomposes into a genuinely good behaviour ("do not commit
before you can be useful") and harvesting a convention with no real-world
counterpart. **This is optimising against the evaluator, which is the task. It
is not evidence that users would prefer it.**

### 6.4 `tools/offline_eval.py` is BROKEN by this change — confirmed, with fix

**Checked 2026-08-31. This is not a hypothetical: the validation gate will
produce false disagreements on every gated session unless `offline_eval` is
patched in the same commit.**

Two facts combine:

1. **The trace is written unconditionally.** `agent.py` calls
   `self._log(state, ctx, candidates, turn)` *before* the
   `recommendations = (...)` decision, so a suppressed turn still emits its
   full set of feature rows.
2. **`replay_session` has no notion of a withheld turn.** It reconstructs the
   ordering from those rows and tests membership directly
   (`tools/offline_eval.py:159-163`):

   ```python
   ordered = rank_turn(turns[turn], score_fn)[:TOP_K]
   if override_applied and target in ordered:
       best_rank = ordered.index(target) + 1
       hit_turn = turn
       break
   ```

Consequence: the live agent stays silent at turn 1, while replay finds the
target at turn 1 and records the hit there. They disagree on precisely the
sessions the gate is designed to help — so the 200/200 gate would fail loudly,
and worse, could be "fixed" by someone weakening the gate rather than the tool.

**The fix is small, and it avoids the duplication hazard §4.2 exists to
prevent.** `rank_turn` already computes the scores and then discards them
(`return [asin for _, asin in scored]`). Return them instead, and let
`replay_session` apply the same gate using the *same* `nqc` function:

```python
def rank_turn(rows, score_fn):
    scored = [...]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [asin for _, asin in scored], [s for s, _ in scored]
```

```python
ordered, scores = rank_turn(turns[turn], score_fn)
gated = (config.dialogue.min_recommend_confidence > 0.0
         and turn < config.dialogue.recommend_turn_fallback
         and nqc(scores) < config.dialogue.min_recommend_confidence)
if not gated and override_applied and target in ordered[:TOP_K]:
    ...
```

This is exact rather than approximate: `replay_session` already reproduces the
live scores (that is what the 200/200 gate asserts), and the live gate reads
`[s for _, s in ranked]` — the full ranked list, of which `nqc` uses only the
top 10. Same inputs, same function, same result.

**Note `replay_session` does not currently take a `Config`.** Threading one in
is part of this work.

---

## 7. What the outside literature says

Researched because "does this match real customer behaviour?" is a fair question
to ask of any policy that withholds results.

**Supports it.** The ask-vs-recommend decision is first-class in the CRS
literature, and `clarify.py` already implements the canonical gate.
[EAR (Lei et al., WSDM 2020)](https://arxiv.org/abs/2002.09102) holds that a
system should act only when the recommender is confident its top results will be
accepted — **which is exactly this gate, finally wired to the recommendation
instead of only to the ask.** Turn budget is well inside tolerance:
[an empirical study of clarifying-question e-commerce systems](https://arxiv.org/pdf/2008.00279)
found users answer **11.4 questions per product** on average.

**Undercuts it.** [Three experiments on withheld information in conversational
interfaces](https://pmc.ncbi.nlm.nih.gov/articles/PMC11008880/)
(n = 1,811 / 905 / 801) found conversational delivery makes withholding *worse*
than in a normal UI — lower willingness to use, better recall that something was
withheld, and inferred motive. Critically, **pairing conversational and visual
modes reduced the effect**: showing results *alongside* the question is the
mitigation, and that is precisely what this policy stops doing.

**Net:** a confidence gate is far more defensible than a fixed turn threshold,
because it makes a claim about *this* session ("the ranking has separated") that
a person could in principle check, rather than a population average pretending
to be a decision. But the withholding itself remains the weaker half, and §5's
preference for the shortest hold is partly motivated by this.

---

## 8. Acceptance criteria

Ordered; each must pass before the next is meaningful.

1. **Default-off is byte-identical.** With `min_recommend_confidence = 0.0`,
   the unmodified evaluator reproduces **0.909328** exactly.
2. **The existing test suite passes** at both default-off and the chosen config.
3. **`nqc` has a single definition.** `ClarificationPolicy._confidence`
   delegates to it; no second copy anywhere, `tools/` included.
4. **Live score at the chosen config reproduces 0.936614** on the unmodified CLI
   evaluator, HR@10 1.0000, MTTC 2.310.
5. **`tools.offline_eval` agrees 200/200 on `best_rank`** — **requires the
   `offline_eval` patch in §6.4 first.** Without it this gate fails by
   construction, on exactly the sessions the change is meant to help. Land the
   tool fix in the same commit as the gate.
6. **`stratified_halves(seed=7)` fold B ≥ +0.020.**
7. **`results.json` untouched**; every experimental run to a scratch path
   (Critical rule 1). **`config/tuned.json` edited only at adoption**, in the
   same commit as the CLAUDE.md entry (the `.githooks/pre-commit` guard).

---

## 9. Out of scope

- **Tuning `ask_max_confidence` alongside this.** Different decision, and the
  two numbers are unrelated despite sharing a statistic (§4.1).
- **Per-scenario thresholds.** The agent never receives `scenario_type`. An
  intent-conditional variant is possible via `intent.route()` but is a separate,
  additionally-fitted change — and `buying` is the only scenario carrying an HR
  risk, so it is the obvious follow-up rather than part of this.
- **Changing which attribute is asked.** Measured and rejected in the same
  session: always-`other` scored **0.898705** vs 0.909328 (−0.0106), HR@10
  1.000 → 0.990, *despite* handing the agent strictly tighter constraints
  (median tightest span 0.14% of catalog vs 1.23% for the incumbent
  `feature`-first ask). The query-pollution explanation was tested via
  `constraint_commonness_penalty` {0.30, 0.50, 0.70} and **failed to recover it**
  (0.898705 → 0.898552 → 0.897008). Unexplained; do not retry without a new
  hypothesis.
- **Raising the ceiling further.** With this fix the measured oracle ceiling on
  this lever is ~0.9596. The remainder is structural.

---

## 10. Rollback

`min_recommend_confidence = 0.0`. One config value, no code path removal,
byte-identical to today. This is why the gate is written as `> 0.0` rather than
as a boolean.

---

## Appendix — reproduction

Scratch harnesses used (all read-only against the repo, none committed):

- `no_break_conf.py` — instrumented run recording target rank *and* NQC at every
  turn, first-hit `break` removed. ~3 min, 200 sessions.
- `gate_sim.py` — scores any gate policy off those trajectories in ~1s.
  **Sanity gate: it reproduces the four live turn-hold numbers to 5 decimals
  before any new policy is trusted.**
- `hold_conf.py` — live verification against the unmodified evaluator.

The replay approach is ~40× cheaper than a live run and exact for
recommend-timing policies only. It is **not** valid for changes that alter the
ask sequence or the ranker — those still need a full run.

```powershell
$env:SHOPPING_COPILOT_CONFIG = "c:\tmp\cfg_conf_gate.json"
python -m evaluator.local_evaluator --output c:\tmp\out_conf_gate.json
```
