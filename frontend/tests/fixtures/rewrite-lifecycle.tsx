import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { api, jsonRequest, type Document } from "../../src/api";
import { CapabilitiesProvider } from "../../src/capabilities";
import type { WritingContext } from "../../src/context";
import { RewritePanel } from "../../src/rewrite";

// Actual rewrite panel with synthetic state transitions and mocked HTTP only.
// Updating a prop models a completed context save or a new document version.
const document: Document = {
  id: "synthetic-document", name: "합성 수정 문서.hwpx", current_version: "saved-version", created_at: 1, role: "owner",
};
const context: WritingContext = {
  revision: 1, base_version: "saved-version", stale: false, purpose: "합성 작성 목적", facts: "",
  selection: null, writing_criteria: "합성 기준", confirmed_facts: [], confirmation: { id: "confirmation", by: "test-user", at: 1 },
};
function Harness() {
  const [version, setVersion] = useState(document.current_version), [revision, setRevision] = useState(context.revision);
  return <>
    <button onClick={() => setRevision(2)}>작성 정보 리비전 2 반영</button>
    <button onClick={() => setVersion("next-version")}>새 문서 저장본 반영</button>
    <RewritePanel thread="thread-rewrite" document={{ ...document, current_version: version }} context={{ ...context, revision }}
      approveRewrite={async (task, candidate, application) => {
        await api(`/rewrite-tasks/${task}/approve`, jsonRequest("POST", { id: application, candidate_id: candidate }));
      }} />
  </>;
}
createRoot(window.document.getElementById("root")!).render(<CapabilitiesProvider><Harness /></CapabilitiesProvider>);
