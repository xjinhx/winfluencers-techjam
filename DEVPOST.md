# Buyte — Conversational Search Agent

**A shopping agent that asks better questions, remembers what you said, and
ranks 50,000 products by what you actually meant.**

TechJam 2026 · Track 4: AI Conversational Search and Recommendations

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
information arrives, so there is nothing better to rank. And a third thing
turned out to matter more than either — *when* you show the list at all. All
three findings are below.

## What we built

A per-turn pipeline, all in-memory, no vector database, no LLM, no network call:

`parse → route intent → retrieve (lexical + semantic, fused, plus conjunctive
injection) → rerank → ask, hold, or recommend`

**Utterance parsing and state.** A `ShoppingState` accumulates slots across
turns and rewrites them on override. Parsing is deliberately not bag-of-words.
On the opener *"I'm looking for Jewelry Necklaces. A key requirement is:
Material:alloy"*, a bag-of-words query scores "key" as a content term and
returns key-pendant necklaces — the true target fell outside the top 200
entirely. Each turn is split into a category phrase, constraint spans, and
control signals, and only content reaches the retriever. An override *demotes*
the retracted constraint rather than deleting it: a withdrawn preference must
never again be checked for satisfaction, but its words still describe the
region of the catalog in play.

**Retrieval.** Five per-field BM25 indexes — title, features, categories,
description, store — never concatenated, because a term matching in a 12-token
title means something different from the same term in 400 words of description.
Queries are field-routed: the category phrase goes at `categories` and `title`,
quoted constraint spans at `features`. A character-n-gram semantic route runs
alongside, fused by convex combination.

On top of that, a **conjunctive exact-substring injection**: any product whose
text contains *every* live constraint span verbatim enters the candidate pool
regardless of its BM25 rank. This is the route that retrieval depth alone
cannot provide — we measured pool recall across the full `per_field_depth` ×
`candidate_depth` grid and found it saturates completely between 600 and 800,
with 800 → 50,000 buying **0.0 percentage points**. The injection took
`target_never_in_pool` from 1 to **0**: every public target now reaches the
ranker.

**Feature-based reranking.** 40 features per candidate — per-field BM25,
semantic similarity, phrase/bigram overlap, term coverage, catalog priors,
three-way constraint satisfaction, per-dimension unknown-penalty indicators as
first-class columns, and `span_all` (does this candidate satisfy *every*
disclosed constraint span, not just some of them) — combined by a weighted
linear model, ordered deterministically with ties broken on `parent_asin`.

**Ask, hold, or recommend.** An EAR-style gate decides whether a question is
worth a turn. A second, independent gate decides whether to show a
recommendation list *at all* this turn — see the finding below, which is where
most of our final score came from.

## The findings that made the difference

**1. Clarification is the system, not a refinement to it.** Removing it costs
−0.3892 TechnicalScore on the shipped build — 37 points of HR@10, and more than
five times any other component. Our first working build scored 0.3167 because the clarification gate was being
handed the ten returned recommendations instead of the ranked candidate pool, so
its "is the candidate space still large enough to narrow?" test rejected every
turn and the agent never asked anything. Browsing sessions stalled with the
simulated customer repeating *"Ask me about one specific attribute"* to an agent
that never did. Fixing that one plumbing bug moved us from 0.3167 to 0.7641.

**2. Asking well matters as much as asking.** Information gain computed over
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

**3. The evaluator stops at the first hit — so *when* you answer decides your
rank, and this was our single largest gain.** `local_evaluator.py` ends a
session the first turn its target appears in the top 10, which means the rank
shown on that turn is final even if the very next turn would have been better.
Measured directly: **45 of 75 sub-rank-1 sessions locked in on turn 1**, before
the customer had disclosed anything beyond a category name — and every one of
them reached rank 1 by turn 2 or 3 when replayed past their lock-in turn. The
exchange rate strongly favours waiting: one extra turn costs ~0.0001 of score
via Efficiency, while a rank 2→1 recovery pays ~0.00075 — **7.5× more**.

So the agent holds the list back on turns that cannot support it. Two gates do
this, built independently, and they compound because they read different
signals: one asks *has the customer said anything concrete yet* (at least one
disclosed constraint span), the other asks *has the ranker committed* (Normalized
Query Commitment, Shtok et al. 2009, over the ranked pool). Either can fail on
a turn the other passes.

