# Shopping Copilot — Conversational Search Agent

**TechJam 2026 · Track 4: AI Conversational Search and Recommendations**

A multi-turn shopping agent that finds a hidden target product in a 50,000-item
Amazon clothing catalog, asking clarifying questions only when they are worth
more than another retrieval call.

## Results

Measured on the 200 public development sessions using the official local
evaluator (`evaluator/local_evaluator.py`, unmodified).

| Configuration | HR@10 | MRR | MTTC | TechnicalScore |
|---|---|---|---|---|
| Official weak BM25 baseline | 0.125 | 0.068 | 9.81 | 0.1067 |
| Ours — default weights | 0.885 | 0.554 | 3.23 | 0.7641 |
| **Ours — tuned** | **1.000** | **0.902** | **2.39** | **0.9429** |
| *superseded 2026-08-31* | *0.960* | *0.690* | *2.24* | *0.8621* |

`TechnicalScore = 0.50 × HR@10 + 0.30 × MRR + 0.20 × Efficiency`

An 8.1× improvement over the provided baseline, with mean turns-to-conversion
falling from 9.81 to 2.24.

Per scenario, tuned:

| scenario | n | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| buying | 80 | 0.975 | 0.7176 | 1.51 |
| browsing | 80 | 0.938 | 0.5966 | 2.29 |
| intent override | 30 | 0.967 | 0.8317 | 3.93 |
| boundary | 10 | 1.000 | 0.7850 | 2.60 |

The tuned row is fitted on a 100-session half of this same set, so it is
optimistically biased — expect the private set below it. The split-half estimate
last measured **0.7763 → 0.7869**, but that was taken at an earlier tuning round
and has not been re-run against the current weights.

---

## Project overview

Traditional keyword search fails conversational shoppers in two ways: it has no
memory across turns, and it cannot tell an open-ended browse from a high-intent
purchase. Our agent addresses both, and adds a third element that came out of
measuring the dataset rather than reading the literature.

**Per-turn pipeline:** parse → route intent → retrieve (lexical + semantic,
fused) → rerank → clarify.

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

3. **Feature-based reranking.** 30 features per candidate — per-field BM25,
   semantic similarity, phrase/bigram overlap, term coverage, catalog priors,
   and three-way constraint satisfaction — combined by a weighted linear model,
   ordered deterministically with ties broken on `parent_asin`.

### What we found in the data

**Clarification is the system, not a refinement to it.** Removing it costs
−0.4473 TechnicalScore, an order of magnitude more than any other component.
The reason is structural: a browsing session opens with a category and no
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
**unknown** — with `unknown` a mild penalty rather than an exclusion. Anything
that deletes candidates on a missing field deletes the target. Material and
colour resolve only to satisfied or unknown, never violated: absence of a word
from sparse copy is not evidence of conflict.

