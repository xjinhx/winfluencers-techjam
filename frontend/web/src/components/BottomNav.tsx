import { HomeIcon, InboxIcon, ProfileIcon } from "./NavIcons";
import shop from "../assets/icons/shop.svg";
import "./BottomNav.css";

// Dead chrome per PRD 3.1 -- Shop is always the active tab, nothing tappable.
export function BottomNav() {
  return (
    <div className="bottom-nav">
      <div className="bottom-nav-item">
        <HomeIcon />
        <span>Home</span>
      </div>
      <div className="bottom-nav-item active">
        <img src={shop} alt="" width={22} height={22} />
        <span>Shop</span>
      </div>
      <div className="bottom-nav-item">
        <InboxIcon />
        <span>Inbox</span>
      </div>
      <div className="bottom-nav-item">
        <ProfileIcon />
        <span>Profile</span>
      </div>
    </div>
  );
}
