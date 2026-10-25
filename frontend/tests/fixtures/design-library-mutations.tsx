import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { DesignLibrary } from "../../src/design-library";
import "../../src/style.css";

function Fixture() {
  const [settings, setSettings] = useState({ table_header_fill: "#eeeeee", table_border_color: "#334455" });
  const [changes, setChanges] = useState<string[]>([]);
  return <>
    <DesignLibrary settings={settings} onChanged={id => setChanges(current => [...current, id])} />
    <button onClick={() => setSettings({ table_header_fill: "#ffffff", table_border_color: "#112233" })}>합성 설정 변경</button>
    <output aria-label="확인된 디자인 변경">{JSON.stringify(changes)}</output>
  </>;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
