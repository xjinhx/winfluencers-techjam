import { useState } from "react";
import type { KeyboardEvent } from "react";
import sendIcon from "../assets/icons/send.svg";
import "./InputBar.css";

export function InputBar({
  disabled,
  placeholder,
  onSend,
}: {
  disabled: boolean;
  placeholder: string;
  onSend: (text: string) => void;
}) {
  const [value, setValue] = useState("");

  function submit() {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") submit();
  }

  return (
    <div className="input-bar">
      <div className="input-field">
        <input
          type="text"
          value={value}
          disabled={disabled}
          placeholder={placeholder}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
        />
      </div>
      <button
        type="button"
        className="send-button"
        disabled={disabled || value.trim().length === 0}
        onClick={submit}
        aria-label="Send"
      >
        <img src={sendIcon} alt="" width={20} height={20} />
      </button>
    </div>
  );
}
