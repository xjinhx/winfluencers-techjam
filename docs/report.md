# Shopping Copilot — method report

An offline, deterministic, dependency-free conversational shopping agent for the
TechJam Conversational E-Commerce Search Challenge.

| | HR@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---|---|---|---|---|
| Weak BM25 baseline | 0.125 | 0.0680 | 9.81 | 0.119 | 0.1067 |
| Shopping Copilot, shipped defaults | 0.885 | 0.5535 | 3.23 | 0.778 | 0.7641 |
| **Shopping Copilot, tuned** | **0.910** | **0.5648** | **2.98** | **0.802** | **0.7848** |

Measured on all 200 public sessions with the unmodified official evaluator
(`python -m evaluator.local_evaluator`).

**Read the tuned row with the caveat it deserves.** Parameters were fitted on a
100-session half of this same set, so the 0.7848 is measured partly on data it
was tuned on and is optimistically biased. The unbiased estimate is the held-out
half: **0.7763 → 0.7869** (+0.011), fitted without ever seeing those sessions.
Expect the private set to land nearer the holdout figure than the headline one.
Section 8 gives the protocol.

---

## 1. Architecture

The system is a package, one module per component of the design. Nothing in the
turn path calls a network or a model.

```
parse ─→ route intent ─→ retrieve (A + B, fused) ─→ rerank ─→ clarify
```

| module | role | design section |
|---|---|---|
| `text.py` | one tokeniser for index and query time | — |
| `catalog.py` | catalog load, normalisation, coverage flags, priors | P2 |
| `index.py` | per-field BM25, one inverted index per field | §3 Route A |
| `dense.py` | character-n-gram semantic route (see §6 below) | §3 Route B |
| `structured.py` | gender / brand / category slots, three-way checks | §3 Route C |
| `fusion.py` | TM2C2 normalisation, convex combination | §3 |
| `state.py` | utterance parsing, slot accumulation, override rewrite | §2 |
| `intent.py` | BUYING / BROWSING / UNCERTAIN router | §1 |
| `profile.py` | anonymised-profile personalisation | §4a |
| `features.py` | the 30-dimension feature vector, as a pure function | §4a |
| `ranking.py` | linear scorer, MMR diversity | §4b, §6 |
| `clarify.py` | EAR-style clarification gate | §5 |
| `agent.py` | orchestration, contract conformance | §9 |

`starter/agent.py` is the entry point the harness imports. It only selects a
configuration, so the submission seam stays stable.

---

## 2. What actually moved the score

The ablation table is the argument. Each row disables exactly one component
against the full system, over all 200 sessions (`python -m tools.ablate`).

| component removed | HR@10 | MRR | MTTC | TechnicalScore | delta |
|---|---|---|---|---|---|
| *full system* | 0.885 | 0.5535 | 3.23 | 0.7641 | — |
| clarification policy | 0.380 | 0.1858 | 7.45 | 0.3167 | **−0.4473** |
| candidate depth 200 → 20 | 0.830 | 0.5689 | 3.67 | 0.7323 | −0.0318 |
| popularity priors | 0.860 | 0.5371 | 3.50 | 0.7410 | −0.0230 |
| phrase / bigram evidence | 0.870 | 0.5443 | 3.40 | 0.7502 | −0.0139 |
| coverage + category focus | 0.880 | 0.5417 | 3.30 | 0.7565 | −0.0076 |
| constraint scoring (Route C) | 0.885 | 0.5470 | 3.23 | 0.7620 | −0.0021 |
| dense route (Route B) | 0.890 | 0.5366 | 3.16 | 0.7628 | −0.0013 |
| per-field weighting | 0.895 | 0.5295 | 3.08 | 0.7648 | +0.0007 |
| profile personalisation | 0.885 | 0.5575 | 3.23 | 0.7650 | +0.0010 |
| *added:* MMR diversity | 0.885 | 0.5539 | 3.23 | 0.7641 | +0.0000 |

