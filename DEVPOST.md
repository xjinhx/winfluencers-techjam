# Shopping Copilot — Conversational Search Agent

**A shopping agent that asks better questions, remembers what you said, and
ranks 50,000 products by what you actually meant.**

---

## The problem, as we came to understand it

Keyword search breaks down in conversation for a reason that is easy to state
and hard to fix: it has no memory and no theory of intent. "Under $40" and "less
than $40" are different strings to BM25. A customer who says "I need shoes for a
trip" and then "waterproof, under $80" has given two halves of one request that
a stateless index will never join.

The competition frames this as three sub-problems — routing intent, tracking
state, ranking results — and weights them into a single score:

`TechnicalScore = 0.50 × HR@10 + 0.30 × MRR + 0.20 × Efficiency`

Our first decision came from reading that formula. HR@10 and MRR are both
determined entirely by the returned list, so 80% of the score lives in ranking
quality. The provided baseline scores 12.5% HR@10 — a ranking failure, not a
dialogue failure.

**That reading was half wrong, and finding out how was the most useful thing
that happened to us.** Ranking is where the score is *counted*, but on this
benchmark dialogue is where it is *created*: without a question, no new
information arrives, so there is nothing better to rank. More below.

## What we built

A per-turn pipeline, all in-memory, no vector database, no LLM:

`parse → route intent → retrieve (lexical + semantic, fused) → rerank → clarify`

**Utterance parsing and state.** A `ShoppingState` accumulates slots across
turns and rewrites them on override. Parsing is deliberately not bag-of-words.
On the opener *"I'm looking for Jewelry Necklaces. A key requirement is:
Material:alloy"*, a bag-of-words query scores "key" as a content term and
returns key-pendant necklaces — the true target fell outside the top 200
entirely. Each turn is split into a category phrase, constraint spans, and
control signals, and only content reaches the retriever.

**Retrieval.** Five per-field BM25 indexes — title, features, categories,
description, store — never concatenated, because a term matching in a 12-token
title means something different from the same term in 400 words of description.
Queries are field-routed: the category phrase goes at `categories` and `title`,
quoted constraint spans at `features`. A character-n-gram semantic route runs
alongside, fused by convex combination.

**Feature-based reranking.** 30 features per candidate — per-field BM25,
semantic similarity, phrase/bigram overlap, term coverage, catalog priors, and
three-way constraint satisfaction — combined by a weighted linear model and
ordered deterministically.

## The findings that made the difference

**Clarification is the system, not a refinement to it.** Removing it costs
−0.4473 TechnicalScore, an order of magnitude more than any other component. Our
first working build scored 0.3167 because the clarification gate was being
handed the ten returned recommendations instead of the ranked candidate pool, so
its "is the candidate space still large enough to narrow?" test rejected every
turn and the agent never asked anything. Browsing sessions stalled with the
simulated customer repeating *"Ask me about one specific attribute"* to an agent
that never did. Fixing that one plumbing bug moved us from 0.3167 to 0.7641.

**Asking well matters as much as asking.** Information gain computed over
catalog fields picks `category` and `brand` — they partition the candidate pool
beautifully, and this customer answers them *never*. So we measured disclosure
per attribute across all 200 sessions instead of assuming it:

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

Expected gain became `P(answered) × uncertainty removed`, with the first term
measured. A question the customer cannot answer wastes one of ten turns however
elegantly it would have split the catalog.

**Target products are a popularity-biased subpopulation.** Median review count
is 6,846 for targets versus 12 for a random catalog row — AUC 0.956 on that
single feature. This is not a discovery about shoppers; it is a property of how
the benchmark was constructed. Targets are sampled from a 5-core leave-last-out
split of real purchase records, and real purchases concentrate on popular items.

**We use it as a soft prior, never a filter.** Pruning to 25+ reviews would keep
97.5% of targets and cut the search space to 37.9% — but 5% of targets live below
the popular tail, one with a single review, and a discarded target is an
unrecoverable loss.

**And we want to say plainly that this is right for the benchmark and wrong for
a real store.** A deployed system ranking this way would starve the long tail. It
exploits the label distribution, not shopper behaviour. The honest version of
the result is that popularity alone scores just 3.5% HR@10 — *worse* than the
BM25 baseline — because it is query-blind, returning identical Crocs and Hanes
boxer briefs to every customer. The lift comes from combining relevance with the
prior, and neither half works alone.

