import type { DemoProfile, RespondResult, SimulationResult } from "../types";

const API_URL = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

async function request<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${path} failed (${res.status}): ${detail}`);
  }
  return res.json() as Promise<T>;
}

export function resetSession(session_id: string, user_profile: Record<string, unknown>) {
  return request<{ ok: boolean }>("/reset", { session_id, user_profile });
}

export function respond(session_id: string, user_message: string, turn: number, top_k = 10) {
  return request<RespondResult>("/respond", { session_id, user_message, turn, top_k });
}

export async function fetchDemoProfile(dev = false): Promise<DemoProfile> {
  const res = await fetch(`${API_URL}/demo-profile${dev ? "?dev=1" : ""}`);
  if (!res.ok) throw new Error(`/demo-profile failed (${res.status})`);
  return res.json() as Promise<DemoProfile>;
}

// Replays one random public-set sample end to end -- the real agent against
// the real evaluator's simulated customer -- and returns the full turn-by-
// turn transcript plus the hit/miss verdict. No user input involved.
export function simulate(sampleId?: string): Promise<SimulationResult> {
  const qs = sampleId ? `?sample_id=${encodeURIComponent(sampleId)}` : "";
  return request<SimulationResult>(`/simulate${qs}`, {});
}
