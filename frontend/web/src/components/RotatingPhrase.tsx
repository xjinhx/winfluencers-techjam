import { useEffect, useState } from "react";
import "./RotatingPhrase.css";

const PHRASES = [
  "Sweater weather",
  "Denim days",
  "Layer up",
  "Cozy knits",
  "Summer breeze",
  "Street style",
  "Office ready",
  "Weekend casual",
];

const HOLD_MS = 2500;
const FADE_MS = 300;

export function RotatingPhrase() {
  const [index, setIndex] = useState(0);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const fadeOut = setTimeout(() => setVisible(false), HOLD_MS);
    const advance = setTimeout(() => {
      setIndex((i) => (i + 1) % PHRASES.length);
      setVisible(true);
    }, HOLD_MS + FADE_MS);
    return () => {
      clearTimeout(fadeOut);
      clearTimeout(advance);
    };
  }, [index]);

  return (
    <span className={`rotating-phrase${visible ? "" : " rotating-phrase-fade"}`}>
      {PHRASES[index]}
    </span>
  );
}
