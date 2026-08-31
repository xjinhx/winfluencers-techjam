import type { EnrichedProduct } from "../types";
import { ProductCard } from "./ProductCard";
import "./RecommendationList.css";

// Rank-pill color and the "matched:" line are presentation garnish, not
// agent output -- respond() returns only a ranked parent_asin list. See
// PRD_demo_frontend.md 3.2.
function matchedLine(product: EnrichedProduct, disclosed: string[]): string {
  const haystack = `${product.title} ${product.category ?? ""}`.toLowerCase();
  const hits = disclosed.filter((term) => term.length > 2 && haystack.includes(term.toLowerCase()));
  const unique = Array.from(new Set(hits)).slice(0, 3);
  return unique.length > 0 ? `matched: ${unique.join(" · ")}` : "matched to your request";
}

export function RecommendationList({
  products,
  disclosedTerms,
}: {
  products: EnrichedProduct[];
  disclosedTerms: string[];
}) {
  return (
    <div className="recommendation-list">
      {products.map((product, i) => {
        const rank = i + 1;
        return (
          <div className="recommendation-item" key={product.parent_asin}>
            <div className="recommendation-meta">
              <span className={`rank-pill ${rank % 2 === 0 ? "rank-pill-rose" : "rank-pill-teal"}`}>
                #{rank}
              </span>
              <span className="matched-note">{matchedLine(product, disclosedTerms)}</span>
            </div>
            <ProductCard product={product} />
          </div>
        );
      })}
    </div>
  );
}
