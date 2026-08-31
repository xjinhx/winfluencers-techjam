import { useEffect, useRef, useState } from "react";
import { StatusBar } from "../components/StatusBar";
import { ChatHeader } from "../components/ChatHeader";
import { SessionBar } from "../components/SessionBar";
import { SessionVerdict } from "../components/SessionVerdict";
import { UserBubble, AgentBubble } from "../components/ChatBubble";
import { RecommendationList } from "../components/RecommendationList";
import { RunBar } from "../components/RunBar";
import type { RunStatus } from "../components/RunBar";
import { simulate } from "../lib/api";
import type { SimulationResult, SimulationTurn } from "../types";
import "./Chat.css";

const TURN_LIMIT = 10;
const FIRST_TURN_DELAY_MS = 200;
const REVEAL_DELAY_MS = 900;

export function Chat({ onBack }: { onBack: () => void }) {
  const [status, setStatus] = useState<RunStatus>("idle");
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [visibleTurns, setVisibleTurns] = useState<SimulationTurn[]>([]);
  const [error, setError] = useState<string | null>(null);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const disclosedRef = useRef<string[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [visibleTurns, status]);

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  function revealNext(turns: SimulationTurn[], index: number) {
    if (index >= turns.length) {
      setStatus("done");
      return;
    }
    timerRef.current = setTimeout(
      () => {
        const turn = turns[index];
        disclosedRef.current.push(...turn.customer_message.toLowerCase().split(/\s+/).filter(Boolean));
        setVisibleTurns((prev) => [...prev, turn]);
        revealNext(turns, index + 1);
      },
      index === 0 ? FIRST_TURN_DELAY_MS : REVEAL_DELAY_MS,
    );
  }

  async function runSession() {
    if (status === "running") return;
    setStatus("running");
    setError(null);
    setResult(null);
    setVisibleTurns([]);
    disclosedRef.current = [];
    try {
      const data = await simulate();
      setResult(data);
      revealNext(data.turns, 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach the agent API.");
      setStatus("idle");
    }
  }

  const waitingOnNextTurn = status === "running" && (!result || visibleTurns.length < result.turns.length);

  return (
    <div className="chat-screen">
      <StatusBar />
      <ChatHeader turn={visibleTurns.length} turnLimit={TURN_LIMIT} ended={status === "done"} onBack={onBack} />
      {result ? <SessionBar result={result} /> : null}

      <div className="chat-thread">
        {status === "idle" && visibleTurns.length === 0 ? (
          <div className="agent-turn">
            <AgentBubble text="Tap the button below to watch BuyteAI play a real evaluator session: a simulated customer, the unmodified agent, and a hit/miss verdict at the end. You don't type anything -- just watch." />
          </div>
        ) : null}

        {visibleTurns.map((t) => (
          <div key={t.turn} className="agent-turn">
            <UserBubble text={t.customer_message} />
            <AgentBubble text={t.agent_message} />
            {t.recommendations.length > 0 ? (
              <RecommendationList
                products={t.recommendations}
                disclosedTerms={disclosedRef.current}
                targetAsin={result?.target.parent_asin ?? null}
              />
            ) : null}
          </div>
        ))}

        {waitingOnNextTurn ? (
          <div className="agent-turn">
            <AgentBubble text="…" />
          </div>
        ) : null}

        {status === "done" && result ? <SessionVerdict result={result} /> : null}
        {error ? <p className="chat-error">{error}</p> : null}
        <div ref={threadEndRef} />
      </div>

      <RunBar status={status} onRun={runSession} />
    </div>
  );
}