Two other measurements shaped the build:

- **The catalog is sparse where the problem statement assumes it is rich.**
  Price is null on 78.9% of rows; `Color` exists on 4.9% of products, `Material`
  on 4.1%, `Size` on 1.9%. So every constraint is three-way — satisfied,
  violated, or **unknown** — with unknown a mild penalty rather than an
  exclusion. Hard-filtering on a field that is 79% missing deletes the target.
- **One structured attribute survives.** `Department` covers 87.2% of the
  catalog and encodes gender. Gender is the highest-elimination-power attribute
  in clothing, and it is the one we can actually use, with title and
  category-path fallback lifting effective coverage to 98.3%.

## Results

| Configuration | HR@10 | MRR | MTTC | TechnicalScore |
|---|---|---|---|---|
| Official baseline | 0.125 | 0.068 | 9.81 | 0.1067 |
| Ours — defaults | 0.885 | 0.554 | 3.23 | 0.7641 |
| **Ours — tuned** | **0.910** | **0.565** | **2.98** | **0.7848** |

7.4× the baseline TechnicalScore. Turns-to-conversion down from 9.81 to 2.98.
Measured on the 200 public sessions with the unmodified official evaluator.

Defaults already reach 0.7641 — tuning contributed 0.021, and only 0.011 of that
survived to a held-out half of the sessions. Most of the gain is architectural
rather than fitted, which is the main reason we expect it to hold on the private
set.

### What each component is actually worth

Every row disables exactly one thing, full 200 sessions:

| component removed | TechnicalScore | delta |
|---|---|---|
| *full system* | 0.7641 | — |
| clarification policy | 0.3167 | **−0.4473** |
| candidate depth 200 → 20 | 0.7323 | −0.0318 |
| popularity priors | 0.7410 | −0.0230 |
| phrase / bigram evidence | 0.7502 | −0.0139 |
| coverage + category focus | 0.7565 | −0.0076 |
| constraint scoring | 0.7620 | −0.0021 |
| semantic route | 0.7628 | −0.0013 |
| per-field weighting | 0.7648 | +0.0007 |
| profile personalisation | 0.7650 | +0.0010 |
| *added:* MMR diversity | 0.7641 | +0.0000 |

Two components measurably do **not** earn their place — profile personalisation
and per-field lexical weighting both score marginally better when removed. Both
are inside noise, so it is not a finding in either direction, but nothing in our
results argues for them and we would rather report that than imply otherwise.

We also learned a methodology lesson worth passing on: we first ran this table
on an 80-session subset and three readings were wrong, including one component
we nearly cut. At 80 sessions a single session is 1.25 points of HR@10, so
anything under ~0.02 was sampling noise. All ablations here are on the full 200.

## Grounding in prior work

**Convex combination over Reciprocal Rank Fusion.** Bruch et al. (arXiv
2210.11934) find RRF sensitive to its parameters and poorly generalising
out-of-domain, while convex combination wins in- and out-of-domain and is
sample-efficient — one parameter, tunable on a small set. With 200 sessions,
sample efficiency decided it. We fuse two routes, staying inside the regime that
paper studied.

**The EAR clarification gate** (Lei et al., 2020): ask only when the candidate
space is small enough, the question still carries information gain against user
patience, and the recommender is not yet confident. We did *not* implement the
RL policies that succeeded it (SCPR, UNICORN) — they assume clean per-item
attribute sets, and our audit showed this catalog has none.

**Tree ensembles for tabular ranking.** Grinsztajn et al. (arXiv 2207.08815)
find GBDTs frequently outperform deep models at this data scale, and that
feature engineering rather than model class sets the ceiling — which is why we
treated the shopping-specific features as the contribution rather than the
model. We did not ship a GBDT; see Limitations.

**Anchor-based CRS framing.** PSCon (arXiv 2502.13881) notes e-commerce
conversational recommendation is typically anchor-based, simulated from
predefined intent slots — exactly the structure of the TechJam simulator, and
the reason slot-based routing beats free-form NLU here.

## Development tools

- VSCode with the Python extension
- Python 3.12 virtual environment (`venv`)
- Git / GitHub for version control

