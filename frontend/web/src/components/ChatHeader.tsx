import backIcon from "../assets/icons/back.svg";
import avatarMark from "../assets/icons/avatar-mark.svg";
import liveDot from "../assets/icons/live-dot.svg";
import { MoreIcon } from "./NavIcons";
import "./ChatHeader.css";

export function ChatHeader({
  turn,
  turnLimit,
  ended,
  onBack,
}: {
  turn: number;
  turnLimit: number;
  ended: boolean;
  onBack: () => void;
}) {
  return (
    <div className="chat-header">
      <button type="button" className="icon-button" onClick={onBack} aria-label="Back">
        <img src={backIcon} alt="" width={22} height={22} />
      </button>
      <div className="chat-avatar">
        <img src={avatarMark} alt="" width={18} height={18} />
      </div>
      <div className="chat-header-text">
        <span className="chat-title">Shopping Copilot</span>
        <div className="chat-status">
          <img src={liveDot} alt="" width={6} height={6} style={{ opacity: ended ? 0.4 : 1 }} />
          <span>
            {ended ? "Session ended" : `Live · turn ${Math.min(turn, turnLimit)} of ${turnLimit}`}
          </span>
        </div>
      </div>
      <MoreIcon className="more-icon" />
    </div>
  );
}
