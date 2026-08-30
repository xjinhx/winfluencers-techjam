# Pending work

Things the code does not yet do, ordered by expected value. Everything here is
grounded in a measurement from the current build (tuned config, 0.7848 on the
200 public sessions) rather than intuition.

**Where the remaining score actually is:**

| source | score available | note |
|---|---|---|
| perfect reranking of hits we already find | **+0.1036** | 92 of 182 hits land below rank 1 |
| converting all 18 misses at rank 1 | +0.0720 | plus a small Efficiency gain |
| Efficiency (MTTC 2.98 → floor 1.30) | +0.0336 | floor is structural, see below |

Reranking is worth more than recall. That should drive the ordering of anything
we pick up next.

---

## P0 — Tune the dialogue parameters

**Why this is first.** Clarification is worth −0.4473 in ablation, an order of
magnitude beyond any other component, and **not one of its parameters has ever
been tuned.** The coordinate-ascent search space in `tools/tune.py` covers
`retrieval` (6), `ranking` (10), `priors` (3) and `constraints` (1) — and zero
from `dialogue`. We tuned the parts worth hundredths and left the part worth
tenths at its hand-picked defaults.

Untuned parameters that exist today:

```
dialogue.ask_min_candidates      12     EAR gate 1: pool size to still narrow
dialogue.ask_max_confidence      0.82   EAR gate 3: confidence to stop asking
dialogue.ask_turn_budget         8      turn at which we stop asking entirely
dialogue.ask_min_info_gain       0.05   minimum expected gain to spend a turn
dialogue.attribute_prior_floor   0.05   floor under measured answer rates
dialogue.repeat_ask_decay        0.45   discount for re-asking an attribute
dialogue.intent_buying_threshold 0.65   (inert today — see P1)
dialogue.intent_browsing_threshold 0.35 (inert today — see P1)
```

**How.** Add a `dialogue` block to `SEARCH_SPACE` in `tools/tune.py`. The
existing split-half protocol applies unchanged. Suggested grids:

```python
("dialogue", "ask_turn_budget",      [4, 6, 8, 10]),
("dialogue", "ask_max_confidence",   [0.60, 0.72, 0.82, 0.92]),
("dialogue", "ask_min_candidates",   [4, 12, 30, 60]),
("dialogue", "repeat_ask_decay",     [0.2, 0.45, 0.7, 1.0]),
("dialogue", "ask_min_info_gain",    [0.0, 0.05, 0.12]),
```

**Watch for.** `ask_turn_budget` trades HR@10 against MTTC directly — asking
later can find more targets while costing Efficiency. The composite score is the
right arbiter, which is why the tuner optimises it rather than a proxy. Also
note `state.observe(override_decay=0.25)` and `state.query(recency_bonus=0.15)`
are keyword defaults, not config fields; promote them to `DialogueConfig` before
tuning them.

**Cost.** ~5 parameters × 4 values × 55 s ≈ 20 min per pass.

---

## P1 — Make the intent router affect the output

**Current state.** `intent.py` scores every turn BUYING / BROWSING / UNCERTAIN
from four features. Its only consumer is:

```python
# agent.py
ranked = self.ranker.rank(candidates, ctx, top_k,
                          diversify=(decision.intent == BROWSING))
```

…and `RankingConfig.enable_mmr` is `False` by default, because MMR ablated to
exactly **+0.0000**. So the router computes a label that reaches the trace log
and changes nothing. The docs say this plainly; the alternative is to make it
true.

**Proposal.** Let the route select a retrieval weight profile, which is what the
architecture claimed all along. Browsing turns have a category and little else,
so they should lean on `categories` and the semantic route; buying turns carry
quoted product copy, so they should lean on `features` and phrase evidence.

Concretely, add to `RetrievalConfig` a small set of per-intent multipliers and
apply them in `agent._respond` before `lexical.search`:

```python
BROWSING:  w_categories x1.3, w_features x0.8, fusion_alpha -0.10  (more semantic)
BUYING:    w_features   x1.2, w_categories x0.9, fusion_alpha +0.05
UNCERTAIN: unchanged
```

Then tune the multipliers, and **ablate the router against a fixed profile.**

**Be prepared for a null result.** The per-field weighting ablation already
measures at +0.0007, i.e. uniform field weights are as good as our tuned ones on
this data. If field weights barely matter globally, making them
intent-conditional may also measure at zero. That would be a legitimate finding
worth reporting — a null result with an ablation behind it is a stronger claim
than an unmeasured component — but it means P1 should not be attempted before
P0.

**Decision point.** If it measures at zero after tuning, choose deliberately
between keeping it documented-as-inert (current state) and removing `intent.py`
from the pipeline. Do not leave it undecided.

---

## P2 — LambdaRank reranker

**Why.** +0.1036 of score sits in reranking hits we already retrieve: 92 of 182
hits land below rank 1, with the mass at ranks 3–5 (45 hits). A pairwise
learning-to-rank objective weights each swap by its effect on the metric, which
is exactly where this headroom lives.

| rank | hits | score if promoted to 1 |
|---|---|---|
| 2 | 13 | +0.0097 |
| 3 | 15 | +0.0150 |
| 4 | 18 | +0.0203 |
| 5 | 12 | +0.0144 |
| 6–10 | 34 | +0.0441 |

