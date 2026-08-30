# Shopping Copilot — Agent Architecture

**Status:** design doc. Ownership marked per component.
**Scoring target:** TechnicalScore = 0.50·HR@10 + 0.30·MRR + 0.20·Efficiency
**Baseline to beat:** HR@10 12.5%, MRR 0.068, MTTC 9.81

---

## 0. Design principles

Three claims drive every decision below.

**P1 — 80% of the score lives in the returned list.** HR@10 and MRR are both
determined entirely by `ordered_recommendations`. Efficiency is 20% and partly
capped. Ranking quality is the bottleneck, not dialogue quality.

**P2 — The catalog is sparse in exactly the fields the problem statement
assumes.** Price is null on 78.9% of rows; `details.Color` exists on 4.9%,
`Size` on 1.9%, `Material` on 4.1%. Structured attribute filtering is mostly
not available. Anything that deletes candidates on a missing field will delete
the target.

**P3 — Targets are a popularity-biased subpopulation, by construction.** The
benchmark samples real purchases from a 5-core leave-last-out split, so targets
concentrate on heavily-reviewed products. Median target `rating_number` is
6,846 vs 13 for a random catalog row (AUC 0.955). This is a property of the
label-generation pipeline, not of shopper psychology — see §8 for how to frame
it.

---

## 1. Intent Router — *owner: dialogue*

**Does:** classifies each turn as BUYING / BROWSING / UNCERTAIN, which selects
the retrieval weight profile downstream.

**Grounding:** PSCon (arXiv 2502.13881) notes existing e-commerce CRS work is
usually anchor-based — conversations simulated from predefined intent slots,
entities, and attributes. That is exactly what the TechJam simulator does, which
means slot-based routing is the right abstraction rather than free-form NLU.

**Build:** the hybrid scorer already drafted by the team — constraint density,
linguistic markers, slot specificity, profile alignment. Thresholds 0.65 / 0.35.

**Do not** use an LLM call for this. It is a 4-feature linear decision, it runs
every turn, and it must be deterministic.

**Priority:** medium. Routing changes the weight profile; it does not change
whether the target is retrievable.

---

## 2. State Manager — *owner: dialogue*

**Does:** maintains `ShoppingState` across turns; accumulates slots, handles
override (erasure + rewrite).

**Grounding:** multi-round conversational recommendation (MCR) as formalised in
SCPR (arXiv 2007.00194) and UNICORN (arXiv 2105.09710) — the system alternates
between asking about attributes and recommending, updating candidate item and
attribute sets after each user response.

**Caveat that matters:** that literature assumes each item carries a clean
attribute set and the user holds definite preferences over all attributes. Per
P2, this catalog does not have clean attributes. The state machine is still
correct; the *entropy-over-attributes* machinery those papers build on top of it
is not directly transferable. See §5.

**Build:** slot dict + override markers ("actually", "instead", "rather",
"no, I want") + semantic contradiction check. 15% of sessions are
intent_override, so this is worth real effort.

**Priority:** high. Override sessions are 30 of the 200 public sessions and
cannot score before the changed intent appears.

---

## 3. Retrieval — *owner: contested, see §9*

Three routes, fused.

**Route A — Lexical (BM25/FTS5), per-field.** Separate indexes for title,
features, categories. Do *not* concatenate fields. Titles are short and clean
(median 12 tokens, mean 12.6, p95 22) with genuine IDF range, so title BM25 is
a stronger signal than the weak baseline suggests. `description` is empty on
47.8% of rows and `features` on 10.4% — carry `has_description` / `has_features`
as flags rather than letting sparse fields silently penalise.

**Route B — Dense.** Sentence embeddings over title + categories. Earns its
place mainly on BROWSING ("something elegant for a dinner date"), where the
query terms won't appear literally.

**Route C — Structured.** Only two fields have coverage worth filtering on:

| field | coverage | use |
|---|---|---|
| `details.Department` | 87.2% | gender — normalise `womens`/`women`, `unisex-adult`/`unisex adult` |
| `store` | 99.4% | brand, 19,855 distinct (Nike 564, adidas 438, Skechers 375) |
| `categories` | 100% | soft boost only — 800 leaves, contains junk values |