| configuration | TechnicalScore | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| neither gate | 0.909328 | 1.0000 | 0.756095 | 1.875 |
| evidence gate alone | 0.928002 | 1.0000 | 0.833673 | 2.105 |
| confidence gate alone | 0.936614 | 1.0000 | 0.876048 | 2.310 |
| **both** | **0.942939** | **1.0000** | **0.902464** | 2.390 |

*(The shipped config then spends 0.0002 of that on behaviour rather than
score — see "A deliberate regression" below.)*

**We want to name this one honestly: part of that gain is a scoring artifact.**
Real shoppers do not vanish the moment they see a decent list — shown something
mediocre they keep talking, and you get another attempt for free. First-hit-break
is a measurement convention for time-to-first-success, not a model of customer
patience. Optimising against it *is* the task, but it is not evidence that users
would prefer an agent that stays quiet. That is exactly why the shipped
confidence threshold sits at the conservative low edge of its measured plateau
(τ = 0.054) rather than at its highest-scoring point (τ = 0.085, which scores
0.941314 on the full set but gains nothing on the held-out fold).

**4. Rank 2 was read by hand, product by product — and it closed off an entire
class of future work.** We read all 30 rank-2 sessions against exactly what the
customer had said at the turn that scored. In **0 of 30** did the ranker hold
separating information and still get the order wrong. 27 were separable only
with more disclosure than had yet arrived (a timing problem — finding 3), and 3
were genuine ties: two listings identical on every disclosed constraint, where
no feature, no weighting and no human reading the text could tell them apart.

This retired two things on evidence. First, the theory that popularity signal
was drowning constraint evidence — winner-is-more-popular was a coin flip
(15/30), and in several pairs the target is far more popular and loses anyway.
Second, **seven separate attempts at a learned reranker**: five linear
formulations, a regularised sklearn logistic regression, and a LightGBM
LambdaMART. None beat the tuned linear model held out. The best (LambdaMART)
reached −0.0080 — a tie — and eight configurations spanning `num_leaves` 2–15
and 15–200 trees produced a flat-to-worse curve, which is the signature of no
remaining signal rather than of undertuning. The feature vector already contains
what a rank-2 pair needs; there was nothing left to reweight.

**5. Target products are a popularity-biased subpopulation.** Median review
count is 6,846 for targets versus 12 for a random catalog row — AUC 0.956 on
that single feature. This is not a discovery about shoppers; it is a property of
how the benchmark was constructed. Targets are sampled from a 5-core
leave-last-out split of real purchase records, and real purchases concentrate on
popular items.

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

**6. Two correctness bugs found by auditing the catalog, not the leaderboard.**
Both were found by a "own-goal" test — score every one of the 50,000 rows against
constraints drawn from its *own* listing, and see how often a product violates
itself:

- **Gender hierarchy.** `kids` was coded as a sibling of `boys`/`girls` rather
  than their parent, so a customer saying "toddler" scored every boys'/girls'
  listing as VIOLATED — including the listing whose own category path produced
  the word "kids". 506 of 50,000 rows (1.01%) were taking that own goal. Fixed
  as a hierarchy; own-goal rate down to 180/50,000.
- **Brand false positives.** Single-word brand matching against 19,855 catalog
  store names fires on ordinary listing boilerplate. Measured live at the
  lock-in turn: a brand was extracted in 66 of 200 sessions and **62 were
  wrong** — driven by `wash`, `sole`, `hand`, `machine` out of "Machine Wash"
  and "Rubber sole". We deliberately did *not* hand-write a blocklist (it would
  fit these 200 sessions and not generalise); instead we gate single-word
  matches by measured catalog text-commonness, where real brands and boilerplate
  separate by two orders of magnitude (`skechers` 0.0077 vs `wash` 0.317).

**Both measured exactly zero effect on the public 200, and we adopted them
anyway.** That was the pre-registered pass condition, not a disappointment: the
private set is 800 unseen sessions, a ~0.6%-of-rows defect is ~5 sessions there,
and a fix tuned to make a public-set number move would be worth negative value.

Two properties of the data shaped everything else:

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

Measured on the 200 public development sessions with the unmodified official
evaluator (`evaluator/local_evaluator.py`).