<!-- If the competition expects disclosure of AI coding assistants, add it here.
     This is a disclosure decision for the team to make, not a technical one. -->

## APIs used

**None.** The agent makes no external API calls. All ranking is deterministic
and runs in-memory, so there is no per-session token cost, no rate limit, and no
network dependency at evaluation time. The evaluator's reported token usage for
a full 200-session run is 0 prompt / 0 completion.

Disclosure figures: $0.00 estimated cost, 0 tokens, ~20 s one-off index build,
89 ms median per-turn latency (122 ms p95), ~110 s for the full 200-session run.

## Libraries and frameworks

**Python standard library only — no third-party dependencies.** This is
deliberate: submission rules warn that organizer policy may disable network
access for final scoring, so there is no package to install and no model to
download. `requirements.txt` is intentionally empty of packages.

Modules used: `json`, `re`, `math`, `array`, `collections`, `dataclasses`,
`functools`, `pathlib`, `statistics`, `random`, `argparse`, `unittest`.

The BM25 inverted indexes, the character-n-gram semantic index, the fusion
layer, the feature extractor, the linear ranker, and the coordinate-ascent tuner
are all implemented from scratch.

## Datasets and assets

- **TechJam frozen catalog** — 50,000 products from the
  `Clothing_Shoes_and_Jewelry` category, provided by the organizers
- **200 labelled public development sessions** — used for tuning and validation
- Both derived from **Amazon Reviews 2023** (McAuley Lab, UCSD)
- No external datasets, no manually labelled data, no scraped content

## What we would do with more time

A prioritised roadmap with the measurement behind each item is in
`docs/pending.md`.

**Make the intent router actually do something.** It is implemented and scores
every turn, but its only consumer is MMR diversity for browsing sessions, and
MMR measured at exactly +0.0000 so it is disabled. The router therefore computes
a label that reaches the trace log and nothing else. Having the route select
genuinely different retrieval weight profiles is the cleanest unfinished piece
of our design, and we would rather say so than describe a routing behaviour we
did not ship.

**MRR is where the headroom is.** At 0.910 HR@10 and 0.565 MRR we find the
target in 91% of sessions but at mean rank 3.05 — 90 of 182 hits land at rank 1,
and 66 land at rank 3 or worse. The remaining MRR is worth 0.131 of
TechnicalScore, more than the remaining HR@10 and Efficiency headroom combined.
A LightGBM `lambdarank` model trained on logged sessions weights each pairwise
swap by its effect on the ranking metric, concentrating learning on exactly
those top positions. The feature vector, a trace hook that logs replayable
feature rows, and a `ScoringModel` seam are already in place for it.

**Bridge the attribute vocabulary gap.** The nine profile `preference_tags` are
abstract — fit, material, comfort, style, durability, performance, warmth,
weather, general shopping — and map onto no catalog field. Our profile affinity
feature ablates to +0.0010, i.e. inert. Learning tag-to-product affinities from
the labelled sessions is the most promising direction we did not get to.

**Replace the popularity prior with real personalisation.** For any use outside
this benchmark, that is the necessary change — see the honest caveat above.

## Limitations

- **The intent router does not affect output** in the shipped configuration, as
  described above
- **The "dense" route is not neural** — it is a character-n-gram TF-IDF index,
  chosen so the system survives an offline scoring run. It buys tolerance to
  morphology and spelling drift, not semantic generalisation: "something elegant
  for a dinner date" will not reach a listing that never says "elegant"
- **No LambdaRank shipped** — LightGBM is not installable under the offline
  constraint, and at 200 sessions a tuned linear model was the defensible choice
- Weights are tuned on 200 sessions with one positive each; the tuned headline is
  measured partly on sessions it was fitted on, so the unbiased estimate is the
  held-out 0.7869 rather than 0.7848
- The two validation folds differ by 0.025 under identical default weights,
  which exceeds most individual component effects — small-sample noise is a real
  constraint on every conclusion here
- MTTC has a floor of 1.30 because intent-override sessions cannot score before
  the override appears on turn 3–4; our 2.98 includes 0.99 contributed purely by
  the 9% of sessions we miss entirely
- The popularity prior is a benchmark property and would not transfer to
  production
