export interface EnrichedProduct {
  parent_asin: string;
  title: string;
  store: string | null;
  average_rating: number | null;
  rating_number: number | null;
  price: number | null;
  category: string | null;
}

export interface RespondResult {
  message: string;
  ask_attribute: string | null;
  recommendations: EnrichedProduct[];
}

export interface DemoProfile {
  sample_id: string;
  user_profile: Record<string, unknown>;
  ground_truth?: EnrichedProduct | null;
}

export type ChatRole = "user" | "agent";

export interface QuickReply {
  label: string;
  value: string;
}

export interface ChatTurn {
  id: string;
  role: ChatRole;
  text: string;
  quickReplies?: QuickReply[];
  recommendations?: EnrichedProduct[];
  matchedNote?: string;
}

export interface SimulationTurn {
  turn: number;
  customer_message: string;
  agent_message: string;
  ask_attribute: string | null;
  recommendations: EnrichedProduct[];
  target_rank: number | null;
}

export interface SimulationResult {
  sample_id: string;
  scenario_type: string;
  difficulty_bucket: string | null;
  user_profile_summary: string;
  target: EnrichedProduct;
  hit: boolean;
  first_hit_turn: number | null;
  best_rank: number | null;
  reciprocal_rank: number;
  turns: SimulationTurn[];
}