**A methodological note that changed the conclusions.** This table was first run
on a stratified 80-session subset to save time, and three of its readings were
wrong: phrase evidence looked neutral (−0.0003) when it is worth −0.0139,
coverage looked actively harmful (+0.0033) when it is worth −0.0076, and
per-field weighting looked clearly harmful (+0.0088) when it is within noise of
zero. At 80 sessions one session is 1.25 points of HR@10, so anything under
about ±0.02 was indistinguishable from sampling noise. Ablations are reported on
the full 200; the subset mode exists for iteration speed only, and its numbers
should not be quoted.

Findings worth stating plainly, including the unflattering ones.

**Clarification is the system.** Removing it costs −0.447 TechnicalScore — an
order of magnitude more than any other component. The reason is structural: a
browsing session opens with only a category and no constraints. If the agent
never asks, no new information ever arrives, the ranking cannot change, and the
remaining nine turns re-return the same wrong list. The first working version of
this agent scored 0.317 for exactly this reason: the clarification gate was
being handed the top-10 list instead of the candidate pool, so its "is the space
large enough to narrow?" test rejected every turn. Fixing that one plumbing bug
moved the score from 0.317 to 0.764.

**Asking well matters as much as asking.** Information gain over catalog fields
picks `category` and `brand`, which partition the candidate pool beautifully —
and which this customer answers *never*. `tools/measure_attribute_yield.py`
measures disclosure per attribute across all 200 sessions:

| attribute | answer rate | mean new text (chars) |
|---|---|---|
| feature | 0.915 | 50.9 |
| other | 0.950 | 48.3 |
| material | 0.725 | 28.3 |
| color | 0.245 | 15.0 |
| style | 0.090 | 12.7 |
| size | 0.045 | 7.5 |
| use_case | 0.015 | 2.0 |
| brand / budget / category | **0.000** | **0.0** |

So expected gain is computed as P(answered) × (uncertainty removed), with the
first term measured rather than assumed. A question the customer cannot answer
is a wasted turn out of ten, however well it would have split the catalog.

**Retrieval depth is the second-largest lever (−0.032).** Recall surrendered in
stage one cannot be recovered by any reranker, and the effect is visible in the
metric split: cutting depth to 20 *raises* MRR to 0.5689 while dropping HR@10 to
0.830. A shallow pool ranks the targets it contains slightly better and simply
does not contain the rest. HR@10 is 0.50 of the score and MRR 0.30, so that
trade is a clear loss.

**The popularity prior earns its place (−0.023).** Third-largest. Section 6
states what it does and does not mean.

**Two components do not earn their place.** Profile personalisation (+0.0010)
and per-field lexical weighting (+0.0007) both score marginally *better* when
removed. Both are within noise, so neither is a real finding in either
direction — the honest reading is that they are inert on this benchmark. They
are retained at low weight because the private set is four times larger and
these are the two components most likely to matter on phrasing this simulator
does not produce, but nothing in the public results argues for them. MMR is off
by default for the same reason, with the added evidence that it moves the score
by exactly zero.

---

## 3. Retrieval

**Per-field BM25, never concatenated.** A term matching in a 12-token `title`
means something different from the same term matching in 400 words of
`description`, and concatenation throws that distinction away. Each field also
enters the reranker as an independent feature.

