import React from "react";
import { createRoot } from "react-dom/client";
import { TemplateMarket } from "../../src/template-market";
import { StandardDesigner } from "../../src/standard-designer";
import "../../src/style.css";
import "../../src/workspace-shell.css";

const screen = new URLSearchParams(location.search).get("screen");
createRoot(document.getElementById("root")!).render(screen === "standard"
  ? <StandardDesigner onClose={() => {}} />
  : <TemplateMarket onClose={() => {}} onApply={() => {}} onReport={() => {}} />);
