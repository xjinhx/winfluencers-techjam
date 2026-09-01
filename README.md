# Shopping Copilot — Conversational Search & Recommendation Agent

**TikTok TechJam 2026 · Track 4: Shopping Copilot — AI Conversational Search and Recommendations**

A multi-turn shopping agent that finds a hidden target product in a 50,000-item
Amazon clothing catalog, deciding on every turn whether to ask a clarifying
question or return a ranked list. Retrieval is five per-field BM25 indexes
fused with a character-n-gram dense route; ranking is a 40-feature linear
model with intent-conditional weights; the whole turn path is deterministic
standard-library Python — no LLM call, no network access, no third-party
dependency — because the organizer's rules warn that official scoring may
run offline and a graded submission has to return the same answer twice.

Full design rationale, the fold-validated measurement history, and the
literature this draws on live in [`agent_architecture.md`](agent_architecture.md),
[`docs/report.md`](docs/report.md), and [`CLAUDE.md`](CLAUDE.md) — this file
covers what's needed to run it, reproduce the score, and know its limits.

## Results

All numbers below were re-measured directly against this checkout — the
unmodified `evaluator/local_evaluator.py`, all 200 public sessions — rather
than copied forward from an earlier draft.

| Configuration | HR@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---|---|---|---|---|
| Official weak-BM25 baseline | 0.125 | 0.0680 | 9.81 | 0.119 | 0.1067 |
| **This build, `config/tuned.json`, live** | **1.000** | **0.9025** | **2.39** | **0.861** | **0.9429** |

`TechnicalScore = 0.50·HR@10 + 0.30·MRR + 0.20·Efficiency`. 8.8× the provided
baseline; zero of 200 sessions miss the target entirely.

| scenario | n | HR@10 | MRR | MTTC |
|---|---|---|---|---|
| buying | 80 | 1.000 | 0.9056 | 1.825 |
| browsing | 80 | 1.000 | 0.9110 | 2.388 |
| intent override | 30 | 1.000 | 0.8806 | 3.700 |
| boundary | 10 | 1.000 | 0.8750 | 3.000 |

