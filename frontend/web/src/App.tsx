import { useState } from "react";
import { Storefront } from "./screens/Storefront";
import { Chat } from "./screens/Chat";

type Screen = "storefront" | "chat";

export default function App() {
  const [screen, setScreen] = useState<Screen>("storefront");

  return (
    <div className="app-shell">
      <div className="app-desktop-panel">
        <span className="app-wordmark">Buyte</span>
        <p className="app-tagline">
          BuyteAI narrows 50,000 clothing listings down to the one you actually want. Try the live
          demo alongside.
        </p>
      </div>
      <div className="app-stage">
        <div className="phone-frame">
          {screen === "storefront" ? (
            <Storefront onAskCopilot={() => setScreen("chat")} />
          ) : (
            <Chat onBack={() => setScreen("storefront")} />
          )}
        </div>
      </div>
    </div>
  );
}
