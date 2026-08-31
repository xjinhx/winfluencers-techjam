import { useEffect, useRef, useState } from "react";
import { StatusBar } from "../components/StatusBar";
import { ChatHeader } from "../components/ChatHeader";
import { DevBanner } from "../components/DevBanner";
import { UserBubble, AgentBubble } from "../components/ChatBubble";
import { QuickReplies } from "../components/QuickReplies";
import { RecommendationList } from "../components/RecommendationList";
import { InputBar } from "../components/InputBar";
import { fetchDemoProfile, resetSession, respond } from "../lib/api";
import { quickRepliesFor } from "../lib/presentation";
import type { ChatTurn, EnrichedProduct } from "../types";
import "./Chat.css";

const TURN_LIMIT = 10;

// Separate from the normal demo: append ?dev=1 to the URL to reveal the
// ground-truth target for the session and highlight it in the recommendation
// list, so you can check whether the agent actually found the right item.
// Off by default -- a normal visitor never sees this.
const DEV_MODE =
  typeof window !== "undefined" && new URLSearchParams(window.location.search).get("dev") === "1";

const FALLBACK_PROFILE = {
  average_prior_rating: 4.5,
  preference_tags: ["fit", "comfort", "style"],
  purchase_frequency: "3-4 prior purchases",
  rating_style: "usually positive",
  summary: "Prior purchases emphasize fit, comfort, style.",
};

function newId() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function Chat({ onBack }: { onBack: () => void }) {
  const [turns, setTurns] = useState<ChatTurn[]>([
    {
      id: newId(),
      role: "agent",
      text: "Hi! Tell me what you're shopping for and I'll help you narrow it down.",
    },
  ]);
  const [turn, setTurn] = useState(0);
  const [sessionReady, setSessionReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [devSampleId, setDevSampleId] = useState<string | null>(null);
  const [devTarget, setDevTarget] = useState<EnrichedProduct | null>(null);
  const sessionIdRef = useRef(newId());
  const disclosedRef = useRef<string[]>([]);
  const threadEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let profile: Record<string, unknown> = FALLBACK_PROFILE;
      try {
        const demo = await fetchDemoProfile(DEV_MODE);
        profile = demo.user_profile;
        if (DEV_MODE && !cancelled) {
          setDevSampleId(demo.sample_id);
          setDevTarget(demo.ground_truth ?? null);
        }
      } catch {
        // API not reachable yet -- fall back to a representative profile so
        // the thread still opens; /reset below will surface the real error.
      }
      try {
        await resetSession(sessionIdRef.current, profile);
        if (!cancelled) setSessionReady(true);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Could not reach the agent API.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  const ended = turn >= TURN_LIMIT;

  async function send(text: string) {
    if (busy || ended || !sessionReady) return;
    const nextTurn = turn + 1;
    disclosedRef.current.push(...text.toLowerCase().split(/\s+/).filter(Boolean));

    setTurns((prev) => [...prev, { id: newId(), role: "user", text }]);
    setBusy(true);
    setError(null);

    try {
      const result = await respond(sessionIdRef.current, text, nextTurn, 10);
      setTurn(nextTurn);
      setTurns((prev) => [
        ...prev,
        {
          id: newId(),
          role: "agent",
          text: result.message,
          quickReplies: quickRepliesFor(result.ask_attribute),
          recommendations: result.recommendations,
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The agent didn't respond. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="chat-screen">
      <StatusBar />
      <ChatHeader turn={turn} turnLimit={TURN_LIMIT} ended={ended} onBack={onBack} />
      {DEV_MODE ? <DevBanner sampleId={devSampleId} target={devTarget} /> : null}

      <div className="chat-thread">
        {turns.map((t) =>
          t.role === "user" ? (
            <UserBubble key={t.id} text={t.text} />
          ) : (
            <div key={t.id} className="agent-turn">
              <AgentBubble text={t.text} />
              {t.quickReplies && t.quickReplies.length > 0 ? (
                <QuickReplies replies={t.quickReplies} disabled={busy || ended} onPick={send} />
              ) : null}
              {t.recommendations && t.recommendations.length > 0 ? (
                <RecommendationList
                  products={t.recommendations}
                  disclosedTerms={disclosedRef.current}
                  targetAsin={DEV_MODE ? devTarget?.parent_asin : null}
                />
              ) : null}
            </div>
          ),
        )}
        {busy ? (
          <div className="agent-turn">
            <AgentBubble text="…" />
          </div>
        ) : null}
        {error ? <p className="chat-error">{error}</p> : null}
        <div ref={threadEndRef} />
      </div>

      <InputBar
        disabled={busy || ended || !sessionReady}
        placeholder={ended ? "Session ended" : "Message Buyte…"}
        onSend={send}
      />
    </div>
  );
}