**What already exists.** The whole path is built and unused:

- `features.extract()` is a pure function of (candidate, context) — the same code
  runs live and in replay, so a model cannot be trained on features the agent
  does not emit
- `config.trace_path` appends one JSON row per scored candidate per turn
  (verified writing 30-dim vectors)
- `features.as_row()` emits a labelled training row
- `ranking.ScoringModel` is the protocol a fitted model implements;
  `LinearModel` is the current one and `LinearModel.load()` reads JSON weights

**Sequence, and the order matters.**

1. Run the evaluator with `trace_path` set to log every session
2. Join rows to ground truth offline to attach labels
3. **Freeze the retriever.** Negatives are mined from this retriever's own
   output, so the model learns to correct *that* retriever. Change retrieval
   afterwards and the model is stale
4. Fit LightGBM `lambdarank`, grouped by session
5. Constrain hard: `num_leaves` 7–15, high `min_data_in_leaf`, 5-fold CV grouped
   by session
6. **Ship it only if it beats tuned-linear on held-out folds.** If it does not,
   ship linear and say so

**Blocker.** LightGBM is not installable under the offline constraint the
submission targets (`requirements.txt` is deliberately empty). Either the
scoring environment permits a wheel, or the fitted model must be exported to
something the stdlib can evaluate — a plain tree dump walked in Python would
work and keeps the zero-dependency guarantee.

---

## P3 — Diagnose the 18 misses

Worth +0.0720 if all convert, though some will be genuinely unreachable.

| scenario | misses | rate |
|---|---|---|
| browsing | 9/80 | 11% |
| buying | 6/80 | 8% |
| intent_override | 3/30 | 10% |

**First question to answer, because it splits the work in two:** for each miss,
was the target ever in the candidate pool? Add a debug mode that reports the
target's rank in the 200-candidate pool per turn.

- **Target in pool, ranked >10** → a reranking failure, and P2 addresses it
- **Target never in pool** → a retrieval failure, needing query expansion or
  more depth. Note `candidate_depth` is already 200 and the ablation shows depth
  still mattering at that size (−0.0318 at depth 20), so it may not be saturated

Browsing being the worst track is consistent with those sessions disclosing
least; check whether misses correlate with total characters disclosed by turn 10.

---

## P4 — Bridge the preference-tag vocabulary gap

The nine `preference_tags` are abstract and map onto no catalog field:

```
fit 163 · material 154 · comfort 144 · style 101 · durability 47
performance 26 · warmth 18 · weather 12 · general shopping 1
```

`profile.TAG_LEXICON` currently hand-maps each tag to listing words, and the
resulting `profile_affinity` feature ablates to **+0.0010** — inert, marginally
better removed.

**Better approach.** Learn the mapping instead of writing it: for each tag,
compute the terms over-represented in the titles/features of targets from
sessions carrying that tag, versus the catalog base rate. Requires no new data —
the 200 public sessions have both the tag and the target.

**Caveat.** 200 sessions across 9 tags is thin (warmth appears 18 times, weather
12, "general shopping" once), so anything learned here needs the split-half
treatment before it is believed.

---

## P5 — Decide the fate of the two inert components

Both currently measure marginally *better* when removed:

| component | delta when removed |
|---|---|
| per-field lexical weighting | +0.0007 |
| profile personalisation | +0.0010 |

Both are inside noise, so neither is evidence of harm — but neither earns its
place either. They are retained on the argument that the private set is 4×
larger and these are the parts most likely to matter on phrasing this simulator
never produces. That argument should be tested rather than repeated: re-run the
ablation after P0/P1 and, if they are still inert, either cut them or state the
retention as an explicit bet.

---

## Not planned, deliberately

- **Popularity debiasing / calibrated recommendation.** That literature exists to
  correct a bias these labels contain by construction. Applying it means fighting
  the metric. The right response is the honest framing already in the README, not
  a technical fix.
- **LLM reranking.** Listwise LLM rankers are order-sensitive, which is a
  determinism problem for a graded submission, and official scoring may run
  without network access.
- **A real neural dense route.** The `EmbeddingRoute` seam exists, but the
  current semantic route ablates to −0.0013 — the route is barely contributing at
  all, so upgrading it is unlikely to pay before P0–P2. Revisit only if the
  offline constraint is lifted.
- **Beating the MTTC floor.** 1.30 is structural: intent-override sessions
  cannot convert before the override lands on turn 3–4. Our 2.98 is mostly the
  0.99 contributed by misses, so P3 improves MTTC more directly than any
  dialogue change.

---

## Method notes for whoever picks this up

**Ablate on all 200 sessions, never the 80-session subset.** We ran the table on
80 first and three readings were wrong, including one component we nearly cut.
At 80 sessions one session is 1.25 points of HR@10, so anything under ~0.02 is
sampling noise. `tools/ablate.py --subset` exists for iteration speed only.

**Fold noise is 0.025.** The two split-half folds differ by that much under
identical default weights (0.7518 vs 0.7763). Any claimed improvement smaller
than that needs the full set or a different seed before it is believed.

**Re-validate after any retrieval change.** `config/tuned.json` was fitted
against a specific retrieval configuration. Changing field weights, depth, or
fusion invalidates it — re-run `python -m tools.tune`.