Gender is the highest-elimination-power attribute in clothing and it is one of
the few that is genuinely available. Fall back to title tokens for the 13%
missing (`women` appears in 42% of titles).

**Fusion: convex combination, not RRF.** Bruch et al. (arXiv 2210.11934) find
RRF sensitive to its parameters and poorly generalising out-of-domain, CC
outperforming RRF in-domain and out-of-domain, CC largely agnostic to the choice
of score normalisation, and CC sample-efficient — needing only a small set of
training examples to tune its single parameter. With 200 public sessions,
"one parameter, tune on a small sample" is the deciding property.

```
score = α · bm25_norm + (1-α) · dense_norm      # tune α on the 200
```

Use theoretical min-max normalisation (TM2C2). Note the paper studied fusing
*two* retrievers and explicitly leaves three-or-more to future work — if you
fuse three routes, tune it, don't assume it.

**Candidate depth: 100+, not 20.** A 2026 retrieval benchmark found that with
only 20 candidates reranking is ineffective (Recall@5 0.458) because relevant
documents are often not in the pool. Recall lost in stage one cannot be
recovered downstream. Retrieve 150–200, rerank 100.

---

## 4. Feature Scorer / Reranker — *owner: ranking.py*

The core of the system. Two sub-stages.

### 4a. Feature extraction

Emit a fixed vector per candidate. Keep this a standalone pure function so it
can be replayed offline over logged sessions for training (§7).

**Retrieval features:** `bm25_title`, `bm25_features`, `bm25_categories`,
`dense_sim`, `fused_score`

**Prior features (the empirical edge):**
- `log1p(rating_number)` — AUC 0.955 alone. Strongest single feature in the system.
- `has_price` — 89% of targets vs 21% of catalog
- `n_features` — 7.7 for targets vs 5.0 for catalog
- `average_rating`, Bayesian-shrunk toward the global mean — weak (AUC 0.606), keep only if it survives CV

**Constraint features (three-way, never binary):** for gender, brand, category,
price — emit `satisfied` / `violated` / `unknown`. Per P2, `unknown` must be a
mild penalty, never an exclusion.

**Context features:** `category_match`, `use_case_match`, `scenario_match`,
`profile_tag_affinity`

### 4b. Scoring model

**Start:** hand-weighted linear sum over normalised features. Ship this Day 3.

**Upgrade:** LightGBM with `lambdarank`, grouped by session. Grinsztajn et al.
(arXiv 2207.08815) and the surrounding benchmarks find tree ensembles frequently
outperform deep models on tabular data, with deep learning held back by
data-size and feature-quality constraints — and, importantly, that feature
engineering and dataset characteristics typically set the performance ceiling
rather than model class. Two consequences: GBDT is the defensible choice at 200
sessions, and *the features are the contribution*, not the model.

Trees also split on missingness natively, which matters when price is null on
78.9% of rows — a linear model would need that imputed, inventing signal.

**Constrain hard:** `num_leaves` 7–15, high `min_data_in_leaf`, ≤20 features,
5-fold CV grouped by session. If LambdaRank does not beat tuned-linear on
held-out folds, ship linear and say so.

**Determinism:** tie-break on `parent_asin` so runs reproduce.

---

## 5. Clarification Policy — *owner: dialogue*

**Grounding: the EAR gate (Lei et al. 2020).** Ask only when (1) the candidate
space is small enough, (2) further questions are still useful from an
information-gain or user-patience perspective, and (3) the recommender is not
yet confident the top results will be accepted.

**What not to lift:** SCPR, UNICORN, CoCHPL and the rest of that line are RL
policies over knowledge graphs, assuming clean per-item attribute sets. Per P2
you do not have those. Compute information gain only over `categories`,
`Department`, and title-derived tokens where coverage is real. Simple
entropy-based attribute selection appears in that literature mainly as a
baseline the RL methods beat — it will be shakier here than there.

**Turn-budget override:** at turn ≥ 8, stop asking and answer. Running out of
turns costs Efficiency and risks the session entirely.

---

