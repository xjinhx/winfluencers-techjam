# Buyte
### The shopping agent that knows when to ask — and when to wait

**TikTok TechJam 2026 · Track 4: Shopping Copilot — AI Conversational Search and Recommendations**

A multi-turn agent that finds one hidden target product in a 50,000-item Amazon
clothing catalog, deciding on every turn whether to ask, stay silent, or
recommend. **8.8× the official baseline. Zero misses across all 200 public
sessions. Zero LLM calls, zero external dependencies, $0.00 cost.**

*[Suggested hero image/GIF: a terminal or the `frontend/` storefront replaying
`tools.demo --sample public_0002` — a full multi-turn transcript showing the
agent asking, holding, then recommending correctly.]*

---

## Inspiration

Keyword search breaks down in conversation for a reason that's easy to state
and hard to fix: it has no memory and no theory of intent. "Under $40" and
"less than $40" are different strings to BM25. A customer who says "I need
shoes for a trip" and then "waterproof, under $80" has given two halves of one
request that a stateless index will never join.

The competition frames this as three sub-problems — routing intent, tracking
state, ranking results — weighted into one score:

```
TechnicalScore = 0.50 × HR@10 + 0.30 × MRR + 0.20 × Efficiency
```

Reading that formula gave us our first hypothesis: HR@10 and MRR are both
determined by the returned list, so 80% of the score lives in *ranking*
quality. The provided baseline scores 12.5% HR@10 — looked like a ranking
failure, not a dialogue failure.

**That reading was half wrong, and finding out how was the most useful thing
that happened to us.** Ranking is where the score is *counted*, but on this
benchmark dialogue is where it is *created*: without a question, no new
information arrives, and there's nothing better to rank. A third thing turned
out to matter even more than either: *when* you show the list at all. Those
three findings are what this project is actually about.

## What it does

Every turn runs one deterministic pipeline, entirely in-memory:

```
parse → route intent → retrieve (lexical + semantic, fused, plus
conjunctive injection) → rerank → ask, hold, or recommend
```

- **Stateful slot tracking** — a `ShoppingState` accumulates constraints
  across turns and *demotes* (never deletes) a retracted preference, so a
  withdrawn constraint is never re-checked but still describes the region of
  the catalog in play.
- **Five per-field BM25 indexes** (title / features / categories /
  description / store) — never concatenated, because a term matching in a
  12-token title means something different from the same term in 400 words
  of description — fused with a character-n-gram dense route by convex
  combination.
- **Conjunctive exact-substring injection** — any product whose text contains
  *every* live constraint verbatim enters the candidate pool regardless of
  BM25 rank. This is the one route depth alone can't provide; it took the
  count of targets that never reach the pool from 1 to **0**.
- **A 40-feature intent-conditional linear reranker** — per-field scores,
  semantic similarity, phrase overlap, three-way constraint satisfaction
  (satisfied / violated / **unknown**, since price is null on 78.9% of the
  catalog), and `span_all` — does this candidate satisfy *every* disclosed
  constraint, not just some.
- **Two independent gates deciding whether to ask, stay silent, or recommend**
  — this is where most of the final score actually came from. See below.

## The findings that made the difference

**1. Clarification is the system, not a refinement to it.** Removing it costs
**−0.3892 TechnicalScore** on our shipped build — 37 points of HR@10, more
than five times any other component. Our first working version scored 0.3167
because the clarification gate was being handed the *returned* top-10 instead
of the full candidate pool, so its "is there still room to narrow?" test
rejected every turn and the agent never asked anything. One plumbing fix
moved us from 0.3167 → 0.7641.

**2. Asking well matters as much as asking.** Information-gain-by-the-book
picks `category` and `brand` — they split the catalog beautifully, and this
customer answers them **0.000%** of the time. We measured disclosure per
attribute across all 200 sessions instead of assuming it (feature 91.5%,
material 72.5%, color 24.5%, brand/budget/category 0.0%) and rebuilt expected
gain as `P(answered) × uncertainty removed`, with the first term measured, not
guessed.

