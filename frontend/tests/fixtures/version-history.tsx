import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { History } from "../../src/version-history";
import { api, jsonRequest, type Document, type Version } from "../../src/api";
import "../../src/style.css";
import "../../src/version-history.css";
function Fixture() {
  const [document, setDocument] = useState<Document>({ id: "document", name: "합성 보고서.hwpx", current_version: "v2", created_at: 1, role: "owner" });
  const [open, setOpen] = useState(true);
  return open ? <History document={document} onClose={() => setOpen(false)} onRestore={async (version, key) => {
    const result = await api<Version>(`/documents/document/versions/${version.id}/restore`, { ...jsonRequest("POST", {}), headers: { "Idempotency-Key": key } });
    setDocument({ ...document, current_version: result.id });
    return result;
  }} /> : <p>닫힘</p>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
