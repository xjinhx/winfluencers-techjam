import type { EnrichedProduct } from "../types";
import { artColorFor, formatPrice, formatRatingCount } from "../lib/presentation";
import lightAsset from "../assets/icons/light.svg";
import silhouetteAsset from "../assets/icons/silhouette.svg";
import sparkleAsset from "../assets/icons/sparkle.svg";
import starAsset from "../assets/icons/star.svg";
import "./ProductCard.css";

export function ProductCard({ product }: { product: EnrichedProduct }) {
  const price = formatPrice(product.price);
  return (
    <div className="product-card">
      <div className="product-card-art" style={{ backgroundColor: artColorFor(product.parent_asin) }}>
        <img className="art-light" src={lightAsset} alt="" />
        <img className="art-silhouette" src={silhouetteAsset} alt="" />
        <div className="ai-badge">
          <img src={sparkleAsset} alt="" width={8} height={8} />
          <span>AI</span>
        </div>
      </div>
      <div className="product-card-text">
        <p className="product-card-title">{product.title}</p>
        <div className="product-card-rating">
          <img src={starAsset} alt="" width={10} height={10} />
          <span className="rating-value">{(product.average_rating ?? 0).toFixed(1)}</span>
          <span className="rating-count">({formatRatingCount(product.rating_number)})</span>
        </div>
        <div className="product-card-price-row">
          {price ? (
            <span className="price">{price}</span>
          ) : (
            <span className="price price-unavailable">Price unavailable</span>
          )}
          {product.store ? <span className="store">{product.store}</span> : null}
        </div>
      </div>
    </div>
  );
}
