# Shopping Copilot — Agent Architecture

**Status:** as-built. This describes the system that exists. The code is the
authority; where this doc and the code disagree, the code is right and this doc
is stale.

**Describes `origin/main` @ `d2f12ac`** — the trunk and the submission state.
Feature branches are sometimes behind it. If `FEATURE_NAMES` is 38 rather than
40 in your checkout, you are missing `ab4e55c` and the two coverage-interaction
columns in §4a; merge `main` before treating that section as wrong.

**Scoring target:** TechnicalScore = 0.50·HR@10 + 0.30·MRR + 0.20·Efficiency

| | HR@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---|---|---|---|---|
| Official BM25 baseline | 0.125 | 0.068 | 9.81 | 0.119 | 0.1067 |
| **This system** | **1.000** | **0.902464** | **2.390** | **0.8610** | **0.942939** |
| *superseded 2026-08-31* | *0.990* | *0.747014* | *1.955* | *0.9045* | *0.900004* |

Measured on all 200 public dev sessions, `arwen`+`investigation` merge,
committed `config/tuned.json`, unmodified official evaluator. **No session
misses**; 168 of 200 convert at rank 1. 56/56 tests. The superseded row is the
`d2f12ac` measurement this document originally carried, kept for the chain.

*Prior row's caveats, no longer current: "Two sessions miss. 38/38"* —
unit tests pass. `CLAUDE.md` "Current state" is the number of record — if it and
this table disagree, it is newer.

Section numbers are cited from `shopping_copilot/config.py` (`architecture doc
S3`, `S4b`, `S6`, `S7`, `S8`, `P2`, `D2`). **Do not renumber them.**

**Two sources of weights, and they differ on purpose.** Dataclass defaults in
`config.py` are pre-adoption values, chosen so a new feature is inert until
switched on and prior behaviour stays exactly recoverable
(`constraint_commonness_penalty=0.0`, `w_span_all=0.0`, `per_field_depth=220`).
The live submission weights are `config/tuned.json`. Numbers quoted in this doc
are the tuned ones; they move, so read the config for truth.

---

## 0. Design principles

**P1 — Dialogue creates the score; ranking counts it.** HR@10 and MRR are both
determined entirely by `ordered_recommendations`, so it is tempting to conclude
that ranking quality is the only bottleneck. It is not. Ablating the
clarification policy costs **−0.4473 TechnicalScore**, an order of magnitude more
than any other component (§8).

The mechanism is structural, not incidental: a browsing session opens with a
category and no constraints. If the agent never asks, no new information
arrives, so the ranking cannot change, so the remaining nine turns re-return the
same list. Retrieval and reranking can only order the evidence they are given.
The clarification policy is what produces the evidence.

**P2 — The catalog is sparse in exactly the fields the problem statement
assumes.** Price is null on 78.9% of rows; `details.Color` exists on 4.9%,
`Size` on 1.9%, `Material` on 4.1%. Structured attribute filtering is mostly
unavailable, and **anything that deletes candidates on a missing field deletes
the target.** This is why every constraint is scored three ways with `unknown`
as a mild penalty rather than an exclusion (§4a), and why the RL clarification
literature does not transfer (§5).

**P3 — Targets are a popularity-biased subpopulation, by construction.** The
benchmark samples real purchases from a 5-core leave-last-out split, so targets
concentrate on heavily-reviewed products. Median target `rating_number` is 6,846
against ~12 for a random catalog row (AUC ≈0.955). This is a property of the
label-generation pipeline, not of shopper psychology — see §8 for how to frame
it, and P2 for why it is a prior and never a filter.

**P4 — Determinism is a hard requirement.** No LLM call anywhere on the turn
path, no network, no third-party dependency, stdlib only. Submission rules warn
that organizer policy may disable network access for official scoring, and a
graded submission has to return the same list twice. `usage` is reported as zero
because no model is invoked. This principle closes D3 (§9).

---

## 1. Intent Router — `intent.py`

**Does:** classifies each turn as BUYING / BROWSING / UNCERTAIN.

**Grounding:** PSCon (arXiv 2502.13881) notes existing e-commerce CRS work is
usually anchor-based — conversations simulated from predefined intent slots,
entities, and attributes. That is exactly what the TechJam simulator does, which
makes slot-based routing the right abstraction rather than free-form NLU.