**3. The evaluator locks in the first hit — so *when* you answer decides your
rank, and this was our single largest gain.** The official evaluator ends a
session the first turn its target appears in the top 10. Measured directly:
**45 of 75 sub-rank-1 sessions locked in on turn 1**, before the customer had
disclosed anything beyond a category name — and every one of them would have
reached rank 1 by turn 2–3 if the agent had just waited. The exchange rate
strongly favors patience: one extra turn costs ~0.0001 of score; a rank 2→1
recovery pays ~0.00075 — **7.5× more**. So we built two independent gates —
one asking *has the customer said anything concrete*, the other *has the
ranker actually committed* (Normalized Query Commitment, Shtok et al. 2009) —
that compound because they read different signals:

| configuration | TechnicalScore | MRR |
|---|---|---|
| neither gate | 0.9093 | 0.756 |
| evidence gate alone | 0.9280 | 0.834 |
| confidence gate alone | 0.9366 | 0.876 |
| **both, shipped** | **0.9429** | **0.902** |

We want to name this honestly: part of that gain is a scoring artifact — real
shoppers don't vanish the moment they see a mediocre list, they keep talking.
That's exactly why our shipped confidence threshold sits at the conservative
edge of its measured plateau (τ = 0.054) instead of its highest-scoring point
(τ = 0.085, which scores higher on the public set but gains nothing held out).

**4. Rank-2 was read by hand, product by product — and it closed off an
entire class of future work.** Across all 30 rank-2 sessions, in **0 of 30**
did the ranker hold separating information and still get the order wrong. 27
needed disclosure that hadn't arrived yet (a timing problem — see #3); 3 were
genuine ties, indistinguishable on every disclosed constraint. This also
retired **seven separate reranker attempts** — five linear formulations, a
regularized logistic regression, and a LightGBM LambdaMART model. None beat
the tuned linear model held out; the feature vector already contained
everything a rank-2 pair needed.

**5. Target products are a popularity-biased subpopulation — and we're
saying plainly that this is right for the benchmark and wrong for a real
store.** Median review count is 6,846 for targets vs. 12 for a random catalog
row (AUC 0.956 on that single feature). We use it as a soft prior, never a
filter — popularity alone scores 3.5% HR@10, *worse* than the baseline,
because it's query-blind. The lift only comes from combining relevance with
the prior; a deployed system ranking this way would starve the long tail.

**6. Two correctness bugs, found by auditing the catalog, not the
leaderboard.** A "own-goal" test — score all 50,000 rows against constraints
drawn from their *own* listing — found gender coded as a flat sibling
hierarchy (`kids` was a sibling of `boys`/`girls` instead of their parent, so
"toddler" scored every boys'/girls' listing VIOLATED, including the listing
that produced the word "kids"), and single-word brand matching firing on
listing boilerplate (extracted a "brand" in 66 of 200 sessions — **62 wrong**,
driven by `wash`, `sole`, `hand` out of "Machine Wash" and "Rubber sole"). Both
measured **exactly zero effect on the public 200** and we adopted them anyway
— that was the pre-registered pass condition, because a ~0.6%-of-rows defect
matters on the private 800 even when it's invisible here.

## Results

Measured on the 200 public development sessions with the unmodified official
evaluator.

| Configuration | HR@10 | MRR | MTTC | TechnicalScore |
|---|---|---|---|---|
| Official baseline | 0.125 | 0.068 | 9.81 | 0.1067 |
| Ours — default weights | 0.885 | 0.554 | 3.23 | 0.7641 |
| **Ours — shipped, tuned** | **1.000** | **0.902** | **2.40** | **0.9427** |

**8.8× the baseline TechnicalScore. Zero misses across all 200 sessions.**
Turns-to-conversion down from 9.81 to 2.40.

## Accomplishments we're proud of

- **8.8× the official baseline**, with every one of 200 public sessions
  finding its target — a claim we didn't just measure once, we fold-split it:
  the merged recommendation gates land **+0.029 on the held-out half**, above
  our own noise floor, so this isn't an in-sample number dressed up as a real
  one.