| Configuration | HR@10 | MRR | MTTC | TechnicalScore |
|---|---|---|---|---|
| Official baseline | 0.125 | 0.068 | 9.81 | 0.1067 |
| Ours — default weights | 0.885 | 0.554 | 3.23 | 0.7641 |
| **Ours — shipped (`config/tuned.json`)** | **1.000** | **0.902464** | **2.400** | **0.942739** |

**8.8× the baseline TechnicalScore, zero misses across all 200 sessions**, and
turns-to-conversion down from 9.81 to 2.40.

### A deliberate regression

The shipped score is 0.0002 *below* our best measured configuration, and that
was a choice. `disclosed_ask_decay: 0.0` stops the agent asking about an
attribute the shopper has already disclosed — being asked "do you have a
material preference?" one turn after saying "polyester" reads as an agent that
is not listening.

We measured it first, and the measurement argued against it: across 200
sessions the gate fires on only 2 asks, and **both of them were productive**,
because the simulator discloses at most two constraint spans per reply and an
intent card can hold several of the same class. Enabling it costs 0.942939 →
0.942739, identical at every strength from 0.45 down to 0.0, entirely as one
extra turn of MTTC on one session — MRR is byte-identical and the held-out
fold does not move at all.

We took it anyway. 0.0002 is two orders of magnitude below this set's ~0.029
standard error, so it is not a difference the benchmark can even resolve,
whereas an agent that visibly ignores what the shopper just said is a defect
any person can see in one turn. We would rather report that trade honestly
than quietly keep the higher number.

**Read that as in-sample.** Roughly 36+ configurations have now been scored
against these same 200 sessions, so we validate every change on
`stratified_halves(seed=7)` and quote the conservative held-out fold: the
evidence gate is +0.0193 there and the confidence gate +0.0259. The
*combination* has not itself been fold-split, so 0.942939 is the honest
full-set number, not a private-set forecast. Single-run standard error on 200
sessions is ~0.029; the private 800 has roughly half that.

We also measured the ceiling this leaves, because it changes what is worth
attempting next. MRR wants more disclosure and MTTC wants fewer turns, and
because the evaluator breaks on first hit they are in *direct* opposition:
even the physically impossible "all information disclosed for free on turn 1"
row tops out at 0.9585, and realistic perfect play on disclosure timing is
**~0.947–0.954**. We had been carrying an internal target of 0.97; that
measurement retired it.

### What each component is worth

We switched off each component in turn and re-ran all 200 sessions against the
shipped configuration. `delta` is what that component is worth: a large
negative number means removing it hurts, so it is carrying real load; a
positive number means the system scored slightly *better* without it.

Full table also in `docs/ablations_tuned.md`; regenerate with
`python -m tools.ablate --config config/tuned.json`.

Read the small rows with the noise floor in mind — one session is 0.5 points of
HR@10, so treat anything inside roughly ±0.01 as "no measurable effect" rather
than as a ranking of the minor components. Each row is a single run, not a
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
| constraint scoring | 0.995 | 0.8928 | 2.43 | 0.9368 | −0.0060 |
| coverage + category focus | 1.000 | 0.8780 | 2.29 | 0.9375 | −0.0052 |
| semantic route | 0.995 | 0.9027 | 2.46 | 0.9391 | −0.0036 |
| low-coverage penalties | 1.000 | 0.8990 | 2.40 | 0.9417 | −0.0010 |
| *added:* MMR diversity | 1.000 | 0.9025 | 2.40 | 0.9427 | +0.0000 |
| *added:* re-ask disclosed attributes | 1.000 | 0.9025 | 2.39 | 0.9429 | +0.0002 |
| constraint commonness penalty | 1.000 | 0.9058 | 2.42 | 0.9434 | +0.0007 |
| profile personalisation | 1.000 | 0.9072 | 2.42 | 0.9438 | +0.0010 |

**Clarification dominates everything else by a factor of five.** Removing it
costs 37 points of HR@10. That is the finding we would keep if we could keep
only one.

**The recommendation hold row doubles as a check on the table.** Switching both
gates off lands at 0.9091 — reproducing 0.909328, the pre-gate score we had
measured independently, by a completely different route, much earlier. The
shape of the trade is visible in the row: MRR collapses 0.9025 → 0.7561 while
MTTC *improves* to 1.89. Answering sooner is genuinely faster and genuinely
worse.

