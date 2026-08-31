# Shopping Copilot — Conversational Search Agent

**TechJam 2026 · Track 4: AI Conversational Search and Recommendations**

A multi-turn shopping agent that finds a hidden target product in a 50,000-item
Amazon clothing catalog, asking clarifying questions only when they are worth
more than another retrieval call.

## Results

Measured on the 200 public development sessions using the official local
evaluator (`evaluator/local_evaluator.py`, unmodified), 2026-09-01.

| Configuration | HR@10 | MRR | MTTC | TechnicalScore |
|---|---|---|---|---|
| Official weak BM25 baseline | 0.125 | 0.068 | 9.81 | 0.1067 |
| Ours — default weights | 0.885 | 0.554 | 3.23 | 0.7641 |
| **Ours — current (`config/tuned.json`)** | **1.000** | **0.902464** | **2.400** | **0.942739** |

`TechnicalScore = 0.50 × HR@10 + 0.30 × MRR + 0.20 × Efficiency`, where
`Efficiency = clip((11 − MTTC) / 10, 0, 1)`.

An 8.8× improvement over the provided baseline, zero misses across all 200
public sessions, and mean turns-to-conversion of 2.40.

**One deliberate 0.0002 was spent on behaviour rather than score.**
`disclosed_ask_decay: 0.0` stops the agent asking about an attribute the
shopper has already told it — asking "do you have a material preference?" one
turn after they said "polyester" reads as an agent that is not listening. It
costs 0.942939 → 0.942739, entirely as one extra turn of MTTC on one session
(MRR is byte-identical), which is two orders of magnitude under this set's
~0.029 standard error. Taken knowingly; rollback is `disclosed_ask_decay: 1.0`.
The two-gate table immediately below predates this change and is quoted at
0.942939; the ablation table further down is measured on the shipped build.

**Read this as in-sample, and read the two gates that got it there separately —
they were built independently and neither one predicts the other's contribution:**

| config | full 200 | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| neither recommendation-hold gate | 0.909328 | 1.0000 | 0.756095 | 1.875 |
| `recommend_min_spans: 1` alone | 0.928002 | 1.0000 | 0.833673 | 2.105 |
| `min_recommend_confidence: 0.054` alone | 0.936614 | 1.0000 | 0.876048 | 2.310 |
| **both (before `disclosed_ask_decay`)** | **0.942939** | **1.0000** | **0.902464** | 2.390 |

Each gate was validated on a held-out fold independently (+0.0193 and +0.0259
respectively), but the *combination* has not itself been fold-split — treat
0.942939 as the honest full-set number, not a proven private-set estimate.
Single-run standard error on this 200-session set is ~0.029; the private
800-session set has roughly half that noise. Full reasoning and every
intermediate score in the chain that got here is in `CLAUDE.md`.

Per scenario, on the shipped config:

| scenario | n | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| buying | 80 | 1.0000 | 0.905625 | 1.825 |
| browsing | 80 | 1.0000 | 0.910952 | 2.400 |
| intent_override | 30 | 1.0000 | 0.880556 | 3.733 |
| boundary | 10 | 1.0000 | 0.875000 | 3.000 |

Rank distribution across the 200 sessions: **168 at rank 1**, 18 at rank 2,
12 at ranks 3–5, 2 at ranks 6–10, and no misses.

Browsing was the weakest scenario on every metric for most of this project and
is now the strongest on MRR. That is the recommendation-hold gates doing their
job: a browsing session opens with a category name and nothing else, so it was
precisely the case that used to lock in a poor rank on turn 1. The weak rows
are now `intent_override` and `boundary`, and both are structural —
override sessions cannot register a hit before the override arrives on turn
3–4.

---

## Project overview

Traditional keyword search fails conversational shoppers in two ways: it has no
memory across turns, and it cannot tell an open-ended browse from a high-intent
purchase. Our agent addresses both, plus a series of findings that came out of
measuring the dataset and the evaluator's own construction rather than from
reading the literature alone.

