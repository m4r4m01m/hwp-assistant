import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { DocumentBranches } from "../../src/document-branches";
import { api, type Version } from "../../src/api";
import "../../src/style.css";
function Fixture() {
  const [open, setOpen] = useState(true), [updates, setUpdates] = useState(0), [opened, setOpened] = useState("");
  return <><output aria-label="목록 갱신 횟수">{updates}</output><output aria-label="편집 문서">{opened}</output>{open && <DocumentBranches
    document={{id: "source", name: "합성 보고서.hwpx", current_version: "v2", created_at: 1, role: "owner"}}
    onClose={() => setOpen(false)} onUpdated={() => setUpdates(value => value + 1)} onOpen={document => setOpened(document.id)} onCoedit={document => setOpened(`coedit:${document.id}`)}
    onRestore={(document,version,key)=>api<Version>(`/documents/${document.id}/versions/${version}/restore?base_version=${document.current_version}`,{method:"POST",headers:{"Idempotency-Key":key}})} />}</>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