**Two results contradict what we wrote in earlier versions of this document.**
We are flagging them rather than quietly correcting them, because the earlier
claims were right for the build they were measured on:

- We said **per-field lexical weighting did not earn its place** (+0.0007 on the
  pre-tuning build). On the shipped build it is **−0.0128**. That reading was
  taken before the tuner had differentiated the per-field weights at all.
- **Popularity priors roughly tripled in importance**, −0.0230 → **−0.0744**,
  and are now the second-largest component — the bill for tuning
  `w_log_rating_number` from 0.15 to 0.88. This one deserves stating plainly,
  because it is the component we are least comfortable defending: the part of
  the system that exploits a property of how the benchmark was *built* rather
  than modelling shoppers has become **more** load-bearing over time, not less.
  Everything in the popularity caveat above applies with more force than when
  we first wrote it.

**Three components still do not earn their place, and we would rather say so
than imply otherwise.** Profile personalisation (+0.0010 — the identical figure
it scored on the pre-tuning build, two independent measurements 0.18 points
apart agreeing), MMR diversity (+0.0000 for the third separate time, which is
why it ships disabled), and the constraint commonness penalty (+0.0007). The
last is a supersession rather than an error: it was adopted with held-out
evidence to stop boilerplate query terms diluting retrieval, and the
conjunctive injection and `span_all` we built afterwards address the same
failure more directly, leaving it nothing to do.

**One component has no row, and we would rather admit that than estimate it.**
The conjunctive injection is unconditional in the code with no enable flag, so
it cannot be switched off from configuration — measuring it would mean
reverting code. Its contribution was measured when it was adopted: HR@10 0.995
→ 1.000, and the count of targets that never reach the candidate pool at all
going 1 → 0.

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

**The EAR clarification gate** (Lei et al., WSDM 2020, arXiv 2002.09102): ask
only when the candidate space is small enough, the question still carries
information gain against user patience, and the recommender is not yet
confident. We did *not* implement the RL policies that succeeded it (SCPR, arXiv
2007.00194; UNICORN, arXiv 2105.09710) — they assume clean per-item attribute
sets, and our audit showed this catalog has none.

**Confidence, quantified: Normalized Query Commitment** (Shtok et al., 2009).
The statistic EAR treats qualitatively — "is the recommender confident" — is
computed explicitly as `std(top-10 scores) / |top|` and used both to gate asking
and to gate showing a list. One caution we recorded while building it: *a gate
set against the wrong empirical range is silently unreachable rather than loudly
wrong.* Our ask gate sat at 0.82 while the entire observed NQC range is
[0.011, 0.194] — it had never once fired, and nobody noticed until the range was
measured.

