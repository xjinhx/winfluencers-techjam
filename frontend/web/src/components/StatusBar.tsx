import signal from "../assets/icons/signal.svg";
import battery from "../assets/icons/battery.svg";
import "./StatusBar.css";

export function StatusBar() {
  return (
    <div className="status-bar">
      <span>9:41</span>
      <div className="status-bar-icons">
        <img src={signal} alt="" width={18} height={10} />
        <img src={battery} alt="" width={26} height={12} />
      </div>
    </div>
  );
}
