import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { EditorAdapter, type Snapshot } from "../../src/editor";
import { RewritePreview } from "../../src/rewrite-preview";
import { api, jsonRequest } from "../../src/api";
import type { RewriteTask } from "../../src/rewrite";

// Actual dialog, synthetic engine responses only. No WASM, model or live editor.
const snapshot = { revision: 0, state: { documentEpoch: 1, changeSeq: 0, documentSha256: "a".repeat(64) },
  selection: { target: { kind: "body_paragraph", section: 0, paragraph: 1, charOffset: 0, length: 5 } },
  evidence: { text: "합성 원문", textSha256: "a".repeat(64), formatSha256: "b".repeat(64), adjacentContextSha256: "c".repeat(64) } } as Snapshot;
const task = { id: "preview-task", document_id: "document", base_version: "version", status: "ready", stale: false,
  snapshot, candidates: [{ id: "candidate-1", ordinal: 1, replacement: "합성 수정", reason: "합성 시험" },
    { id: "candidate-2", ordinal: 2, replacement: "다른 수정", reason: "합성 시험" }], application: null } as RewriteTask;
EditorAdapter.mount = async () => {
  let applied = false;
  return {
    load: async () => { applied = false; }, selection: () => structuredClone(snapshot),
    apply: async () => { applied = true; }, exportHwpx: () => ({ contentLoss: { count: 0 } }), destroy: () => {},
    sdk: { focusTarget: async () => ({ focused: true, page: 1 }), pageCount: async () => 1,
      getPageSvg: async () => `<svg xmlns="http://www.w3.org/2000/svg" width="595" height="842"><text x="20" y="30">${applied ? "합성 수정" : "합성 원문"}</text></svg>` },
  } as unknown as EditorAdapter;
};
function Harness() {
  const [open, setOpen] = useState(true), [candidate, setCandidate] = useState(task.candidates[0]);
  return <>{open ? <RewritePreview key={candidate.id} task={task} candidate={candidate} capture={async () => new Uint8Array([1, 2, 3])}
    onCandidateChange={setCandidate} onClose={() => setOpen(false)} approve={async applicationId => {
      await api("/preview-test/approve", jsonRequest("POST", { id: applicationId, candidate: candidate.id }));
    }} /> : <p>비교 종료</p>}</>;
}
createRoot(document.getElementById("root")!).render(<Harness />);
