import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { api, jsonRequest, type Document } from "../../src/api";
import { DocumentAssistantPanel, type AssistantTurn } from "../../src/document-assistant";
import { CapabilitiesProvider } from "../../src/capabilities";

// Actual panel, synthetic document and transport only. This fixture does not
// load WASM or start an AI provider and is not a production build entry point.
const document: Document = {
  id: "synthetic-document", name: "합성 대화 문서.hwpx", current_version: "saved-version", created_at: 1, role: "owner",
};
const noop = () => {};
function Harness() {
  const [shown, setShown] = useState(true), [thread, setThread] = useState("thread-a"), [holding, setHolding] = useState(false), [previewTask, setPreviewTask] = useState("");
  return <>
    <button onClick={() => setShown(value => !value)}>{shown ? "대화 패널 숨기기" : "대화 패널 보이기"}</button>
    <button onClick={() => setThread("thread-b")}>대화 B 열기</button>
    <output data-testid="assistant-hold">{holding ? "응답 확인 중" : "대기"}</output>
    <output data-testid="assistant-preview-task">{previewTask}</output>
    <aside className="conversation-panel" hidden={!shown}>
      <DocumentAssistantPanel key={thread} thread={thread} document={document} active={shown}
        onDirty={noop} onActive={setHolding} onShowRewrite={task => setPreviewTask(task || "manual")}
        captureDocument={async () => ({ base_version: document.current_version, selection: null,
          document_context: { text: "합성 본문", sha256: "a".repeat(64), truncated: false, revision: 0, epoch: 1 } })}
        sendAssistant={(id, value) => api<AssistantTurn>(`/threads/${id}/assistant-turns`, jsonRequest("POST", value))}
        requestRewrite={async (id, instruction) => { const value = await api<{ id: string }>(`/threads/${id}/rewrite-tasks`, jsonRequest("POST", { instruction })); return value.id; }} />
    </aside>
  </>;
}
createRoot(window.document.getElementById("root")!).render(<CapabilitiesProvider><Harness /></CapabilitiesProvider>);