**Four bounded features, one linear decision, no model call:**

```
score = 0.40·constraint_density + 0.25·marker_score
      + 0.25·slot_specificity  + 0.10·profile_alignment

score ≥ 0.65 → BUYING    score ≤ 0.35 → BROWSING    else UNCERTAIN
```

`constraint_density` counts what the customer has committed to; `marker_score`
is a signed contrast of browsing against buying phrase markers; `slot_specificity`
counts structured slots actually resolved, not merely words said;
`profile_alignment` reads purchase history and preference-tag count.

**The label selects a ranking weight profile, not a retrieval one.** Retrieval
is identical across intents. `Ranker` builds one `LinearModel` per intent, and
only the features in `ranking.INTENT_OVERRIDABLE` — currently `fused`,
`bm25_title`, `profile_affinity`, `category_focus` — may differ, via
`w_{feature}_{intent}` config fields that fall back to the shared default when
unset (§4b).

Per P4, this must not become an LLM call. It runs every turn and must be
deterministic.

---

## 2. State Manager — `state.py`

**Does:** maintains `ShoppingState` across turns; accumulates constraint spans,
handles override, and builds the per-field weighted query.

**Grounding:** multi-round conversational recommendation as formalised in SCPR
(arXiv 2007.00194) and UNICORN (arXiv 2105.09710) — the system alternates
between asking about attributes and recommending, updating candidate and
attribute sets after each user response. That state machine transfers; the
entropy-over-attributes machinery those papers build on top of it does not, per
P2. See §5.

**Parsing is deliberately not bag-of-words.** Each turn splits into:

- a **category phrase** — the taxonomy node the customer named;
- **constraint spans** — quoted product copy, kept as whole strings;
- **control signals** — "ask me about one specific attribute", refusals.

Only content reaches the retriever. On the opener *"I'm looking for Jewelry
Necklaces. A key requirement is: Material:alloy"*, a bag-of-words query scores
`key` as a content term and returns key-pendant necklaces, putting the target
outside the top 200 entirely.

**Override is decay, not erasure.** A superseded span keeps its text in the
query at reduced weight (`dialogue.override_decay`, 0.25) — the customer changed
their mind, but what they originally said is still weak evidence about what they
are shopping for. Later disclosures outweigh earlier ones by
`dialogue.recency_bonus` (0.15).

**Three views of accumulated state, and the difference between them is the
point:**

| method | returns | why it exists |
|---|---|---|
| `active_terms()` | token set | feeds BM25 and `coverage` |
| `active_bigrams()` | ordered bigram set | phrase evidence — separates the target from unigram-similar neighbours |
| `active_spans()` | **whole strings, untokenised** | the conjunctive signal (§4a). `"95% Polyester, 5% Spandex"` matches 5.3% of a category where `polyester` alone matches 41.2% |

`query()` also applies the commonness damping described in §3.

---

## 3. Retrieval — `index.py`, `dense.py`, `fusion.py`, driven by `agent.py`

**Two routes, fused.** Structured constraints are *not* a retrieval route — they
are reranker features (§4a). This keeps the system inside the regime the fusion
evidence covers: Bruch et al. studied fusing **two** retrievers and explicitly
leave three-or-more to future work.

### Route A — lexical, five per-field BM25 indexes

`title`, `features`, `categories`, `description`, `store`, over a hand-rolled
inverted index. Postings are interleaved `(doc_id, tf)` pairs in a single
`array('i')` per term, which holds 50k documents in tens of MB instead of the
hundreds a dict-of-dicts would cost.

**Fields are never concatenated.** A term matching in a 12-token title means
something different from the same term inside 400 words of description, and
keeping them apart is what lets the reranker weight each independently. Each
field carries its own length-normalisation `b` — 0.20 on `store` up to 0.75 on
`features` and `description`. `b_title` is 0.45 because titles are short and
near-uniform in length, so length normalisation mostly adds noise there.

```
score_f(d,q) = Σ_{t∈q} w_t · IDF(t) · tf·(k₁+1) / (tf + k₁·(1 − b_f + b_f·|d|/avgdl_f))
IDF(t)       = ln(1 + (N − df + 0.5)/(df + 0.5))     # Robertson/Spärck Jones, +1 keeps it non-negative
```