**One structured attribute does survive.** `details.Department` covers 87.2% of
the catalog and encodes gender (50.9% womens, 21.2% mens, plus unisex and
children's splits). Gender is the highest-elimination-power attribute in
clothing. For the missing 13% we fall back to title tokens and then the category
path, which lifts effective coverage to 98.3%.

### Component ablations

Each row disables exactly one component, full 200 sessions
(`python -m tools.ablate`, table in `docs/ablations.md`).

| component removed | HR@10 | MRR | MTTC | TechnicalScore | delta |
|---|---|---|---|---|---|
| *full system* | 0.885 | 0.5535 | 3.23 | 0.7641 | — |
| clarification policy | 0.380 | 0.1858 | 7.45 | 0.3167 | **−0.4473** |
| candidate depth 200 → 20 | 0.830 | 0.5689 | 3.67 | 0.7323 | −0.0318 |
| popularity priors | 0.860 | 0.5371 | 3.50 | 0.7410 | −0.0230 |
| phrase / bigram evidence | 0.870 | 0.5443 | 3.40 | 0.7502 | −0.0139 |
| coverage + category focus | 0.880 | 0.5417 | 3.30 | 0.7565 | −0.0076 |
| constraint scoring | 0.885 | 0.5470 | 3.23 | 0.7620 | −0.0021 |
| semantic route | 0.890 | 0.5366 | 3.16 | 0.7628 | −0.0013 |
| per-field weighting | 0.895 | 0.5295 | 3.08 | 0.7648 | +0.0007 |
| profile personalisation | 0.885 | 0.5575 | 3.23 | 0.7650 | +0.0010 |
| *added:* MMR diversity | 0.885 | 0.5539 | 3.23 | 0.7641 | +0.0000 |

Retrieval depth is the second-largest lever, and the metric split shows why:
depth 20 *raises* MRR to 0.5689 while dropping HR@10 to 0.830. A shallow pool
ranks what it contains slightly better and simply lacks the rest.

### Grounding in prior work

- **Fusion by convex combination, not RRF.** Bruch et al. (arXiv 2210.11934)
  find RRF sensitive to its parameters and poorly generalising out-of-domain,
  while convex combination outperforms it both in- and out-of-domain, is largely
  agnostic to score normalisation, and is sample-efficient — needing only a
  small training set to tune its single parameter. With 200 sessions, that last
  property decided it. We fuse two routes, which keeps us inside the regime that
  paper studied; it explicitly leaves three-or-more to future work.
- **Clarification gate from EAR** (Lei et al., 2020): ask only when the
  candidate space is small enough, further questions still carry information
  gain against user patience, and the recommender is not yet confident. We
  deliberately did *not* implement the RL policies that follow it (SCPR, arXiv
  2007.00194; UNICORN, arXiv 2105.09710) because they assume clean per-item
  attribute sets, which this catalog does not have.
- **Tree ensembles for tabular ranking.** Grinsztajn et al. (arXiv 2207.08815)
  find GBDTs frequently outperform deep models on tabular data at this scale,
  and that feature engineering rather than model class sets the ceiling — which
  is why we treated the shopping-specific features as the contribution. We did
  not ship a GBDT; see Limitations.
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
the turn path.

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
MTTC   2.39
TechnicalScore  0.942939
```

Tuned weights live in `config/tuned.json` and are loaded automatically by
`starter/agent.py`. **To reproduce the untuned 0.7641 row, delete or rename that
file** (or point `SHOPPING_COPILOT_CONFIG` at a different config). The evaluator
and public labels are unmodified from the participant kit.

Other entry points:

```bash
python -m unittest discover -s tests      # 29 unit tests
python -m tools.demo --sample public_0002 # one full multi-turn transcript
python -m tools.ablate                    # regenerate the ablation table
python -m tools.measure_attribute_yield   # regenerate the disclosure table
python -m tools.tune                      # coordinate ascent, split-half CV
```

### Repository layout

```
starter/agent.py               Agent contract: reset() / respond(); loads config
shopping_copilot/
  text.py                      Shared tokeniser (index and query time)
  config.py                    Every tunable parameter, JSON round-trippable
  catalog.py                   Catalog load, normalisation, coverage flags, priors
  index.py                     Per-field BM25 inverted indexes
  dense.py                     Character-n-gram semantic route
  fusion.py                    TM2C2 normalisation, convex combination
  structured.py                Gender/brand/category slots, three-way checks
  profile.py                   Anonymised-profile personalisation
  state.py                     Utterance parsing, slots, override rewrite
  intent.py                    BUYING / BROWSING / UNCERTAIN router
  features.py                  30-dim feature vector (pure function)
  ranking.py                   Linear scorer, MMR diversity
  clarify.py                   EAR-style clarification gate
  agent.py                     Orchestration
  baselines/weak_bm25.py       The original starter, kept for comparison
tools/                         tune, ablate, demo, attribute-yield measurement
tests/                         29 unit tests
config/tuned.json              Tuned weights (loaded by default)
docs/report.md                 Full method report
evaluator/                     Official local evaluator (unmodified)
data/                          catalog.jsonl (downloaded), public_set.jsonl
```

---

## Limitations and what we would improve

Planned work, with the measurement behind each item, is in
[`docs/pending.md`](docs/pending.md).

**The intent router does not currently affect the output.** It is implemented
and wired, scoring every turn on constraint density, linguistic markers, slot
specificity, and profile alignment. But its only consumer is MMR diversity for
browsing sessions, and MMR is disabled by default because it measured at exactly
+0.0000 in ablation. So the router computes a label that reaches the trace log
and nothing else. We are stating this rather than describing an
intent-conditioned retrieval profile we did not ship. Making the route select
genuinely different retrieval weights is the cleanest unfinished piece of the
design.

**The semantic route is not neural.** `dense.py` is a character-n-gram TF-IDF
index, not sentence embeddings — a deliberate trade against the rule that final
scoring may disable network access. It buys tolerance to morphology and spelling
drift ("camisole"/"cami", "Skechers"/"skecher"); it does *not* buy semantic
generalisation, so "something elegant for a dinner date" will not reach a
listing that never says "elegant". Measured contribution is −0.0013. A documented
`EmbeddingRoute` seam exists for real vectors.

**No LambdaRank.** The design called for a GBDT reranker if the data supported
it. LightGBM is not installable under the offline constraint, and at 200
sessions a tuned linear model is the defensible choice. The feature vector, a
trace hook (`config.trace_path`) that logs replayable feature rows, and a
`ScoringModel` protocol are all in place for that upgrade — we did not take it
on faith that it would win.

**The popularity prior is benchmark-specific.** Our strongest prior feature
exploits how the evaluation labels were generated. It transfers to the private
800 sessions because they come from the same construction pipeline, but it would
not transfer to a live store. We consider naming this more useful than hiding it.

**Tuned on 200 sessions.** Weights were fitted by coordinate ascent on a
100-session half, with the other half held out and scored once: 0.7763 → 0.7869.
The train-side gain was 0.7518 → 0.7828, three times larger, which is the honest
measure of how much was fitting the fold rather than the problem. Note also that
the two folds differ by 0.025 under *identical* default weights — that is the
scale of fold-to-fold noise at 100 sessions, and it exceeds most individual
component effects above, which is why ablations are reported on all 200.

**MTTC has a structural floor we cannot cross.** Intent-override sessions cannot
score before the changed intent appears on turn 3 or 4. With those at 15% of
traffic, the theoretical best MTTC is 1.30 even with perfect play. Our 2.24
includes 0.44 contributed purely by the 4% of sessions we miss entirely (misses
are scored as turn 11); successful sessions convert at turn 1.88.

**MRR is where the remaining headroom is.** At HR@10 0.960 and MRR 0.690 we find
the target in 96% of sessions but at mean rank 2.22 — 109 of 192 hits land at
rank 1, and 83 land below it. The largest single bucket is rank 2: **39 sessions
place the target exactly one position too low**, which alone is worth +0.029 of
TechnicalScore. Reranking every hit we already retrieve to rank 1 would be worth
+0.081, more than the remaining HR@10 and Efficiency headroom combined.

**Attribute vocabulary gap, unaddressed.** The nine `preference_tags` in the user
profile are abstract (fit, material, comfort, style, durability, performance,
warmth, weather, general shopping) and do not map onto any catalog field. Our
profile affinity feature ablates to +0.0010 — inert. Learning tag-to-product
affinities from the labelled sessions is the most promising unexplored
direction.

---

## Team contributions

<!-- TODO: one line per team member. -->

- **[Name]** — retrieval indexes, fusion, intent router
- **[Name]** — feature extraction, scoring, weight tuning
- **[Name]** — state machine, override handling, clarification policy
- **[Name]** — data audit, evaluation harness, documentation

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
| Startup (catalog + index build) | ~20 s, once per process |
| Per-turn latency | 89 ms median, 122 ms p95, 139 ms max |
| Full 200-session evaluation | ~110 s |
| Determinism | exact; ties break on `parent_asin` |
