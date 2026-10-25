import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { Reports } from "../../src/reports";
import { CapabilitiesProvider } from "../../src/capabilities";
import "../../src/style.css";

function Fixture() {
  const [opened, setOpened] = useState("");
  return opened ? <div data-testid="opened-document">{opened}</div>
    : <CapabilitiesProvider><Reports onClose={() => {}} onOpen={document => setOpened(document.id)} /></CapabilitiesProvider>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
