import { useEffect, useRef, useState } from "react";
import { api, downloadFile, errorMessage, fileUrl, type Document } from "./api";
import { EditorAdapter } from "./editor";
import { safeSvg } from "./block-insert";
import "./session-viewer.css";

/** Saved snapshot only: no edit lease, mutation commands, or automatic takeover. */
export function SessionViewer({ document: doc, onRetry, onClose, onCoedit, linkedSnapshot = false }: {
  document: Document; onRetry: () => void; onClose: () => void; onCoedit: () => void; linkedSnapshot?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null), engine = useRef<EditorAdapter | null>(null);
  const [owner, setOwner] = useState("다른 탭 또는 사용자"), [available, setAvailable] = useState(false);
  const [page, setPage] = useState(0), [pages, setPages] = useState(0), [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [collaborative, setCollaborative] = useState(false);
  useEffect(() => {
    let alive = true; const controller = new AbortController();
    async function check() {
      try {
        const value = await api<{active: boolean; editor: string | null; mode: string}>(`/documents/${doc.id}/session`, {signal: controller.signal});
        if (alive) { setAvailable(!value.active); setOwner(value.editor || "다른 탭 또는 사용자"); setCollaborative(value.active && value.mode === "collaborative"); }
      } catch (cause) { if (alive) setError(errorMessage(cause)); }
    }
    void check(); const timer = setInterval(check, 10000);
    void (async () => {
      const adapter = await EditorAdapter.mount(host.current!, {signal: controller.signal});
      if (!alive) { adapter.destroy(); return; } engine.current = adapter;
      const bytes = await downloadFile(fileUrl(doc, doc.current_version), {signal: controller.signal});
      await adapter.load(bytes, doc.name);
      const count = await adapter.sdk.pageCount();
      if (alive) setPages(count);
    })().catch(cause => { if (alive) setError(errorMessage(cause)); });
    return () => { alive = false; controller.abort(); clearInterval(timer); engine.current?.destroy(); engine.current = null; };
  }, [doc.id, doc.current_version]);
  useEffect(() => {
    let alive = true;
    if (pages && engine.current) void engine.current.sdk.getPageSvg(page).then(value => { if (alive) setSvg(safeSvg(value)); }).catch(cause => { if (alive) setError(errorMessage(cause)); });
    return () => { alive = false; };
  }, [pages, page]);
  return <main className="session-viewer">
    <header><button onClick={onClose}>문서함</button><strong>{doc.name}</strong><span>읽기 전용 · 저장본</span></header>
    <div role="status">{linkedSnapshot && <span>링크에 지정된 저장본입니다. 다른 참여자의 미저장 내용은 포함하지 않습니다.</span>}{doc.role === "reader" ? "읽기 권한으로 열었습니다." : available ? "편집권을 받을 수 있습니다. 최신 저장본으로 다시 엽니다." : `${owner} 편집 중 · 저장 후 편집을 종료하면 이어서 작업할 수 있습니다.`}
      {doc.role !== "reader" && <button onClick={onRetry}>{linkedSnapshot ? "최신 저장본 편집" : "편집권 다시 확인"}</button>}</div>
    {collaborative && doc.role !== "reader" && <button onClick={onCoedit}>공동편집으로 참여</button>}
    {error && <p role="alert">{error}</p>}
    <nav aria-label="저장본 페이지"><button disabled={!page} onClick={() => setPage(page - 1)}>이전 쪽</button><span>{pages ? page + 1 : 0} / {pages}</span><button disabled={!pages || page + 1 >= pages} onClick={() => setPage(page + 1)}>다음 쪽</button></nav>
    <div className="session-viewer-page" dangerouslySetInnerHTML={{__html: svg}} />
    <div ref={host} className="session-viewer-engine" aria-hidden="true" inert />
  </main>;
}
