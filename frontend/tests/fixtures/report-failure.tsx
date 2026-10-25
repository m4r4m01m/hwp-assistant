import React from "react";
import { createRoot } from "react-dom/client";
import { Reports } from "../../src/reports";
import { CapabilitiesProvider } from "../../src/capabilities";
import "../../src/style.css";

createRoot(document.getElementById("root")!).render(<CapabilitiesProvider><Reports onClose={() => {}} onOpen={() => {}} /></CapabilitiesProvider>);
