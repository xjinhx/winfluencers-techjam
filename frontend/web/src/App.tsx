import { useState } from "react";
import { Storefront } from "./screens/Storefront";
import { Chat } from "./screens/Chat";

type Screen = "storefront" | "chat";

export default function App() {
  const [screen, setScreen] = useState<Screen>("storefront");

  return (
    <div className="phone-frame">
      {screen === "storefront" ? (
        <Storefront onAskCopilot={() => setScreen("chat")} />
      ) : (
        <Chat onBack={() => setScreen("storefront")} />
      )}
    </div>
  );
}
