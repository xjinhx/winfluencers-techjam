import type { QuickReply } from "../types";
import "./QuickReplies.css";

export function QuickReplies({
  replies,
  disabled,
  onPick,
}: {
  replies: QuickReply[];
  disabled: boolean;
  onPick: (value: string) => void;
}) {
  if (replies.length === 0) return null;
  return (
    <div className="quick-replies">
      {replies.map((reply) => (
        <button
          key={reply.label}
          type="button"
          className="quick-reply-chip"
          disabled={disabled}
          onClick={() => onPick(reply.value)}
        >
          {reply.label}
        </button>
      ))}
    </div>
  );
}
