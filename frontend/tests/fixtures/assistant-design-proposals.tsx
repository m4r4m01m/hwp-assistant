import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { api, jsonRequest, type Document } from "../../src/api";
import { DocumentAssistantPanel, type AssistantTurn } from "../../src/document-assistant";
import { CapabilitiesProvider } from "../../src/capabilities";
import { assistantDesignContext } from "../../src/document-assistant-context";
import type { StyleTarget } from "../../src/editor";
import { zipSync } from "fflate";

Object.assign(window, { testDesignContext: (section: string, header: string, target: StyleTarget | null) => assistantDesignContext({
  bytes: zipSync({ "Contents/section0.xml": new TextEncoder().encode(section), "Contents/header.xml": new TextEncoder().encode(header) }),
  contentLoss: { count: 0, losses: [] }, revision: 7, epoch: 2,
}, target) });

// Actual conversation panel with a synthetic buffer. No engine or AI runs here.
const doc: Document = { id: "design-document", name: "합성 문서.hwpx", current_version: "saved-version", created_at: 1, role: "owner" };
const noop = () => {};
function Harness() {
  const [reader, setReader] = useState(false), [last, setLast] = useState("");
  return <>
    <button onClick={() => setReader(value => !value)}>읽기 권한 전환</button>
    <output data-testid="design-preview-request">{last}</output>
    <aside className="conversation-panel">
      <DocumentAssistantPanel thread="design-thread" document={{ ...doc, role: reader ? "reader" : "owner" }}
        onDirty={noop} onActive={noop} onShowRewrite={noop}
        captureDocument={async () => ({ base_version: doc.current_version, selection: null,
          document_context: { text: "현재 미저장 표의 합성 내용", sha256: "a".repeat(64), truncated: false, revision: 7, epoch: 2 },
          design_context: { source_sha256: "b".repeat(64), target: { kind: "table", section: 0, paragraph: 3, control: 0 } } })}
        sendAssistant={(id, value) => api<AssistantTurn>(`/threads/${id}/assistant-turns`, jsonRequest("POST", value))}
        onDesignProposal={(proposal, turn) => setLast(JSON.stringify({ proposal, design_context: turn.design_context,
          revision: turn.document_context.revision, epoch: turn.document_context.epoch, base_version: turn.base_version }))} />
    </aside>
  </>;
}
createRoot(document.getElementById("root")!).render(<CapabilitiesProvider><Harness /></CapabilitiesProvider>);