**Field-routed queries.** The customer names a taxonomy node ("Jewelry
Necklaces") and separately quotes product copy ("Material:alloy"). These are
routed at different indexes — the category phrase at `categories` and `title`,
the quoted spans at `features` — rather than fired as one undifferentiated bag
of words at everything.

**Template scaffolding is stripped before tokenising.** This is not cosmetic. On
the opener *"I'm looking for Jewelry Necklaces. A key requirement is:
Material:alloy"*, bag-of-words retrieval scores the word "key" as a content term
and returns key-pendant necklaces. The target was absent from the top 200 before
the parser was added.

**Convex combination, not RRF.** Bruch et al. (arXiv 2210.11934) find RRF
sensitive to its parameters and poorly generalising out of domain, while CC wins
both in- and out-of-domain, is largely agnostic to the normalisation choice, and
is sample-efficient. With 200 sessions to tune on and 800 private ones to
generalise to, "one parameter, tunable on a small sample" decides it. Two routes
are fused, which keeps this inside the regime that paper studied.

**Candidate depth 200, not 20.** Recall lost in stage one cannot be recovered
downstream. Cutting depth to 20 costs −0.032 TechnicalScore, the second-largest
component effect measured.

---

## 4. Constraints are never filters

Coverage on the fields the problem statement assumes:

| field | coverage | use |
|---|---|---|
| `details.Department` | 87.2% → **98.3%** with title/category fallback | gender |
| `store` | 99.4% | brand |
| `categories` | 100% | soft boost |
| `price` | 21.1% | soft |
| `details.Color` / `Material` / `Size` | 4.9% / 4.1% / 1.9% | not filterable |

Every check returns SATISFIED / VIOLATED / **UNKNOWN**, and UNKNOWN is a mild
penalty rather than an exclusion. A hard filter on a field that is null four
times in five deletes the target four times in five. Material and colour resolve
only to SATISFIED or UNKNOWN — never VIOLATED — because the absence of a word
from sparse marketing copy is not evidence of conflict.

Gender is the one attribute with genuine elimination power, and title +
category-path fallback lifts its coverage from 87.2% to 98.3% of the catalog.
That fallback did **not** change the public score, and the reason is worth
recording: this simulated customer almost never states a gender, so the check
resolves to UNKNOWN either way. It is retained as robustness against a private
set whose phrasing may differ, not as a claimed gain.

---

## 5. Open decisions, resolved

**D1 — retrieval boundary.** The agent owns retrieval; the ranker reranks a pool
handed to it. This keeps the reranker a pure function of (candidates, context),
which is what makes offline replay possible at all, and it puts the popularity
prior in the reranker where it can be ablated rather than in candidate selection
where it would silently delete the ~5% of targets below the popular tail.

**D2 — recommend on ask-turns.** Yes. Nothing in the schema makes
`ask_attribute` and `recommendations` exclusive, first-hit turn drives MTTC, and
a silent turn is a discarded chance at the hit. 57 of 200 sessions convert on
turn 1.

**D3 — LLM reranker.** Not built. Listwise LLM rankers are order-sensitive,
which is a determinism problem for a graded submission, and official scoring may
run without network access. The `ScoringModel` protocol in `ranking.py` is the
seam if that changes.

---

## 6. Honest limitations

**The dense route is not neural.** `dense.py` is a character-n-gram TF-IDF
cosine index, not sentence embeddings. It buys tolerance to morphology and
spelling drift ("camisole"/"cami", "Skechers"/"skecher"); it does *not* buy
semantic generalisation, so "something elegant for a dinner date" will not reach
a listing that never says "elegant". This is a deliberate trade against the rule
that final scoring may disable network access. `EmbeddingRoute` is the documented
seam for real vectors. Its measured contribution is small (−0.0013), and
removing it actually raises HR@10 slightly while lowering MRR -- it is doing
very little work here.

**The popularity prior is correct for this benchmark and wrong for a store.**
Median target `rating_number` is 6,846 against 12 for a random catalog row. That
is a property of the label-generation pipeline — the benchmark samples real
purchases from a 5-core leave-last-out split, and real purchases concentrate on
popular items — not a discovery about shoppers. A deployed system ranking this
way would starve the long tail. It is used as a soft prior and never as a filter,
because ~5% of targets sit below the popular tail. Popularity alone scores
HR@10 3.5%: the gain comes from combining relevance with the prior, not from
either alone. For the same reason, popularity debiasing and calibrated
recommendation are deliberately *not* built — that literature exists to correct a
bias these labels contain by construction, and applying it here means fighting
the metric.

**The clarification prior is fitted to the observable customer policy.** The
attribute yields in §2 were measured against the public simulator. The
specification states the private set uses the same deterministic customer policy
and the same scenario mix, and that any added paraphrasing cannot decide
correctness — but if the private disclosure policy differs at the margins, the
prior is stale. A floor (`attribute_prior_floor`) keeps zero-yield attributes
reachable once everything else is exhausted.

**No LambdaRank.** The design called for GBDT if the data supported it. LightGBM
is not installable under the offline constraint, and at 200 sessions a
20-parameter linear model tuned by split-half CV is the defensible choice. The
feature vector, the trace hook (`config.trace_path`), and the `ScoringModel`
protocol are all in place for that upgrade; it was not taken on faith.

---

## 7. Model choice, cost, latency, tokens

| | |
|---|---|
| Model | **none** — no LLM, no embedding model, no external service |
| Network access required | **no** |
| Third-party dependencies | **none**; Python 3.10+ standard library only |
| Estimated API cost | **$0.00** |
| Reported token usage | 0 prompt / 0 completion (nothing to report) |
| Startup (catalog + indexes) | ~20 s, one-off per process |
| Per-turn latency | 89 ms median, 122 ms p95, 139 ms max (n=120) |
| Full 200-session evaluation | ~110 s |
| Determinism | exact; ties break on `parent_asin` |

Token usage is reported as zero because no model is invoked — not because
reporting was skipped.

---

## 8. Tuning protocol

Coordinate ascent over 20 parameters, one at a time, accepting only strict
improvements, with the official `TechnicalScore` as the objective — no proxy
metric. The 200 sessions are split in half, stratified by scenario and grouped
by session, since turns within a session are anything but independent. Fitting
happens on fold A only; fold B is scored once at the end and never optimised
against.

| | fold A (tuned on) | fold B (held out) |
|---|---|---|
| defaults | 0.7518 | 0.7763 |
| tuned | 0.7828 | **0.7869** |
| gain | +0.0310 | **+0.0106** |

The train gain is three times the holdout gain. That gap is the honest measure
of how much of the coordinate ascent was fitting this fold rather than the
problem, and it is why the holdout number is the one quoted for generalisation.

Eight of twenty parameters moved:

| parameter | default | tuned |
|---|---|---|
| `retrieval.w_description` | 0.45 | 0.80 |
| `retrieval.w_categories` | 0.90 | 0.70 |
| `ranking.w_coverage` | 0.30 | 0.60 |
| `ranking.w_phrase_categories` | 0.24 | 0.50 |
| `ranking.w_bm25_categories` | 0.22 | 0.11 |
| `ranking.w_category_focus` | 0.07 | 0.00 |
| `priors.w_has_price` | 0.04 | 0.09 |
| `priors.w_n_features` | 0.03 | 0.07 |

Two of these are worth reading as findings rather than numbers. `w_coverage`
doubling and `w_phrase_categories` doubling both say the same thing: how much of
what the customer said is *present* in a listing matters more than how a BM25
curve scores it. And `w_category_focus` going to zero while `w_phrase_categories`
doubles says the two were measuring the same signal, with the phrase version
strictly better.

Note also that the two folds differ by 0.025 under identical defaults (0.7518 vs
0.7763). That is the scale of fold-to-fold noise at 100 sessions, and it is
larger than most of the individual component effects in section 2 — another
reason those are reported on all 200.

The ablation table in section 2 is computed against the **shipped defaults**, so
each delta isolates one component rather than one component plus a tuning
interaction.

---

## 9. Reproduction

```bash
# Python 3.10+; no install step required.
python -m evaluator.local_evaluator          # official score -> results.json
python -m unittest discover -s tests         # 29 tests
python -m tools.demo --sample public_0002    # one full multi-turn transcript
python -m tools.ablate                       # regenerate the ablation table
python -m tools.measure_attribute_yield      # regenerate the disclosure table
python -m tools.tune                         # coordinate ascent, split-half CV
```

`tools/tune.py` writes `config/tuned.json`, which `starter/agent.py` loads
automatically if present (override with `SHOPPING_COPILOT_CONFIG`). Delete that
file to run on the shipped defaults.
