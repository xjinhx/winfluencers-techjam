# Rank-2 text read — why the near misses happen, and where the ceiling is

**Date:** 2026-08-31 · **Build:** `eba9e70` (synthetic span normalisation),
`TechnicalScore = 0.909328`, HR@10 = 1.000
**Reproduce:** `python -m tools.read_pairs --ranks 2 --pack <scratch>/pack.txt`

This closes the roadmap item that had stood since 2026-08-30: *"read the actual
title/features text of the rank-2 targets against their winners and find what
distinguishes them — a feature question, not a weighting question."* It turned
out to be neither.

## Method

The feature trace records vectors, not dialogue, so it cannot show what the
customer had actually said. `tools/read_pairs.py` wraps the real `Agent` and
records, per turn, the customer's message, the constraint spans live at that
moment, and the ranking returned — then joins positionally to the labels. The
instrumented run reproduces `0.909328` exactly, so it is the real dialogue.

Every pair is classified:

| verdict | meaning | implication |
|---|---|---|
| **A** | what was disclosed already separated them | a **ranking** defect — reweighting would help |
| **B** | separable only once more is disclosed | a **timing** defect — asking would help |
| **C** | not separable even with the full card | a **structural tie** — nothing helps |

## Result: A=0, B=27, C=3

**Zero A cases.** There is not one rank-2 session where the agent held enough
information and still mis-ordered it. The ranker is not making avoidable
mistakes in this band, which retires the theory that popularity was drowning
constraint evidence: winner-is-more-popular is **15/30**, a coin flip, and in
several pairs the target is far more popular and loses anyway (`public_0006`,
3,042 ratings vs 41; `public_0058`, 1,032 vs 231).

**The state of the world at lock-in:**

- median **1** span live, out of a median **4**-span card
- **8 of 30** had *zero* spans — the customer had said nothing but a category
- **0 of 30** had the full card disclosed

Picking one product out of a category from a bare category name is a lottery.
Rank 2 there is a good outcome, not a defect.

## The three structural ties

These, and only these, bound the ceiling.

`public_0058` is the cleanest example. The customer said *"Rain & Anoraks
Raincoats. A key requirement is: polyester."*

| | target `B08L83YQTZ` | winner `B07BCP8DG5` |
|---|---|---|
| store | JTANIB | Rokka&Rolla |
| title | Women Packable Rain Jacket Waterproof Lightweight Raincoat Hooded | Women's Lightweight Rain Jacket Hooded Anorak Windbreaker Raincoat |
| features open | `100% Polyester Imported Zipper closure` | `100% Polyester Imported Zipper closure` |
| card match | **4/4** | **4/4** |

Both satisfy every constraint the simulator will ever disclose. No feature, no
weighting, and no human can separate them from the listing text — the answer
key is arbitrary between them. Also `public_0120` and `public_0175`.

## The ceiling, with MTTC priced in

MRR wants more disclosure; MTTC wants fewer turns; the evaluator breaks on
first hit. They are in direct opposition, so both cannot be had at once.

| disclosure reached by | MTTC | ceiling |
|---|---|---|
| turn 1 (free — physically impossible) | 1.875 | 0.9585 |
| turn 2 | 2.100 | 0.9540 |
| **turn 3 — realistic: a 4-span card at ≤2 spans per ask** | **2.410** | **0.9478** |
| turn 4 | 2.760 | 0.9408 |

**0.97 is not reachable.** It would require MRR ≈ 0.97 *and* MTTC ≈ 1.5
simultaneously, which the first-hit-break rule forbids. Even the impossible
free-disclosure row is 0.9585.

## What follows

The remaining headroom is roughly **+0.038** (0.9093 → ~0.947), all of it in
disclosure timing, gated on a **policy** change rather than any new feature or
learner. The obvious candidate is confidence-gated withholding — recommend
immediately when the top candidate is clear, ask one more question when the
top two are near-tied.

That was tested before and rejected (blanket, budget 2-8, net −0.0233), but
that test ran at HR@10 0.960 with `target_never_in_pool` = 6, and its dominant
cost was **HR@10 −0.030 from disclosure making retrieval worse** — the exact
mechanism the conjunctive injection has since removed (`target_never_in_pool`
is now 0). The arithmetic also favours waiting: one extra turn costs 0.0001 of
score per session via MTTC, while rank 2 → 1 pays 0.00075 — **7.5×**.

**The prior rejection stands until the experiment is actually re-run**, and it
must be confidence-gated rather than blanket, and measured on fold B.

Plan around **~0.95**, not 0.97.