This is the **public 200-session** development score, used for tuning — it is
optimistic by construction. The organizer's private 800-session set is what
actually confirms it; see [Limitations](#limitations-and-what-wed-improve).

---

## How it works, briefly

Each turn: parse the utterance into slots (`state.py`, handling intent
override as decay rather than erasure) → route BUYING / BROWSING / UNCERTAIN
(`intent.py`) → retrieve via fused lexical + dense search, topped up by a
conjunctive pass that injects any candidate whose text contains every
disclosed constraint verbatim (`index.py`, `dense.py`, `fusion.py`,
`agent.py`) → score 40 features per candidate with intent-conditional
weights (`features.py`, `ranking.py`) → decide whether to ask a clarifying
question or return the ranked list, holding a weak list back rather than
showing it before the ranker has actually committed, since the evaluator
locks in whatever rank is shown on the first hit (`clarify.py`, `agent.py`).

The single biggest lever in the system is the clarification policy itself:
removing it costs **−0.4473 TechnicalScore** in ablation (`docs/ablations.md`,
`python -m tools.ablate`) — an order of magnitude more than any other
component, because a browsing session that opens with just a category and no
constraints never gets new information to rank on if the agent never asks.

Two properties of the data shaped the design: structured fields the problem
statement assumes (price, color, material, size) are missing on 78.9% / 95.1%
/ 95.9% / 98.1% of the catalog, so constraints are scored three-way
(satisfied / violated / **unknown**, never an exclusion); and targets are a
popularity-biased subpopulation by construction of how the benchmark was
built (median `rating_number` ~6,846 vs. ~12 for a random row) — used here as
a soft prior on this benchmark, explicitly not a design we'd ship to a real
store.

---

## Setup and installation

Requires Python 3.10+. **The agent uses only the Python standard library —
there is no install step**; `requirements.txt` is deliberately empty because
official scoring may run without network access.

```bash
git clone https://github.com/xjinhx/winfluencers-techjam
cd winfluencers-techjam
```

### Catalog

The 50,000-product catalog is frozen and not committed (60 MB, gitignored).
Download `catalog.jsonl.gz` from the participant kit release, decompress it,
and place it at `data/catalog.jsonl` (expected row count: 50,000):

```bash
gzip -dk catalog.jsonl.gz
mv catalog.jsonl data/catalog.jsonl
```

`data/public_set.jsonl` (200 labeled development sessions) is already
committed.

---

## Reproducing these numbers

```bash
python -m evaluator.local_evaluator --output <scratch-path>.json
```

Runs all 200 public sessions through the unmodified evaluator. On this
checkout it produced, verified moments before writing this document:

```
hit_rate_at_10  1.0
mrr             0.902464
mttc            2.39
efficiency      0.861
technical_score 0.942939
```

Tuned weights load automatically from `config/tuned.json`. Delete or rename
it to run with untuned defaults instead.

```bash
python -m unittest discover -s tests      # 56 unit tests
python -m tools.demo --sample public_0002 # one full multi-turn transcript
python -m tools.ablate                    # regenerate the ablation table
python -m tools.tune                      # coordinate ascent, split-half CV
```

### Repository layout

```
starter/agent.py               Organizer entry point: reset() / respond(); loads config
shopping_copilot/
  config.py                     Every tunable parameter, JSON round-trippable
  catalog.py                    Catalog load, normalisation, coverage flags, priors
  index.py, dense.py, fusion.py Per-field BM25, character-n-gram route, convex fusion
  structured.py                 Gender/brand/category slots, three-way constraint checks
  state.py, intent.py           Dialogue state, override handling, intent routing
  features.py, ranking.py       40-dimension feature vector, intent-conditional scorer
  clarify.py                    Clarification gate
  agent.py                      Turn orchestration, conjunctive injection, recommend gates
  baselines/weak_bm25.py        The original starter baseline, kept for comparison
tools/                          tune, ablate, demo, diagnostics, offline replay
tests/                          56 unit tests
config/tuned.json               Live tuned weights (loaded by default)
evaluator/                      Official local evaluator (unmodified — never edit)
data/                           catalog.jsonl (downloaded), public_set.jsonl (committed)
```

---

## Limitations and what we'd improve

**Tuned on the 200 public sessions; the private 800 is the real test.**
Weights were fit with train/holdout splits throughout, and single-run MRR
standard error is ≈0.029 on this set — a real gain smaller than that can't be
verified here even though it may hold on the larger private set.

**No neural semantic route, no LLM reranker**, by deliberate scope decision
for the no-network-access requirement — a character-n-gram index stands in
for the former, and a LightGBM reranker was built and evaluated but is not
shipped (it tied at best against held-out data; see `CLAUDE.md`).

**Personalisation is one feature, not a self-adapting system.** The problem
statement's "self-evolution / dynamic context programming" pillar is
addressed by a single `profile_affinity` feature reading purchase history and
preference tags — it ablates to a marginal +0.0010, and is the pillar we'd
most want to build out further given more time.

**Remaining headroom is in MRR, not coverage.** HR@10 is already at 1.000 on
the public set, so further gain has to come from placing an already-found
target higher rather than finding more targets — several reranking
approaches were tried for this and rejected on held-out evidence; see the
"Roadmap" section of `CLAUDE.md`.

**The popularity prior is benchmark-specific.** It's a real, measured
property of how this benchmark's labels were constructed, not of shopper
behaviour, and we'd drop it if this were a production ranking system.

---

## Team contributions

- **He Jinhong** — catalog-wide correctness audits (gender hierarchy, brand
  false-positive gate), conjunctive-injection mechanism, retrieval-depth and
  clarification tuning, measurement discipline across the project
- **Dylan Huang** — retrieval-depth root-causing, the conjunctive
  constraint-matching feature, constraint commonness damping, the pairwise
  learning-to-rank investigation
- **Arwen Tan** — evidence-gated recommendation withholding
- **Joey (jsxysxy)** — ranking feature weights (popularity/title-coverage
  interactions), confidence-gated recommendation hold, fold-validation of the
  merged recommendation gates

## Data attribution

Catalog and sessions derive from the Amazon Reviews 2023 dataset (McAuley Lab,
UCSD), category `Clothing_Shoes_and_Jewelry`, provided frozen by the
competition organizer. No external model API or network access is used at
runtime. See [`DATA_ATTRIBUTION.md`](DATA_ATTRIBUTION.md).