**Withholding results while asking: the literature disagrees with itself.** One
[empirical study of clarifying-question e-commerce systems](https://arxiv.org/pdf/2008.00279)
found users tolerate 11.4 questions per product, so turn budget is not the
binding constraint. But [three controlled experiments on withheld information](https://pmc.ncbi.nlm.nih.gov/articles/PMC11008880/)
(n = 1,811 / 905 / 801) found conversational withholding scores *worse* than the
same withholding in an ordinary UI — mitigated specifically by showing results
*alongside* the question, which a hold gate by definition stops doing. We built
the gate because the effect is measurable per-session rather than
population-average, and we set it at the conservative edge of its plateau
because of this literature, not despite it.

**Tree ensembles for tabular ranking.** Grinsztajn et al. (arXiv 2207.08815)
find GBDTs frequently outperform deep models at this data scale, and that
feature engineering rather than model class sets the ceiling. We tested that
claim directly rather than citing it: LightGBM `lambdarank` was measured against
six other learned formulations and none beat the tuned linear model held out —
consistent with the paper's point that the ceiling is set by features, not model
class, once the features stop changing.

**Anchor-based CRS framing.** PSCon (arXiv 2502.13881) notes e-commerce
conversational recommendation is typically anchor-based, simulated from
predefined intent slots — exactly the structure of the TechJam simulator, and
the reason slot-based routing beats free-form NLU here.

## Development tools

- VSCode with the Python extension
- Python 3.12 virtual environment (`venv`)
- Git / GitHub for version control

## APIs used

**None.** The agent makes no external API calls. All ranking is deterministic
and runs in-memory, so there is no per-session token cost, no rate limit, and no
network dependency at evaluation time. The evaluator's reported token usage for
a full 200-session run is 0 prompt / 0 completion.

Disclosure figures: **$0.00 estimated cost, 0 tokens.** A one-off index build
takes ~20 s; a full 200-session evaluation run takes ~3–4 minutes on a laptop
(~1 s per session, dominated by the conjunctive-injection catalog scan).

## Libraries and frameworks

**Python standard library only — no third-party dependencies.** This is
deliberate: submission rules warn that organizer policy may disable network
access for final scoring, so there is no package to install and no model to
download. `requirements.txt` is intentionally empty of packages.

Modules used: `json`, `re`, `math`, `array`, `collections`, `dataclasses`,
`functools`, `pathlib`, `os`, `typing`, plus `statistics`, `random`,
`argparse` and `unittest` in the tooling and tests. (`sqlite3` appears once, in
the organizer's original weak-BM25 baseline, which we keep in the repo for
comparison — nothing on the agent's turn path uses a database.)

The BM25 inverted indexes, the character-n-gram semantic index, the fusion
layer, the feature extractor, the linear ranker, and the coordinate-ascent tuner
are all implemented from scratch. 60 unit tests cover the agent and our reading
of the evaluator.

**The Buyte demo UI is separate and does not touch the graded path.**
`frontend/` is an optional presentation layer — a FastAPI wrapper plus a React
storefront that replays real evaluator sessions turn by turn — calling the same
unmodified `starter.agent.Agent`. It has its own dependencies and its own
deployment, and contributes nothing to `TechnicalScore`.

## Datasets and assets

- **TechJam frozen catalog** — 50,000 products from the
  `Clothing_Shoes_and_Jewelry` category, provided by the organizers
- **200 labelled public development sessions** — used for tuning and validation
- Both derived from **Amazon Reviews 2023** (McAuley Lab, UCSD)
- No external datasets, no manually labelled data, no scraped content

## What we would do with more time

**Push the recommendation hold past the conservative edge.** The gap between the
shipped threshold (0.9366 at τ = 0.054) and the sweep's best public-set point
(0.9413 at τ = 0.085) is real, but it lives entirely in the fitted fold — the
held-out fold is tied. We would take that on private-set feedback or as a
per-intent threshold, not on the public set's say-so.

**Bridge the attribute vocabulary gap.** The nine profile `preference_tags` are
abstract — fit, material, comfort, style, durability, performance, warmth,
weather, general shopping — and map onto no catalog field. Learning
tag-to-product affinities from the labelled sessions is the most promising
direction we did not get to.

**Replace the popularity prior with real personalisation.** For any use outside
this benchmark, that is the necessary change — see the honest caveat above.

## Limitations

- **0.942739 is in-sample.** 36+ configurations have been scored against these
  200 sessions. Every adopted change carries a held-out fold number, but the
  shipped *combination* does not, and the held-out fold itself has now been
  looked at enough times to have lost some of its independence. Expect the
  private 800 to come in lower.
- **HR@10 = 1.000 is a property of this sample, not a guarantee.** One session
  (`public_0020`) is visible on exactly one turn at NQC 0.0546, between the
  shipped threshold and the next value up. We expect to lose ~0.5% of sessions
  to that on the private 800; it is already priced into the held-out number.
- **Part of the recommendation-hold gain is a scoring artifact**, as described
  above. It optimises the evaluator's first-hit-break convention, which is not
  a model of shopper patience.
- **The "dense" route is not neural** — it is a character-n-gram index, chosen
  so the system survives an offline scoring run. It buys tolerance to morphology
  and spelling drift, not semantic generalisation: "something elegant for a
  dinner date" will not reach a listing that never says "elegant".
- **No learned reranker shipped**, and this is a measured decision rather than a
  constraint: seven approaches including LightGBM LambdaMART were built and
  tested, and none beat the tuned linear model held out.
- **Three of the 200 sessions are genuine ties** — the target and its competitor
  are indistinguishable on every disclosed constraint, and the answer key is
  arbitrary between them. That is a floor no amount of ranking work removes.
- **The popularity prior is a benchmark property** and would not transfer to
  production.
