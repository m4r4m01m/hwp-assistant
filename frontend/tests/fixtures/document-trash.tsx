import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { DocumentTrashDialog } from "../../src/document-trash";
import "../../src/style.css";

function Fixture() {
  const [open, setOpen] = useState(true), [changed, setChanged] = useState(0);
  const target = new URLSearchParams(location.search).get("mode") === "delete"
    ? { id: "synthetic-doc", name: "합성 문서.hwpx", current_version: "v1", created_at: 1, role: "owner" as const } : null;
  return <><output aria-label="목록 갱신 횟수">{changed}</output>{open && <DocumentTrashDialog document={target}
    onClose={() => setOpen(false)} onChanged={() => setChanged(count => count + 1)} />}</>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
