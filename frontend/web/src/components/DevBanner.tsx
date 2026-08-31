import type { EnrichedProduct } from "../types";
import "./DevBanner.css";

// Dev-mode only (?dev=1) -- shows the ground-truth target for this demo
// session so you can verify whether the agent actually found the right
// item. Never shown by default; see PRD_demo_frontend.md 3.2 pattern.
export function DevBanner({
  sampleId,
  target,
}: {
  sampleId: string | null;
  target: EnrichedProduct | null;
}) {
  return (
    <div className="dev-banner">
      <span className="dev-banner-tag">DEV</span>
      <span className="dev-banner-text">
        {sampleId ? `sample ${sampleId}` : "sample unknown"}
        {target ? ` · target: ${target.title}` : " · no ground truth for this sample"}
      </span>
    </div>
  );
}
