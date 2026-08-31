import { useState } from "react";
import "./MethodologyNotes.css";

// Static numbers, not fetched -- these are the last measured figures from
// the project's own CLAUDE.md ("Current state"), the unmodified CLI
// evaluator over all 200 public sessions, dated 2026-08-31. Update this
// block by hand if that measurement changes; don't wire it to a live
// endpoint, since re-running the evaluator is a multi-minute operation not
// meant to happen on page load.
export function MethodologyNotes() {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        className="methodology-trigger"
        onClick={() => setOpen(true)}
        aria-label="About these results"
      >
        ?
      </button>
      {open ? (
        <div className="methodology-overlay" onClick={() => setOpen(false)}>
          <div className="methodology-panel" onClick={(e) => e.stopPropagation()}>
            <div className="methodology-header">
              <span>How BuyteAI is scored</span>
              <button
                type="button"
                className="methodology-close"
                onClick={() => setOpen(false)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <p>
              This replay runs the exact same logic as our official scoring: a simulated customer
              built from the hidden target product, the unmodified agent, and a hit whenever the
              target lands in the top 10 before the session runs out of turns.
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
              passing). The private 800-session set is scored the same way but isn't shown here.
            </p>
          </div>
        </div>
      ) : null}
    </>
  );
}