**Per-turn pipeline:** parse → route intent → retrieve (lexical + dense, fused,
plus a conjunctive exact-match injection) → rerank (linear model over a
constraint-aware feature vector) → decide whether to ask, hold, or recommend.

1. **Utterance parsing and state.** A `ShoppingState` accumulates slots across
   turns and handles override. Parsing is not bag-of-words, and that matters
   more than it sounds: on the opener *"I'm looking for Jewelry Necklaces. A key
   requirement is: Material:alloy"*, treating the raw string as a bag of words
   scores "key" as a content term and returns key-pendant necklaces — the target
   fell outside the top 200 entirely. Each turn is split into a category phrase,
   constraint spans, and control signals, and only content reaches the
   retriever.

2. **Retrieval.** Five per-field BM25 indexes — title, features, categories,
   description, store — never concatenated, because a term matching in a
   12-token title means something different from the same term in 400 words of
   description. Queries are field-routed: the category phrase goes at
   `categories` and `title`, the quoted constraint spans at `features`. A
   character-n-gram semantic route runs alongside, fused by convex combination.
   On top of that, a **conjunctive exact-substring injection** scans for any
   product whose text contains *every* live constraint span verbatim and pulls
   it into the pool regardless of BM25 rank — the route that raising
   `per_field_depth` alone (flat past ~800, measured on a full depth ×
   candidate-depth grid) cannot provide.

3. **Feature-based reranking.** A linear model over a per-candidate feature
   vector — per-field BM25, semantic similarity, phrase/bigram overlap, term
   coverage, catalog priors, `span_coverage` / `span_all` (does this candidate
   satisfy every disclosed constraint span, not just some of them), and
   per-dimension unknown-penalty indicators folded in as first-class features
   rather than a post-hoc adjustment — ordered deterministically with ties
   broken on `parent_asin`.

4. **Clarify, hold, or recommend.** An EAR-style gate asks only when the
   candidate space is still large enough to narrow and the customer has
   turns to spare. A separate, later-added gate governs *when to show a
   recommendation list at all*: because the evaluator scores the first turn a
   session's target appears in the top 10 and then stops, showing a weak list
   early can lock in a bad rank permanently. So the agent shows a list only
   when **both** of two independent gates pass: the customer has disclosed at
   least one concrete constraint span, *and* the ranker's own confidence
   (Normalized Query Commitment, Shtok et al. 2009) has crossed a measured
   threshold. They read different signals and either can hold a turn the other
   would release, which is why both ship rather than one superseding the other.
   Each carries its own turn cap — 4 and 3 respectively — past which it stops
   holding, so a customer who never discloses anything is not met with silence
   forever. An uncapped hold would turn a hit into a miss, spending the
   0.5-weighted term to save the 0.2-weighted one.

### What we found in the data

**Clarification is the system, not a refinement to it.** Removing it costs
−0.3892 TechnicalScore on the shipped build — 37 points of HR@10, and more than
five times any other component. The reason is structural: a browsing session opens with a category and no
constraints, so if the agent never asks, no information arrives, the ranking
cannot change, and the remaining nine turns re-return the same wrong list. Our
first working build scored 0.3167 because the clarification gate was handed the
ten returned recommendations instead of the ranked candidate pool — its "is the
space still large enough to narrow?" test therefore rejected every turn.

**Asking well matters as much as asking.** Information gain computed over
catalog fields picks `category` and `brand`: they partition the candidate pool
beautifully, and this customer answers them *never*. Measured across all 200
sessions (`python -m tools.measure_attribute_yield`):

| attribute | answer rate | mean new text disclosed |
|---|---|---|
| feature | 0.915 | 50.9 chars |
| other | 0.950 | 48.3 |
| material | 0.725 | 28.3 |
| color | 0.245 | 15.0 |
| style | 0.090 | 12.7 |
| size | 0.045 | 7.5 |
| use_case | 0.015 | 2.0 |
| **brand / budget / category** | **0.000** | **0.0** |