- **Turned down a higher score on purpose.** `disclosed_ask_decay: 0.0` costs
  us 0.0002 TechnicalScore — two orders of magnitude below this set's
  standard error — because it stops the agent asking about something the
  shopper already told it. We'd rather report that trade honestly than ship
  the number that looks better.
- **Zero external dependencies, zero LLM calls, $0.00 cost.** Every ranking
  decision is deterministic, standard-library Python — a deliberate hedge
  against the organizer's warning that final scoring may run offline.
- **Caught two correctness bugs the public leaderboard could never have shown
  us**, by auditing all 50,000 catalog rows against themselves rather than
  against the 200 sessions we're scored on.
- **Retired seven learned-reranker attempts on evidence**, including a
  LightGBM LambdaMART model, rather than shipping a marginal or overfit one.

## Honesty & limitations

We'd rather state these plainly than let a good headline number hide them.

- **0.9427 is in-sample.** 36+ configurations have now been scored against
  these same 200 sessions. Every *individual* change we adopted carries a
  held-out fold number; the shipped *combination* has one fold-validation
  pass, not an independent one for every layer. Expect the private 800-session
  set to land a little lower.
- **Part of our biggest gain is a scoring artifact.** Holding back weak
  recommendations wins because the evaluator breaks on first hit — a
  measurement convention for time-to-first-success, not a model of real
  shopper patience. Optimizing against it *is* the task, but it isn't
  evidence users would actually prefer a quieter agent.
- **The "dense" semantic route is character-n-grams, not neural embeddings** —
  a deliberate trade for an offline, no-network-access scoring run. It buys
  spelling/morphology tolerance, not semantic generalization: "something
  elegant for a dinner date" won't reach a listing that never says "elegant."
- **The popularity prior is a property of how this benchmark's labels were
  built, not a discovery about shoppers**, and we would not ship it to a real
  store without replacing it with real personalization.
- **Three of the 200 sessions are genuine ties** we can't break — the target
  and its competitor are identical on every disclosed constraint. That's a
  floor no amount of ranking work removes.

## What's next

- **Push the recommendation-hold threshold past its conservative edge** —
  but only with private-set feedback or a per-intent variant, not on the
  public set's say-so; the extra headroom there lives entirely in the fitted
  fold.
- **Bridge the profile-tag vocabulary gap.** Our nine `preference_tags`
  (fit, material, comfort, style, durability, performance, warmth, weather,
  general shopping) map onto no catalog field today; learning tag-to-product
  affinities from labeled sessions is the most promising direction we didn't
  get to.
- **Replace the popularity prior with real personalization** — the necessary
  change for any use outside this specific benchmark.

## Development tools

VSCode with the Python extension · Python 3.12 (`venv`) · Git/GitHub

## APIs used

**None.** The graded agent makes no external API calls — no per-session token
cost, no rate limit, no network dependency at evaluation time. Reported token
usage for a full 200-session run: 0 prompt / 0 completion.

## Libraries and frameworks

**Python standard library only** on the graded path (`json`, `re`, `math`,
`array`, `collections`, `dataclasses`, `functools`, `pathlib`, `typing`) — the
BM25 indexes, the semantic route, the fusion layer, the feature extractor, the
linear ranker, and the coordinate-ascent tuner are all implemented from
scratch, covered by 60 unit tests. A separate, optional presentation layer
(`frontend/`) — FastAPI + React/TypeScript/Vite — replays real evaluator
sessions turn by turn through the same unmodified agent; it doesn't touch
`TechnicalScore`.

## Datasets and assets

- **TechJam frozen catalog** — 50,000 products, `Clothing_Shoes_and_Jewelry`
  category, provided by the organizers
- **200 labeled public development sessions** — used for tuning and
  validation only
- Both derived from **Amazon Reviews 2023** (McAuley Lab, UCSD)
- No external datasets, no manually labeled data, no scraped content

## Built With

`python` `bm25` `information-retrieval` `linear-regression` `fastapi`
`react` `typescript` `vite`

## Try it out

[GitHub Repo](https://github.com/xjinhx/winfluencers-techjam)
