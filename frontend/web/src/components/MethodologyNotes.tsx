import { useState } from "react";
import "./MethodologyNotes.css";

type Tab = "challenge" | "results";

// Static content, not fetched -- the challenge copy and the score figures
// are both taken from the project's own README.md / CLAUDE.md, last synced
// 2026-08-31 (unmodified evaluator, 200 public sessions). Update this block
// by hand if that measurement changes; don't wire it to a live endpoint,
// since re-running the evaluator is a multi-minute operation not meant to
// happen on page load.
export function MethodologyNotes() {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("challenge");

  return (
    <>
      <button
        type="button"
        className="methodology-trigger"
        onClick={() => setOpen(true)}
        aria-label="About this demo"
      >
        ?
      </button>
      {open ? (
        <div className="methodology-overlay" onClick={() => setOpen(false)}>
          <div className="methodology-panel" onClick={(e) => e.stopPropagation()}>
            <div className="methodology-header">
              <span>{tab === "challenge" ? "What we're solving" : "How BuyteAI is scored"}</span>
              <button
                type="button"
                className="methodology-close"
                onClick={() => setOpen(false)}
                aria-label="Close"
              >
                ×
              </button>
            </div>

            <div className="methodology-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={tab === "challenge"}
                className={`methodology-tab${tab === "challenge" ? " methodology-tab-active" : ""}`}
                onClick={() => setTab("challenge")}
              >
                The challenge
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === "results"}
                className={`methodology-tab${tab === "results" ? " methodology-tab-active" : ""}`}
                onClick={() => setTab("results")}
              >
                The results
              </button>
            </div>

            {tab === "challenge" ? (
              <>
                <p>
                  <b>TechJam 2026, Track 4 — Conversational Search.</b> The task: build an agent
                  that finds one specific hidden product in a 50,000-item Amazon clothing catalog
                  through conversation, in 10 turns or fewer, asking a clarifying question only
                  when it's worth more than another retrieval call.
                </p>
                <p>
                  Each turn, BuyteAI parses what's been said → routes intent (buying / browsing /
                  uncertain) → retrieves candidates (lexical search across five catalog fields
                  plus a semantic route, fused together, plus an exact-match pass that pulls in
                  anything the fused search missed) → reranks with a model built on the
                  constraints actually disclosed so far → decides whether to ask another
                  question, hold the list, or show recommendations.
                </p>
                <p>
                  <b>The single biggest lesson:</b> asking questions isn't a nice-to-have, it's
                  the whole system. Removing the clarification step alone costs roughly ten times
                  more score than any other component — a browsing session that opens with just a
                  category name gives the ranker nothing to work with until BuyteAI asks
                  something.
                </p>
                <p className="methodology-caveat">
                  No external model or API — every retrieval and ranking step is deterministic
                  and runs in-memory, $0.00 cost, 0 tokens.
                </p>
              </>
            ) : (
              <>
                <p>
                  This replay runs the exact same logic as our official scoring: a simulated
                  customer built from the hidden target product, the unmodified agent, and a hit
                  whenever the target lands in the top 10 before the session runs out of turns.
                </p>
                <dl className="methodology-stats">
                  <div>
                    <dt>TechnicalScore</dt>
                    <dd>0.9429</dd>
                  </div>
                  <div>
                    <dt>Hit rate @10</dt>
                    <dd>100.0%</dd>
                  </div>
                  <div>
                    <dt>MRR</dt>
                    <dd>0.9025</dd>
                  </div>
                  <div>
                    <dt>Avg. turns to hit</dt>
                    <dd>2.39</dd>
                  </div>
                </dl>
                <p className="methodology-formula">
                  TechnicalScore = 0.5 × HR@10 + 0.3 × MRR + 0.2 × efficiency
                </p>
                <p className="methodology-caveat">
                  Measured on the 200-session public dev set with the unmodified evaluator (last
                  measured 2026-08-31, 168/200 sessions hit at rank 1, zero misses, 56/56 tests
                  passing). The private 800-session set is scored the same way but isn't shown
                  here.
                </p>
              </>
            )}
          </div>
        </div>
      ) : null}
    </>
  );
}