## 6. Diversity — *owner: ranking.py, browsing only*

MMR on positions **2–10 only**, BROWSING track only.

Rationale: every diversity swap that demotes the true target costs MRR directly,
and MRR is 0.30 of the score. Position 1 is never diversified.

Near-duplicate clusters are rarer than assumed — only 4.8% of catalog rows fall
in a multi-row cluster (first-8-token shingling), max cluster size 6, 90% of
clusters are pairs. So the top 10 will not naturally fill with duplicates, which
makes MMR *less* necessary than in a typical e-commerce setting. Measure whether
it helps before keeping it.

---

## 7. Training loop — *owner: ranking.py, blocked on simulator*

`public_set.jsonl` contains no dialogue — only a 5-field `user_profile` and the
ground-truth target. Customer turns are generated at runtime by the simulator.

**Therefore:** you cannot train until the simulator runs. Sequence is:

1. Simulator produces sessions
2. Log `(session_id, candidate_asin, feature_vector, label)` per turn
3. Freeze the retriever
4. Train LambdaRank on the logged tuples

Step 3 matters: negatives are mined from your own retrieval output, so the model
learns to correct *that specific retriever*. Change retrieval after training and
the model is stale.

---

## 8. Evaluation & framing

**Tune on:** the 200 public sessions via the official local evaluator. Split-half
CV, grouped by session.

**Ablation table for the writeup** — one row per component, showing HR@10 / MRR /
MTTC. This is what makes Technical Execution (35%) legible to judges.

**On the popularity prior.** State plainly in the README and demo that
popularity-as-feature is correct for this benchmark and wrong for a production
store: the labels were generated by sampling real purchases, which concentrate on
popular items, and a deployed system ranking this way would starve the long tail.

Reference points: ranking by popularity alone scores HR@10 3.5% (it returns Crocs
and Hanes boxer briefs to every user — it is query-blind), against the 12.5% BM25
baseline. The gain comes from combining relevance with the prior, not from either
alone. Pruning to `rating_number ≥ 25` retains 97.5% of targets while cutting the
catalog to 37.9% — usable as a soft prior, **not** as a hard filter, since ~5% of
targets sit below the popular tail and one has a single review.

Saying this out loud converts the strongest feature from a criticism into evidence
of problem insight — Innovation (20%) and Impact & Relevance (20%) both reward it.

**Do not build:** popularity debiasing or calibrated recommendation. That
literature exists to correct a bias these labels contain by construction.
Applying it means fighting the metric.

---

## 9. Open decisions

**D1 — Retrieval boundary.** Does `ranking.py` call the index (owning BM25
weighting, fusion, gender filter), or does it rerank candidates handed to it by
`agent.py`? Argument for the former: the popularity prior is strongest at
candidate-selection time; if retrieval discards 95% of the catalog first, target
presence in the surviving pool is out of the ranker's hands.

**D2 — Emit recommendations on ask-turns?** Nothing in the response schema makes
`ask_attribute` and `ordered_recommendations` mutually exclusive, and first-hit
turn drives MTTC. Every turn returning nothing is a discarded chance at the hit.
Dialogue's call, ranking's output — decide jointly.

**D3 — LLM reranker.** Listwise beats pointwise (LRL, arXiv 2305.02156), but LLM
rankers are order-sensitive, which is why permutation self-consistency exists —
a determinism problem for a graded submission. If used: BROWSING only, shuffle
and aggregate across permutations, and keep the deterministic constraint layer
with final say.

---

## 10. Build order

| Day | Work |
|---|---|
| 1 | Starter kit runs; per-field BM25 indexes; local evaluator reproducing baseline |
| 2 | State machine + intent router; `ShoppingState`; agent contract stub |
| 3 | Feature extractor + linear scorer with popularity prior. **Valid submission exists.** |
| 4 | Dense route + convex fusion (tune α); gender filter; clarification gate |
| 5 | LambdaRank if data supports it; ablations; README; demo video |

Cut line: if Day 5 is tight, ship the Day-3 linear scorer with the Day-4 fusion
and skip LambdaRank. A well-featured linear model with the popularity prior will
not embarrass you.