**Queries are field-routed.** The category phrase goes against `categories` and
`title` — it is the customer naming a taxonomy node, so it does not belong
against 400 words of marketing copy. Constraint spans are quoted product copy,
so they go against `features` and `title`.

**Terms above `max_df_ratio` (0.35) are dropped** from a field's query. Their IDF
is near zero and their posting lists are the most expensive to scan.

### Commonness damping on constraint terms

The customer quotes the target's own copy verbatim, and some of that copy is
near-universal boilerplate (*"Manmade sole; Platform measures approximately
0.5″"*). At full query weight those terms pull in thousands of competitors.

The fix is a continuous ramp on **measured** document frequency, deliberately
not a phrase list — a hardcoded list would fit phrases visible in the 200 public
sessions and would not generalise to the private 800.

```
w′_t = w_t · (1 − λ · min(1, df_ratio(t) / τ))

λ = constraint_commonness_penalty (0.30 tuned, 0.0 default)
τ = max_df_ratio (0.35)
df_ratio = max fraction of the catalog containing t, across title/features/categories

a rare term keeps full weight; a term in ≥35% of the catalog floors at 1 − λ
```

`LexicalIndex.commonness()` reuses the already-built per-field postings, so this
costs no new indexing. It is applied **inside the constraint-span loop only** —
the category phrase is never damped, because it must keep full routing weight
however common its words are.

### Route B — semantic, character n-grams

Per P4 there is no model download, so Route B is a character-n-gram similarity
index (`dense_ngram=4`) over `title + categories`.

Be precise about what this is: **not** a neural dense retriever. What it buys is
tolerance to morphology and spelling drift — *camisole*/*cami*, *sterling
silver*/*silver sterling*, *Skechers*/*skecher*. What it does not buy is semantic
generalisation; *"something elegant for a dinner date"* will not reach a listing
that never says "elegant". `EmbeddingRoute` is a live seam for real embeddings:
precompute vectors offline, point the config at them, and fusion downstream is
unchanged.

The route reads the **surface text**, not the term list — character n-grams need
the original spelling to be worth anything.

### Fusion — convex combination, not RRF

Bruch et al. (arXiv 2210.11934) find RRF sensitive to its parameters and poorly
generalising out-of-domain, convex combination outperforming it both in- and
out-of-domain, largely agnostic to the choice of score normalisation, and
sample-efficient — needing only a small training set to tune its single
parameter. With 200 public sessions, "one parameter, tune on a small sample" is
the deciding property.

```
fused(d) = α · lexical̂(d) + (1 − α) · densê(d)          # α = 0.78 tuned
```

Normalisation is theoretical min-max (TM2C2): the theoretical minimum of a BM25
or cosine score is 0, so only the maximum is estimated from the sample. Using
the *observed* minimum instead would make the bottom of every result list
identically zero and destroy the tail ordering MRR depends on.

Combination is over the **union**, so a document only one route found still gets
its contribution, scored against zero on the other side.

### Depth

| knob | tuned | controls |
|---|---|---|
| `per_field_depth` | 800 | how many docs each field may surface before fusion |
| `candidate_depth` | 200 | pool size after fusion |
| `rerank_depth` | 200 | how many of those get a feature vector |
| `dense_depth` | 150 | Route B's own cutoff |

**Recall lost here cannot be recovered downstream**, and depth is the
second-largest lever in the system (§8). The ablation shows the mechanism
cleanly: cutting the pool to 20 *raises* MRR while dropping HR@10 — a shallow
pool ranks what it contains slightly better and simply lacks the rest.

`per_field_depth` is worth understanding as a category of change: a depth cutoff
is not a coefficient that can be shaped to fit noise in a tuning fold. Raising it
only makes more genuinely-matching documents visible, introducing no new degree
of freedom to overfit against. Held-out score improved at least as much as train
at every value ≥800.

---

## 4. Feature Scorer / Reranker — `features.py`, `ranking.py`

### 4a. Feature extraction

`FEATURE_NAMES` is the authority — **40 columns**. `extract()` is a pure function
of `(candidate, ScoringContext)`: no I/O, no mutation. Purity is the requirement
that matters, because the identical function runs inside the live agent and
offline over logged sessions (§7). `ScoringContext` is passed explicitly rather
than read off the agent, so a replay can reconstruct it exactly from a log line.

| family | columns |
|---|---|
| retrieval | `fused`, `bm25_{title,features,categories,description,store}`, `dense` |
| phrase | `phrase_{title,features,categories}`, `coverage` |
| span | `span_coverage`, `span_all` |
| prior | `popularity`, `quality`, `has_price`, `has_description`, `n_features_norm` |
| constraint | 6 dimensions × {`satisfied`, `violated`, `unknown`} = 18 |
| context | `profile_affinity`, `category_focus`, `title_low_coverage`, `popularity_low_coverage` |

**Six constraint dimensions:** gender, brand, category, price, material, color.

**Three-way, encoded as separate columns.** `satisfied` and `violated` are
distinct binary columns rather than one signed column, so they carry independent
weights — violating a stated gender (−0.230) is far more costly than satisfying
it (+0.055) is valuable. Per P2, `unknown` is a mild penalty and never an
exclusion; three of six are non-zero under the tuned config (gender −0.01,
category −0.005, price −0.005).

`unknown` is a **first-class feature column**, so `LinearModel` alone reproduces
the final ordering. Any tool that applies a separate post-hoc unknown-penalty is
double-counting.

**Material and colour do not emit `violated` under the shipped configuration**,
and the mechanism is not symmetric between them:

- `check_color` structurally cannot return `violated` — `satisfied` on a hit,
  `unknown` otherwise. Absence of a colour word from sparse marketing copy is
  not evidence of conflict.
- `check_material` has a `violated` branch, reached only for a *qualified*
  mention (a "faux leather" match against a wanted `leather`) and gated by the
  module constant `FAKE_MATERIAL_MODE`, currently `"unknown"`.

Consequence worth knowing before tuning: `material_violated` (−0.020) and
`color_violated` (−0.015) are non-zero in `config/tuned.json` and currently
**unreachable** — no candidate lands in those columns.

**`span_all` is the conjunctive signal.** `phrase_*` measures ordered-bigram
*overlap*, which cannot distinguish "3 of 4 spans matched" from "all 4" — both
can produce identical bigram sets. `span_all` is 1.0 iff *every* live constraint
span matches, tested against `Product.search_blob`, a lowercased match surface
built once at catalog load. The conjunction is the discriminative part: four
disclosed spans that each match 13–41% of a category can jointly cut 284
candidates to 2. `span_coverage` (the fraction matched) is carried at weight 0.0.

**Coverage interactions.** `title_low_coverage` and `popularity_low_coverage` are
literally `bm25_title * (1 - coverage)` and `popularity * (1 - coverage)`, both
carrying negative weights (−0.20 and −0.40). This is not a title-length penalty:
a detailed title keeps its evidence when it covers the query. What is reduced is
title and popularity confidence *unsupported by query coverage*.

**Prior features, per P3.** `log1p(rating_number)` is the strongest single
feature in the system (AUC ≈0.955 alone). `has_price`, `n_features`, and
`average_rating` Bayesian-shrunk toward the global mean
(`rating_prior_weight=50`) round it out. Nothing in `PriorConfig` may exclude a
candidate.

### 4b. Scoring model

**A weighted linear sum** — `LinearModel`, weights from `config/tuned.json`,
looked up **by name, not position**, so reordering `FEATURE_NAMES` cannot
silently mis-assign them.

```
score(d) = Σᵢ wᵢ^(intent) · xᵢ(d)
```

Grinsztajn et al. (arXiv 2207.08815) and the surrounding tabular benchmarks find
that **feature engineering and dataset characteristics set the performance
ceiling rather than model class**. That is the operating assumption here: the
features are the contribution, and the model is a way of adding them up.
`ScoringModel` is a Protocol, so a fitted model drops in behind the same
interface without the retriever or the feature function changing.

**Intent-conditional weighting.** `Ranker` builds one `LinearModel` per routed
intent; any feature in `INTENT_OVERRIDABLE` may carry a different weight per
intent, unset falling back to the shared default. Currently live:

```
w_fused           = 1.0
w_fused_buying    = 0.0        w_fused_uncertain = 0.0
w_bm25_title      = 0.26       w_bm25_title_buying = 0.18
```

The reason is double-counting. `fused` is a convex combination of the same
lexical and dense signals that also enter the vector separately, so it counts
text evidence twice. On constraint-bearing turns that double count drowns the
structured features. A **browsing** turn has no disclosed constraints to drown
and `fused` is the best evidence available there — which is why this is
conditional rather than a global cut. The same logic sets a lower `bm25_title`
for buying, where disclosed constraints are stronger evidence.

The per-intent map is built **only when no external `model` is supplied**, so a
model dropped into `model` keeps full control of the vector rather than having
features rewritten underneath it.

**Determinism, per P4.** `top_n` sorts on `(-score, doc_id)`; ties break on
`parent_asin`, never on dict order.

---

## 5. Clarification Policy — `clarify.py`

**The highest-value component in the system (P1).**

**Grounding: the EAR gate (Lei et al. 2020).** Ask only when (1) the candidate
space is still large enough to be worth narrowing, (2) a question still carries
information gain against user patience, and (3) the recommender is not yet
confident its top results will be accepted.

```
gate 1   |candidates| ≥ ask_min_candidates (12)
gate 2   confidence  < ask_max_confidence  (0.82)
gate 3   gain        ≥ ask_min_info_gain   (0.05)

override  turn ≥ ask_turn_budget (8)       → always answer
override  customer asked to be questioned  → always ask, skipping gates 1 and 2
```

The turn-budget override is a hard stop: running out of turns costs Efficiency
and can cost the session outright. The invitation override matters because a
customer who says the options are wrong and asks for a question has told you
that staying silent burns the rest of the session.

**Gate 2 is NQC** (Normalized Query Commitment; Shtok et al. 2009) —
post-retrieval query performance prediction with no ground truth available: the
standard deviation of the top-10 scores, normalised by the top score. High
spread means the ranker clearly pulled some candidates ahead and is worth
trusting; scores bunched together mean it did not, and a question is worth more
than an answer.

**Gate 3 is where this departs from the literature, and the departure is a
measurement.** Entropy alone picks `category` and `brand`: they partition the
candidate pool superbly, and this customer answers them **never** — 0.000 yield
across all 200 sessions. So the objective multiplies by a measured answer
probability:

```
gain(a) = P(answered | a) · H(pool ÷ a)
```

`P(answered)` is `dialogue.attribute_prior`, produced by
`python -m tools.measure_attribute_yield`:

| attribute | prior | | attribute | prior |
|---|---|---|---|---|
| feature | 1.000 | | style | 0.250 |
| other | 0.949 | | size | 0.147 |
| material | 0.556 | | use_case | 0.039 |
| color | 0.295 | | **brand / budget / category** | **0.000** |

`attribute_prior_floor` (0.05) keeps a zero-yield attribute askable once
everything else is exhausted — insurance against the private set's disclosure
policy differing at the margins. `repeat_ask_decay` (0.45) discounts re-asking,
since the customer reveals at most two spans per turn. A refused attribute is
never asked again.

Entropy `H` is estimated **only over fields with real coverage**. Sparse fields
land every candidate in one `__missing__` bucket, which correctly reports them as
uninformative rather than magically discriminative.

**What does not transfer:** SCPR, UNICORN and that line are RL policies over
knowledge graphs, assuming clean per-item attribute sets. Per P2 those do not
exist here. The EAR gate is the part of that literature the data supports.

**Two implementation requirements that are load-bearing:**

1. **The clarifier is handed the whole ordered pool, not the ten returned.**
   Gate 1 asks whether the space is still large enough to narrow, and a list
   truncated to ten always looks settled — which silently disables clarification
   altogether. `Ranker.rank` returns the entire pool ordered for this reason.
2. **The agent asks *and* answers on the same turn — SUPERSEDED 2026-08-31.**
   Still unconditionally true, but two later gates hold the recommendation list
   back on early turns regardless: `recommend_min_spans` (wait for the customer
   to disclose something concrete) and `min_recommend_confidence` (wait for the
   ranker to commit). See the revised D2 in §9.

---

## 6. Diversity — `ranking.py`

MMR on positions 2–10, browsing only, position 1 never diversified — every
demotion of the true target costs MRR directly.

**Shipped disabled** (`enable_mmr = False`); ties break on `parent_asin`. The
catalog explains why: only 4.8% of rows fall in a multi-row near-duplicate
cluster (first-8-token shingling), max cluster size 6, 90% of clusters are pairs.
The top 10 does not naturally fill with duplicates, so there is nothing for
diversity to fix. `agent.py` still passes `diversify=(intent == BROWSING)`, so
the path is live if the catalog assumption ever changes.

---

## 7. Trace and replay loop — `tools/offline_eval.py`, `tools/tune.py`

**Tracing.** Setting `trace_path` in the config appends a feature row per scored
candidate (~115k rows, ~45 MB per run). Tracing is passive and verified not to
change results.

**The replay gate, and it is the most important tool in the repo.**
`tools/offline_eval.py` reproduces the ranker from a trace and must agree with
the live evaluator **session by session on `best_rank` — all 200 of them, not
merely on aggregate MRR.** Aggregate agreement hides compensating errors; this
gate has caught real defects that aggregate MRR would have passed.

**Freeze the retriever before fitting anything.** Negatives are mined from the
retriever's own output, so any model learns to correct *that specific retriever*.
Change retrieval after fitting and the model is stale.

**Tuning.** `tools/tune.py` runs coordinate search over `SEARCH_SPACE`, with
`stratified_halves` for train/holdout splits. Two operational hazards:

- `tune.py`'s `--output` **defaults to `config/tuned.json`** and `--report` to
  `docs/tuning_report.json`, so a bare run silently overwrites the live
  submission config. Always pass both.
- The evaluator's `--output` defaults to `results.json`, which is gitignored with
  no committed backup. Always redirect it when experimenting.

`.githooks/pre-commit` refuses a commit staging `config/tuned.json` without
`CLAUDE.md`. It is tracked, but the setting that activates it is local: every
clone must run `git config core.hooksPath .githooks` once.

---

## 8. Evaluation & framing

**Tune on** the 200 public sessions via the official local evaluator, unmodified.
`stratified_halves` (seed 7) splits scenario-balanced folds; **fit on one, quote
the other.**

**Measurement discipline**, because 200 sessions is a small set and many
configurations have been scored against it:

- Paired MRR standard error is ~0.024 across 200 sessions (bootstrap, 20k
  resamples); single-run SE ~0.029. A change worth less than roughly +0.05 MRR
  **cannot be verified here.** That is a statement about what 200 sessions can
  measure, not about whether a change is real — report such results as unverified
  rather than discarding them. The private set is 800 sessions with roughly half
  the noise.
- **Report the sign test and the confidence interval together.** They frequently
  disagree, and quoting whichever looks better is how you fool yourself.
- **Decompose against the score weights before adopting.** A change framed purely
  around MRR is optimising the 0.30 term while HR@10 carries 0.50.
- **Prefer changes that add information to the vector over changes that
  redistribute weight within it.** The features that have generalised on this
  problem — the span conjunction, commonness damping, retrieval depth — all
  supplied something the vector did not previously contain.
- **Re-measure the merge.** Two branches developed in parallel are each scored
  against a baseline lacking the other, so their gains do not simply add. Record
  the commit next to every number you report; a branch checkout silently changes
  `FEATURE_NAMES`, `config/tuned.json` and the test count under any analysis in
  flight.

**Four evaluator facts that will silently corrupt an offline analysis:**

1. **Sessions cannot be joined to labels by id.** The evaluator hands the agent a
   fresh `uuid4`; the agent never sees `sample_id`. Join **positionally** —
   `evaluate()` iterates samples single-threaded, so the Nth distinct session id
   in a trace is the Nth sample.
2. **The override turn is drawn per sample**, `rng.choice([3, 4])` seeded on
   `f"{sample_id}\0{scenario_type}"` — not fixed at 3.
3. **`best_rank` is first-hit-in-top-10, not full-pool rank.** For
   `intent_override` sessions any hit *before* the override turn is ignored, and
   the evaluator breaks on first hit — so recommending early locks in whatever
   rank you have.
4. **`difficulty_bucket` is deterministic from `scenario_type`** and carries no
   extra information. Slicing on it is redundant.

**Ablation table for the writeup** — one row per component, HR@10 / MRR / MTTC,
live in `docs/ablations.md`. It is what makes Technical Execution legible to
judges. The ordering has been stable across every tuning round: clarification
(−0.4473) dominates, then candidate depth, then popularity priors, then phrase
evidence.

**On the popularity prior.** State plainly in the README and demo that
popularity-as-feature is correct for this benchmark and wrong for a production
store: the labels were generated by sampling real purchases, which concentrate on
popular items, and a deployed system ranking this way would starve the long tail.

Reference points: ranking by popularity alone scores HR@10 3.5% — it returns
Crocs and Hanes boxer briefs to every user, because it is query-blind — against
the 12.5% BM25 baseline. The gain comes from combining relevance with the prior,
not from either alone. Pruning to `rating_number ≥ 25` retains 97.5% of targets
while cutting the catalog to 37.9%: usable as a soft prior, **not** as a hard
filter, since ~5% of targets sit below the popular tail and one has a single
review.

Saying this out loud converts the strongest feature from a criticism into
evidence of problem insight — Innovation and Impact & Relevance both reward it.

**Do not build** popularity debiasing or calibrated recommendation. That
literature exists to correct a bias these labels contain by construction.
Applying it means fighting the metric.

---

## 9. Decisions — settled

*Numbering preserved because `config.py` cites D2.*

**D1 — Retrieval boundary. `agent.py` owns retrieval; the ranker never calls the
index.** The agent hands the ranker a candidate pool, which keeps the reranker a
pure function of `(candidates, context)` — the property that makes offline replay
(§7) possible at all. It also puts the popularity prior in the reranker, where it
can be ablated, rather than in candidate selection, where it would silently
delete the ~5% of targets below the popular tail.

**D2 — Emit recommendations on ask-turns? Yes, but not before the evidence
arrives. REVISED 2026-08-31.**

*Original decision, kept per supersede-never-delete:* "Yes
(`dialogue.recommend_on_ask_turns = True`). Nothing in the response schema makes
`ask_attribute` and `recommendations` mutually exclusive, first-hit turn drives
MTTC, and a turn that returns nothing is a discarded chance at the hit. The agent
asks and answers on the same turn."

**What that reasoning missed.** `local_evaluator.py:252` breaks the session on
first hit, so the rank at that moment is *final* — a weak early list does not
merely miss a better rank later, it forecloses it. The original argument is
correct for HR@10 and MTTC and wrong for MRR. Two gates now hold the list back
while the turn is uninformative, and they compound:

| | TechnicalScore | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| neither gate | 0.909328 | 1.000 | 0.756095 | 1.875 |
| `recommend_min_spans: 1` | 0.928002 | 1.000 | 0.833673 | 2.105 |
| `min_recommend_confidence: 0.054` | 0.936614 | 1.000 | 0.876048 | 2.310 |
| **both (live)** | **0.942939** | **1.000** | **0.902464** | 2.390 |

**The `recommend_on_ask_turns` flag is gone (2026-08-31).** Asking and
answering may always coexist — that is now unconditional in the code rather
than a config value, because the alternative was measured and is not a position
anyone should be able to select: at `False` the agent goes silent on every
ask-turn, MTTC blows out 2.390 → 8.015 and the score falls to **0.842339**
(−0.1006), even though MRR *rises* to 0.950464. The gates are about *when there
is enough evidence to answer*, not about whether asking and answering may
coexist, and they buy that same MRR selectively at a fraction of the MTTC. Full
reasoning and measurement in CLAUDE.md.

**D3 — LLM reranker. No, per P4.** Listwise beats pointwise (LRL, arXiv
2305.02156), but LLM rankers are order-sensitive — which is why permutation
self-consistency exists, and which is a determinism problem for a graded
submission. Official scoring may also run without network access. Closed on
reproducibility and rules, not on quality.

---

## 10. Where the headroom is

*Live numbers in `CLAUDE.md`.*

HR@10 is near ceiling, and a small number of targets are genuinely unidentifiable
from anything the customer is able to say — at least one has been traced turn by
turn and confirmed to carry no distinguishing information anywhere in the
session. Remaining headroom is therefore mostly **MRR**, concentrated in sessions
that retrieve the target and place it a position or two off the top.

**Browsing is the weakest track on every metric** and holds the remaining misses.
It is the least-explored area, and the one where new information would pay most.

The reliable route there is a **new feature** — something the vector does not
currently measure — rather than a new weighting over the existing 40 columns.
`tools/separability.py` will compute, for any candidate feature set, whether a
linear scorer can win a given rank bucket without losing the sessions already
won; run it before building against that band.
