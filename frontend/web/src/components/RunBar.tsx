import sendIcon from "../assets/icons/send.svg";
import "./RunBar.css";

export type RunStatus = "idle" | "running" | "done";

const LABEL: Record<RunStatus, string> = {
  idle: "Tap to run a random evaluator session",
  running: "BuyteAI is working through this session…",
  done: "Session complete — run another?",
};

export function RunBar({ status, onRun }: { status: RunStatus; onRun: () => void }) {
  return (
    <div className="run-bar">
      <div className="run-bar-label">{LABEL[status]}</div>
      <button
        type="button"
        className="run-button"
        disabled={status === "running"}
        onClick={onRun}
        aria-label={status === "idle" ? "Run session" : "Run another session"}
      >
        <img src={sendIcon} alt="" width={20} height={20} />
      </button>
    </div>
  );
}
