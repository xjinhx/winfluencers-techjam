import sparkle from "../assets/icons/ask-sparkle.svg";
import "./AskCopilotPill.css";

export function AskCopilotPill({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="ask-copilot-pill" onClick={onClick}>
      <img src={sparkle} alt="" width={18} height={18} />
      <span>Ask the Copilot</span>
    </button>
  );
}
