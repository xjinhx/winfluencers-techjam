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
