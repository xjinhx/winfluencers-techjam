import { StatusBar } from "../components/StatusBar";
import { BottomNav } from "../components/BottomNav";
import { AskCopilotPill } from "../components/AskCopilotPill";
import { ProductCard } from "../components/ProductCard";
import { RotatingPhrase } from "../components/RotatingPhrase";
import { TRENDING_PRODUCTS } from "../data/trending";
import searchIcon from "../assets/icons/search.svg";
import heroEllipse from "../assets/icons/hero-ellipse.svg";
import badgeDot from "../assets/icons/badge-dot.svg";
import "./Storefront.css";

const CATEGORIES = ["All", "Tops", "Dresses", "Shoes", "Bags", "Accessories"];

export function Storefront({ onAskCopilot }: { onAskCopilot: () => void }) {
  return (
    <div className="storefront">
      <StatusBar />

      <div className="top-tabs">
        <span className="tab">Following</span>
        <span className="tab">For You</span>
        <div className="tab tab-active">
          <span>Shop</span>
          <div className="tab-underline" />
        </div>
        <div className="tab-spacer" />
        <img src={searchIcon} alt="Search" width={22} height={22} />
      </div>

      <div className="category-chips">
        {CATEGORIES.map((category, i) => (
          <div key={category} className={`chip ${i === 0 ? "chip-active" : ""}`}>
            {category}
          </div>
        ))}
      </div>

      <div className="storefront-content">
        <div className="hero-feature">
          <img className="hero-ellipse" src={heroEllipse} alt="" />
          <img className="hero-mascot" src="/buyte-mascot.png" alt="" />
          <div className="hero-feature-inner">
            <div className="hero-badge">
              <img src={badgeDot} alt="" width={6} height={6} />
              <span>BUYTE PICKS · UPDATED TODAY</span>
            </div>
            <div className="hero-copy">
              <h2 className="hero-title">
                <RotatingPhrase />
                <br />
                sorted for you.
              </h2>
              <p className="hero-subtitle">Ask Buyte — we'll narrow 50k pieces down to 10.</p>
            </div>
          </div>
        </div>

        <div className="section-heading">
          <h3>Trending this week</h3>
          <span>See all</span>
        </div>

        <div className="product-grid">
          {TRENDING_PRODUCTS.map((product) => (
            <ProductCard key={product.parent_asin} product={product} />
          ))}
        </div>
      </div>

      <BottomNav />
      <AskCopilotPill onClick={onAskCopilot} />
    </div>
  );
}