So expected gain is `P(answered) × uncertainty removed`, with the first term
measured rather than assumed. A question the customer cannot answer wastes one
of ten turns however well it would have split the catalog.

**The evaluator breaks on first hit, and that fact drives the single biggest
gain in the project.** `local_evaluator.py` stops scoring a session the first
turn its target appears in the top 10 — so whatever rank the agent shows on
that turn is final, even if the very next turn would have done better. Measured
directly: **45 of 75 sub-rank-1 sessions locked in on turn 1**, before the
customer had disclosed anything beyond a category name, and every one of them
reached rank 1 by turn 2 or 3 when replayed past their lock-in turn. The
exchange rate strongly favours waiting: one extra turn of delay costs ~0.0001
of score via efficiency; a rank 2→1 recovery is worth ~0.00075 — about 7.5×
more. This is the mechanism behind both recommendation-hold gates above, and
it is a property of *how the metric is defined*, not of real shopper patience
— see the honesty note under "Grounding in prior work".

**Targets are a popularity-biased subpopulation.** Median `rating_number` is
6,846 for target products versus 12 for a random catalog row — AUC 0.956 on that
single feature. This is a property of how the benchmark was built: targets are
sampled from a 5-core leave-last-out split of real purchases, and purchases
concentrate on popular items.

We use it as a **soft prior, never a filter.** Pruning to `rating_number ≥ 25`
would retain 97.5% of targets while cutting the catalog to 37.9% — tempting, but
5% of targets sit below the popular tail (one has a single review) and a
discarded target is an unrecoverable HR@10 loss.

We want to be explicit that **this is correct for this benchmark and wrong for a
production store.** A deployed system ranking this way would starve the long
tail. It is a property of the label distribution, not of shopper psychology.
Ranking by popularity alone scores just 3.5% HR@10 — worse than the BM25
baseline — because it is query-blind, returning the same Crocs and Hanes boxer
briefs to every customer. The gain comes from combining relevance with the
prior; neither half works alone.

**The catalog is sparse in exactly the fields the problem statement assumes.**
Price is null on 78.9% of rows. `details.Color` exists on 4.9% of products,
`Material` on 4.1%, `Size` on 1.9%. Structured attribute filtering is largely
unavailable, so every constraint is emitted three-way — satisfied / violated /
**unknown** — with `unknown` a mild penalty rather than an exclusion, folded
directly into the linear model's weights rather than applied afterward.
Anything that deletes candidates on a missing field deletes the target.
Material and colour resolve only to satisfied or unknown, never violated:
absence of a word from sparse copy is not evidence of conflict.

