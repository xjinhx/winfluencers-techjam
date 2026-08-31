import type { EnrichedProduct } from "../types";

// Static storefront slice per PRD_demo_frontend.md 3.1 ("hardcode the 6 shown
// in Figma if that's faster"). The storefront is a non-functional stage set —
// only the Chat screen talks to the real agent.
export const TRENDING_PRODUCTS: EnrichedProduct[] = [
  {
    parent_asin: "demo-1",
    title: "Belle Poque V-Neck Pullover Sweater",
    store: "Belle Poque",
    average_rating: 4.2,
    rating_number: 1200,
    price: 28,
    category: "Sweaters",
  },
  {
    parent_asin: "demo-2",
    title: "Siilsaa Summer Letter Print Tee",
    store: "siilsaa",
    average_rating: 4.1,
    rating_number: 482,
    price: 14,
    category: "Tops",
  },
  {
    parent_asin: "demo-3",
    title: "High-Waist Wide Leg Trousers",
    store: "Aritzia",
    average_rating: 4.6,
    rating_number: 3400,
    price: 36,
    category: "Trousers",
  },
  {
    parent_asin: "demo-4",
    title: "Foldover Collar Knit Cardigan",
    store: "Belle Poque",
    average_rating: 4.4,
    rating_number: 890,
    price: 42,
    category: "Sweaters",
  },
  {
    parent_asin: "demo-5",
    title: "Cropped Denim Jacket Vintage Wash",
    store: "Levi's",
    average_rating: 4.7,
    rating_number: 8900,
    price: 54,
    category: "Outerwear",
  },
  {
    parent_asin: "demo-6",
    title: "Satin Slip Midi Dress",
    store: "Reformation",
    average_rating: 4.3,
    rating_number: 1500,
    price: 31,
    category: "Dresses",
  },
];
