import avatarMark from "../assets/icons/bubble-avatar-mark.svg";
import "./ChatBubble.css";

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="bubble-row bubble-row-user">
      <div className="bubble bubble-user">
        <p>{text}</p>
      </div>
    </div>
  );
}

export function AgentBubble({ text }: { text: string }) {
  return (
    <div className="bubble-row bubble-row-agent">
      <div className="bubble-avatar-small">
        <img src={avatarMark} alt="" width={14} height={14} />
      </div>
      <div className="bubble bubble-agent">
        <p>{text}</p>
      </div>
    </div>
  );
}