**One structured attribute does survive.** `details.Department` covers 87.2% of
the catalog and encodes gender (50.9% womens, 21.2% mens, plus unisex and
children's splits). Gender is the highest-elimination-power attribute in
clothing. For the missing 13% we fall back to title tokens and then the category
path, which lifts effective coverage to 98.3%. A catalog-wide own-goal audit
(evaluate every one of the 50,000 rows against constraints drawn from its own
listing) later found the `kids` gender bucket had been coded as a *sibling* of
`boys`/`girls` rather than their parent — 1.01% of the catalog was scoring its
own listing as violating its own gender constraint. Fixed as a hierarchy
(`kids` satisfied-by boys/girls, never violated-by), catalog-wide own-goal rate
506/50,000 → 180/50,000; zero measurable effect on the public 200 (expected —
the defect wasn't reachable from those 200 openers), adopted anyway because
the private 800 will reach it.

**Single-word brand extraction was 94% false positives, and the fix generalises
instead of hardcoding a blocklist.** A `BrandVocabulary` built from 19,855
catalog store names matches ordinary listing boilerplate — "Machine Wash",
"Rubber sole" — as if it were a customer-stated brand. Measured live at the
lock-in turn across all 200 sessions: a brand was extracted in 66 sessions and
62 were wrong. Rather than hand-writing a blocklist tuned to what these 200
sessions happen to say (which would not generalise to the private 800), we gate
single-word brand matches by their measured catalog-wide text-commonness — real
brands and boilerplate separate two orders of magnitude (`skechers` 0.0077 vs.
`wash` 0.317). Also zero measured public-set effect (the false-positive rate
applies a uniform penalty across almost the whole pool, and a uniform penalty
can't reorder anything), adopted for the same private-set reason.

**Rank-2 was read by hand, product by product, and it closes off an entire
class of future work.** All 30 rank-2 sessions on the public set were read
against exactly what the customer had said at the turn that scored: in **0 of
30** did the ranker have separating information and still get the order wrong.
27 were separable only with more disclosure than the customer had yet given
(a timing problem, which the recommendation-hold gates above address); 3 were
genuine ties — two listings identical on every disclosed constraint, where no
feature, no weighting, and no human reading the text could tell them apart.
This retired, on evidence, both the theory that popularity signal was drowning
constraint evidence (winner-is-more-popular was a coin flip, 15/30) and seven
separate attempts at a learned reranker (five linear formulations, sklearn
logistic regression, LightGBM LambdaMART) — none beat the incumbent held out,
because the vector already contains what a rank-2 pair needs; there was
nothing left to reweight.

### Component ablations

**How to read this.** Each row switches off exactly one component and re-runs
all 200 sessions against the *shipped* configuration. `delta` is what that
component is worth: a large negative number means removing it hurts, so it is
carrying real load; a positive number means the system scored slightly *better*
without it. Regenerate with
`python -m tools.ablate --config config/tuned.json`.

**Read the magnitudes with the noise floor in mind.** A single run on 200
sessions cannot resolve small differences — one session is 0.5 points of HR@10.
Treat anything inside roughly ±0.01 as "no measurable effect" rather than as a
ranking of the minor components, and note that each row here is one run, not a
fold-validated result.

| component removed | HR@10 | MRR | MTTC | TechnicalScore | delta |
|---|---|---|---|---|---|
| *full system* | 1.000 | 0.9025 | 2.40 | **0.9427** | — |
| clarification policy | 0.630 | 0.4598 | 5.97 | 0.5535 | **−0.3892** |
| popularity priors | 0.960 | 0.7556 | 2.92 | 0.8683 | **−0.0744** |
| candidate depth 200 → 20 | 0.955 | 0.8648 | 2.83 | 0.9003 | **−0.0424** |
| recommendation hold (both gates) | 1.000 | 0.7561 | 1.89 | 0.9091 | **−0.0336** |
| span conjunction (`span_all`) | 0.990 | 0.8522 | 2.56 | 0.9194 | **−0.0234** |
| per-field weighting | 0.990 | 0.8789 | 2.44 | 0.9300 | −0.0128 |
| phrase / bigram evidence | 0.995 | 0.8841 | 2.46 | 0.9335 | −0.0092 |
| constraint scoring (Route C) | 0.995 | 0.8928 | 2.43 | 0.9368 | −0.0060 |
| coverage + category focus | 1.000 | 0.8780 | 2.29 | 0.9375 | −0.0052 |
| dense route (Route B) | 0.995 | 0.9027 | 2.46 | 0.9391 | −0.0036 |
| low-coverage penalties | 1.000 | 0.8990 | 2.40 | 0.9417 | −0.0010 |
| *added:* MMR diversity | 1.000 | 0.9025 | 2.40 | 0.9427 | +0.0000 |
| *added:* re-ask disclosed attributes | 1.000 | 0.9025 | 2.39 | 0.9429 | +0.0002 |
| constraint commonness penalty | 1.000 | 0.9058 | 2.42 | 0.9434 | +0.0007 |
| profile personalisation | 1.000 | 0.9072 | 2.42 | 0.9438 | +0.0010 |

**Clarification is still the system.** Removing it costs 37 points of HR@10 and
more than five times any other component. The mechanism is structural: a
browsing session opens with a category and nothing else, so if the agent never
asks, no information ever arrives, the ranking cannot change, and the remaining
nine turns re-return the same wrong list.

**The recommendation hold lands at 0.9091, which is a useful check on the whole
table** — that reproduces 0.909328, the pre-gate score measured independently
by a different route months of work earlier. Note the shape of the trade: MRR
collapses 0.9025 → 0.7561 while MTTC *improves* to 1.89. Answering sooner is
genuinely faster and genuinely worse, which is exactly the exchange the gates
exist to make.

**Two results contradict what earlier versions of this document claimed, and
the earlier claims were right for the build they were measured on:**

- **Per-field lexical weighting was listed as not earning its place** (+0.0007
  under the pre-tuning defaults). On the shipped build it is **−0.0128** — one
  of the larger contributors. The earlier reading was taken before the tuner
  differentiated the per-field weights at all.
- **Popularity priors roughly tripled**, −0.0230 → **−0.0744**, and are now the
  second-largest component. That is the bill for tuning
  `w_log_rating_number` from 0.15 to 0.88. It deserves saying plainly, because
  this is the component we are least comfortable defending (see the caveat
  above): the part of the system that exploits a benchmark artifact rather than
  modelling shoppers has become *more* load-bearing over time, not less.

**Three components still do not earn their place**, and we would rather report
that than imply otherwise: profile personalisation (+0.0010, the same figure it
scored on the pre-tuning build — two independent measurements 0.18 points
apart agreeing), MMR diversity (+0.0000 for the third time, which is why it
ships disabled), and the constraint commonness penalty (+0.0007). The last of
those is a supersession rather than a mistake: it was adopted with fold
evidence to stop boilerplate query terms diluting retrieval, and the
conjunctive injection and `span_all` added later address the same failure more
directly, leaving it with nothing to do.

**One component has no row, deliberately.** The conjunctive exact-substring
injection is unconditional in `agent.py` with no enable flag, so it cannot be
switched off from config; measuring it would require reverting code. Its
contribution was measured at adoption time instead: HR@10 0.995 → 1.000, and
`target_never_in_pool` 1 → 0.

*The pre-tuning ablation table this section used to carry, measured against the
0.7641 default-weights build, is preserved at `docs/ablations.md`. It is not
comparable row-for-row with the table above — a stronger baseline leaves less
room to fall, which is why clarification reads −0.3892 here against −0.4473
there.*

### Grounding in prior work

- **Fusion by convex combination, not RRF.** Bruch et al. (arXiv 2210.11934)
  find RRF sensitive to its parameters and poorly generalising out-of-domain,
  while convex combination outperforms it both in- and out-of-domain, is largely
  agnostic to score normalisation, and is sample-efficient — needing only a
  small training set to tune its single parameter. With 200 sessions, that last
  property decided it. We fuse two routes, which keeps us inside the regime that
  paper studied; it explicitly leaves three-or-more to future work.
- **Clarification gate from EAR** (Lei et al., WSDM 2020, arXiv 2002.09102):
  ask only when the candidate space is small enough, further questions still
  carry information gain against user patience, and the recommender is not yet
  confident. We deliberately did *not* implement the RL policies that follow it
  (SCPR, arXiv 2007.00194; UNICORN, arXiv 2105.09710) because they assume clean
  per-item attribute sets, which this catalog does not have.
- **Confidence, quantified: Normalized Query Commitment** (Shtok et al., 2009).
  The same statistic EAR treats qualitatively — "is the recommender confident"
  — is computed explicitly here as `std(top-10 scores) / |top-N|` and used both
  to gate asking (`ask_max_confidence`) and, later, to gate showing a
  recommendation list at all. One caution recorded from building this: a gate
  set against the wrong empirical range is silently unreachable rather than
  loudly wrong — `ask_max_confidence = 0.82` sat above the entire observed NQC
  range [0.011, 0.194] for a long stretch before anyone measured that range and
  noticed the gate had never once fired.
- **Should the system withhold results while it asks? The literature disagrees
  with itself, and we built the gate anyway because the effect is measurable
  per-session, not just population-average.** [An empirical study of
  clarifying-question e-commerce systems](https://arxiv.org/pdf/2008.00279)
  found users tolerate answering 11.4 questions per product on average — turn
  budget is not the binding constraint. But [three controlled experiments on
  withheld information in conversational interfaces](https://pmc.ncbi.nlm.nih.gov/articles/PMC11008880/)
  (n = 1,811 / 905 / 801) found conversational withholding scores *worse* than
  the same information withheld in an ordinary UI — lower willingness to use,
  better recall that something was withheld, and inferred motive — mitigated
  specifically by showing results *alongside* the question, which a hold gate
  by definition stops doing. Net judgment: a confidence gate is more defensible
  than a fixed turn threshold because it makes a checkable per-session claim
  ("the ranking has separated") rather than a population average masquerading
  as a decision — but the withholding itself remains the weaker half of the
  case, which is part of why the shipped threshold sits at the conservative
  edge of its measured plateau rather than at its highest-scoring point. Full
  writeup: `docs/PRD_confidence_gated_recommend.md`.
- **Tree ensembles for tabular ranking.** Grinsztajn et al. (arXiv 2207.08815)
  find GBDTs frequently outperform deep models on tabular data at this scale,
  and that feature engineering rather than model class sets the ceiling — which
  is why we treated the shopping-specific features as the contribution. We did
  test a LightGBM `lambdarank` reranker directly against seven other learned
  approaches (see Limitations); none beat the tuned linear model held out, which
  is consistent with the paper's claim that the ceiling is set by features, not
  model class, once the features themselves stop changing.
- **Anchor-based CRS framing.** PSCon (arXiv 2502.13881) observes that
  e-commerce conversational recommendation work is typically anchor-based —
  conversations simulated from predefined intent slots and attributes — which is
  exactly the structure of the TechJam simulator, and why slot-based routing is
  the right abstraction here rather than free-form NLU.

---

## Setup and installation

Requires Python 3.10 or later. **The agent uses only the Python standard
library — there is no installation step.**

```bash
git clone https://github.com/xjinhx/winfluencers-techjam
cd techjam-conversational-search
```

`requirements.txt` is intentionally empty of packages. Submission rules warn
that organizer policy may disable network access for final scoring, so the
system has no third-party dependency, no model download, and no service call on
the turn path. (`frontend/` is a separate, optional presentation demo — a
FastAPI wrapper plus a React chat UI — that calls the same unmodified
`starter.agent.Agent`; it has no effect on `TechnicalScore` and is not part of
the graded submission. See `frontend/README.md`.)

### Catalog

The 50,000-product catalog is not committed. Download `catalog.jsonl.gz` from
the participant kit release and place it in `data/`:

```bash
gzip -dk catalog.jsonl.gz
mv catalog.jsonl data/catalog.jsonl
```

Verify against the published `SHA256SUMS` before running.

---

## Steps to reproduce our results

```bash
python -m evaluator.local_evaluator
```

Runs all 200 public sessions and writes per-session results and aggregate
metrics to `results.json`. Expected output:

```
HR@10  1.000
MRR    0.902464
MTTC   2.400
TechnicalScore  0.942739
```

Tuned weights live in `config/tuned.json` and are loaded automatically by
`starter/agent.py`. **To reproduce the untuned 0.7641 row, delete or rename that
file** (or point `SHOPPING_COPILOT_CONFIG` at a different config). The evaluator
and public labels are unmodified from the participant kit.

Other entry points:

```bash
python -m unittest discover -s tests          # 60 unit tests
python -m tools.demo --sample public_0002      # one full multi-turn transcript
python -m tools.ablate                         # regenerate the ablation table
python -m tools.measure_attribute_yield        # regenerate the disclosure table
python -m tools.tune --output <scratch>.json --report <scratch>.json  # coordinate ascent, split-half CV
python -m tools.offline_eval --trace <trace>.jsonl --against <results>.json  # replay/validate a trace session-by-session
python -m tools.why_lost --trace <trace>.jsonl --ranks 2,3,4,5 --top 30       # rank diagnostic: which feature costs a rank
python -m tools.separability                   # linear-separability gate for a candidate reranker before building one
```

**Never let `tools.tune` or the evaluator write to `config/tuned.json` or
`results.json` while experimenting** — both default to the live submission
paths. Always redirect `--output`/`--report`, or set `SHOPPING_COPILOT_CONFIG`
to a scratch file. See `CLAUDE.md` Critical rules 1–2 for the full reasoning.

### Repository layout

```
starter/agent.py               Agent contract: reset() / respond(); loads config
shopping_copilot/
  text.py                      Shared tokeniser (index and query time)
  config.py                    Every tunable parameter, JSON round-trippable
  catalog.py                   Catalog load, normalisation, coverage flags, priors
  index.py                     Per-field BM25 inverted indexes
  dense.py                     Character-n-gram semantic route
  fusion.py                    Convex combination -> `fused`
  structured.py                Gender/brand/category slots, three-way checks
  profile.py                   Anonymised-profile personalisation
  state.py                     Utterance parsing, slots, override rewrite, active spans
  intent.py                    BUYING / BROWSING / UNCERTAIN router
  features.py                  Feature vector (span_all/span_coverage, unknown-penalty, etc.)
  ranking.py                   Linear scorer, MMR diversity (disabled by default)
  clarify.py                   EAR-style ask gate + NQC confidence
  agent.py                     Orchestration: retrieval, injection, rerank, recommend-hold gate, trace emission
  baselines/weak_bm25.py       The original starter, kept for comparison
tools/                         tune, ablate, demo, offline_eval, why_lost, separability,
                                measure_attribute_yield, measure_span_selectivity, read_pairs,
                                stem_audit, train_pairwise, evalkit
tests/                         60 unit tests (test_shopping_copilot.py, test_evaluator.py)
config/tuned.json              Tuned weights (loaded by default) — the live submission config
docs/                          report.md, PRDs, ablations, rank-2/rank-diagnostic reads,
                                competition spec, submission rules, tuning report
evaluator/                     Official local evaluator (unmodified)
data/                          catalog.jsonl (downloaded), public_set.jsonl
frontend/                      Optional demo: FastAPI wrapper (api/) + React chat UI (web/),
                                zero effect on TechnicalScore, not part of the submission
CLAUDE.md                      Living operational log: every measured score, every finding,
                                every decision and its reasoning, in date order
```

---

## Limitations and what we would improve

Planned work, with the measurement behind each item, is in
[`docs/pending.md`](docs/pending.md) (written at an earlier, lower score — its
numbers are stale, its priority ordering mostly still holds) and in the
"Roadmap" section of `CLAUDE.md`, which is the current authoritative list.

**The disclosure-timing lever is close to spent.** The single largest lever in
this project turned out to be *when* the agent shows a recommendation, not what
it shows — see "What we found in the data" above. A span-rescoring ceiling
(rescoring each session's real hit-turn pool as if the full intent card had
already been disclosed) puts the honest ceiling on this lever at MRR ≈ 0.9025;
the live figure is 0.9025's near-neighbour once MTTC is priced back in.
Further MRR gains from here have to come from ranking the disclosed evidence
better, not from waiting longer for more of it.

**Seven learned-reranker attempts, all rejected on held-out evidence.** Five
hand-rolled linear pairwise formulations, a regularised sklearn logistic
regression (`C` swept 1e-3…1e2), and LightGBM `LambdaMART` (8 hyperparameter
configurations) were all tested against the rank-2 bucket specifically. Every
linear attempt regressed the held-out fold by −0.0430 to −0.0716; LightGBM was
the best of the seven at −0.0080 (a tie with the incumbent, not a win). The
rank-2 text-read explains why: **0 of 30 rank-2 sessions have separating
information available to the ranker at the turn that scores** — 27 need more
disclosure (a timing problem, already addressed) and 3 are genuine ties in the
underlying listing text. There is no signal left in the current feature vector
for a reranker to exploit; reopening this is only worthwhile if a new feature
changes what's in the vector.

**The intent router does not currently affect retrieval weighting.** It is
implemented and wired — scoring every turn on constraint density, linguistic
markers, slot specificity, and profile alignment — and does drive the
per-intent `w_fused` weights (buying/uncertain get `w_fused = 0.0`, adopted and
live). But it does not yet select a genuinely different retrieval profile
beyond that single weight, and MMR diversity (its other planned consumer)
measured at exactly +0.0000 in ablation and ships disabled.

**The semantic route is not neural.** `dense.py` is a character-n-gram TF-IDF
index, not sentence embeddings — a deliberate trade against the rule that final
scoring may disable network access. It buys tolerance to morphology and spelling
drift ("camisole"/"cami", "Skechers"/"skecher"); it does *not* buy semantic
generalisation, so "something elegant for a dinner date" will not reach a
listing that never says "elegant". A documented `EmbeddingRoute` seam exists
for real vectors, un-built for the same offline-network reason.

**No GBDT in production, on evidence rather than by default.** LightGBM was
actually built and measured (see above) rather than assumed unhelpful — it
ties the incumbent at best. The feature vector, a trace hook
(`config.trace_path`) that logs replayable feature rows, and a `ScoringModel`
protocol are all in place if a future feature addition changes that answer.

**The popularity prior is benchmark-specific.** Our strongest prior feature
exploits how the evaluation labels were generated — targets are a
popularity-biased subsample of real purchases. It transfers to the private 800
sessions because they come from the same construction pipeline, but it would
not transfer to a live store, where it would starve the long tail. We consider
naming this more useful than hiding it.

**Tuned and selected against the same 200 sessions many times over.** Dozens
of configurations have now been scored against this set across the project's
history. The mitigating practice, applied consistently: every adopted change
is checked against `stratified_halves(seed=7)` and the conservative
(fold-B) half is what gets quoted, not the fitted half — see `CLAUDE.md`'s
"Measurement discipline" section for the full protocol and every individual
fold result.

**MTTC has a structural floor we cannot cross.** Intent-override sessions
cannot score before the changed intent appears on turn 3 or 4 (drawn per
sample by the evaluator's own RNG, not fixed). With those at 15% of traffic,
perfect play still carries a floor above zero-turn conversion.

**Attribute vocabulary gap, unaddressed.** The nine `preference_tags` in the
user profile are abstract (fit, material, comfort, style, durability,
performance, warmth, weather, general shopping) and do not map onto any
catalog field. Our profile affinity feature ablates to +0.0010 — inert.
Learning tag-to-product affinities from the labelled sessions remains the most
promising unexplored direction that has not yet been tried.

---

## Team contributions

- **He Jinhong** — conjunctive candidate injection, gender-hierarchy and
  brand-false-positive structural fixes, rank-2 text-read and ceiling analysis,
  learned-reranker sweep (sklearn + LightGBM), project direction and PRD review
- **Arwen Tan** — evidence-gated recommendation withholding
  (`recommend_min_spans`), span-selectivity and injection-gate sweeps
- **Dylan Huang** — `per_field_depth` recall fix, `constraint_commonness_penalty`,
  pairwise-LTR experiment, dialogue-config tuning, rank-2 diagnostic tooling
  (`tools/why_lost`, `tools/separability`)
- **Joey** — title/coverage interaction weights, NQC-based confidence formula
  (`clarify.py`)

## Data attribution

Catalog and sessions derive from the Amazon Reviews 2023 dataset (McAuley Lab,
UCSD), provided frozen by the competition organizers. See `DATA_ATTRIBUTION.md`.

## Model disclosure

**This agent uses no external model API and requires no network access.** All
retrieval and ranking are deterministic and run in-memory.

| | |
|---|---|
| Model | none |
| Estimated cost per 200-session run | $0.00 |
| Token usage | 0 prompt / 0 completion (the evaluator's reported total is zero) |
| Determinism | exact; ties break on `parent_asin` |
