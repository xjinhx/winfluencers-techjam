import type { SimulationResult } from "../types";
import "./SessionBar.css";

// The target is shown up front, on purpose: the point of this replay is
// watching, turn by turn, whether BuyteAI's own recommendations converge on
// it -- see the highlighted card in RecommendationList once it appears.
export function SessionBar({ result }: { result: SimulationResult }) {
  return (
    <div className="session-bar">
      <span className="session-bar-tag">{result.scenario_type}</span>
      <span className="session-bar-text">
        {result.sample_id} · target: {result.target.title}
      </span>
    </div>
  );
}
