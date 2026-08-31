import type { QuickReply } from "../types";

// Presentation-layer garnish only. The agent returns `ask_attribute` as a
// bare string (e.g. "color"); it does not return suggested values, so the
// chip options here are generic per attribute type, not sourced from the
// catalog or the session. See PRD_demo_frontend.md section 3.2.
const QUICK_REPLIES: Record<string, QuickReply[]> = {
  category: [
    { label: "Tops", value: "Tops" },
    { label: "Dresses", value: "Dresses" },
    { label: "Shoes", value: "Shoes" },
    { label: "Outerwear", value: "Outerwear" },
  ],
  material: [
    { label: "Cotton", value: "Cotton" },
    { label: "Denim", value: "Denim" },
    { label: "Wool", value: "Wool" },
    { label: "No preference", value: "No preference" },
  ],
  color: [
    { label: "Black", value: "Black" },
    { label: "White", value: "White" },
    { label: "Neutral tones", value: "Neutral tones" },
    { label: "No preference", value: "Any color" },
  ],
  size: [
    { label: "Small", value: "Small" },
    { label: "Medium", value: "Medium" },
    { label: "Large", value: "Large" },
    { label: "Not sure yet", value: "Not sure yet" },
  ],
  style: [
    { label: "Casual", value: "Casual" },
    { label: "Classic", value: "Classic" },
    { label: "Trendy", value: "Trendy" },
    { label: "No preference", value: "No preference" },
  ],
  brand: [
    { label: "No brand preference", value: "No brand preference" },
    { label: "Well-known brands", value: "Well-known brands" },
  ],
  budget: [
    { label: "Under $30", value: "Under $30" },
    { label: "Under $50", value: "Under $50" },
    { label: "Under $80", value: "Under $80" },
    { label: "Doesn't matter", value: "Doesn't matter" },
  ],
  feature: [
    { label: "Waterproof", value: "Waterproof" },
    { label: "Lightweight", value: "Lightweight" },
    { label: "Durable", value: "Durable" },
    { label: "No preference", value: "No preference" },
  ],
  use_case: [
    { label: "Everyday wear", value: "Everyday wear" },
    { label: "Special occasion", value: "Special occasion" },
    { label: "Athletic / outdoor", value: "Athletic / outdoor" },
  ],
  other: [{ label: "No preference", value: "No preference" }],
};

export function quickRepliesFor(attribute: string | null): QuickReply[] {
  if (!attribute) return [];
  return QUICK_REPLIES[attribute] ?? [{ label: "No preference", value: "No preference" }];
}

// Deterministic ArtSlot background per product, matching the varied pastel
// swatches in the Figma trending grid (no product photography in this catalog).
const ART_PALETTE = [
  "#ede0d1",
  "#d9ded1",
  "#c9bfb8",
  "#e5d4cc",
  "#b8c7d4",
  "#d1bfcc",
  "#dcd5c3",
  "#c7d3c8",
];

export function artColorFor(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return ART_PALETTE[hash % ART_PALETTE.length];
}

export function formatPrice(price: number | null): string | null {
  if (price === null || Number.isNaN(price)) return null;
  return `$${price % 1 === 0 ? price.toFixed(0) : price.toFixed(2)}`;
}

export function formatRatingCount(n: number | null): string {
  if (!n) return "0";
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
