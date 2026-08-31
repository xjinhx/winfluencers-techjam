import type { SimulationResult } from "../types";
import "./SessionVerdict.css";

export function SessionVerdict({ result }: { result: SimulationResult }) {
  if (result.hit) {
    return (
      <div className="session-verdict session-verdict-hit">
        <span className="session-verdict-badge session-verdict-badge-hit">Hit</span>
        <span className="session-verdict-detail">
          Found the target at rank #{result.best_rank} on turn {result.first_hit_turn} (reciprocal
          rank {result.reciprocal_rank.toFixed(3)}).
        </span>
      </div>
    );
  }
  return (
    <div className="session-verdict session-verdict-miss">
      <span className="session-verdict-badge session-verdict-badge-miss">Miss</span>
      <span className="session-verdict-detail">
        Ran out of turns without surfacing "{result.target.title}" in the top 10.
      </span>
    </div>
  );
}
