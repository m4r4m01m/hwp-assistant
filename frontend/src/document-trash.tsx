import { useEffect, useRef, useState } from "react";
import { api, errorMessage, isAbortError, type Document } from "./api";
import { useComparisonDialog } from "./version-comparison";
import { WorkspaceIcon } from "./workspace-nav";
import "./document-trash.css";

type TrashDocument = Document & { trash_id: string; deleted_at: number };

export function DocumentTrashDialog({ document: target, onClose, onChanged }: {
  document: Document | null; onClose: () => void; onChanged: () => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  const requestId = useRef(crypto.randomUUID());
  const [rows, setRows] = useState<TrashDocument[]>([]);
  const [loading, setLoading] = useState(!target);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  useComparisonDialog(dialog, () => { if (!busy) onClose(); });
  useEffect(() => {
    if (target) return;
    const controller = new AbortController();
    setLoading(true); setError("");
    api<TrashDocument[]>("/document-trash", { signal: controller.signal })
      .then(setRows).catch(e => { if (!isAbortError(e)) setError(errorMessage(e)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [target, reload]);

  async function move() {
    if (!target || busy) return;
    setBusy(true); setError("");
    try {
      await api(`/documents/${target.id}`, { method: "DELETE", headers: { "Idempotency-Key": requestId.current } });
      onChanged(); onClose();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  async function restore(row: TrashDocument) {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try {
      await api(`/document-trash/${row.trash_id}/restore`, { method: "POST" });
      setRows(current => current.filter(item => item.trash_id !== row.trash_id));
      setNotice(`${row.name} 문서를 복구했습니다.`); onChanged();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <div className="modal document-trash-modal">
    <section ref={dialog} role="dialog" aria-modal="true" aria-labelledby="document-trash-title" aria-busy={busy}>
      <header><h2 id="document-trash-title"><WorkspaceIcon name="trash"/>{target ? "휴지통으로 이동" : "휴지통"}</h2>
        <button disabled={busy} onClick={onClose} aria-label="휴지통 닫기"><WorkspaceIcon name="close"/></button></header>
      {target ? <>
        <p><strong>{target.name}</strong> 문서를 휴지통으로 이동할까요?</p>
        <p>문서함에서 숨겨지고 공유 중인 사용자도 열 수 없습니다. 원본과 버전 기록은 보존되며 휴지통에서 복구할 수 있습니다.</p>
      </> : <>
        <p>내가 소유한 문서만 표시됩니다. 복구하면 기존 내용·버전 기록·공유 권한을 그대로 사용할 수 있습니다.</p>
        <button disabled={busy || loading} onClick={() => setReload(value => value + 1)}><WorkspaceIcon name="refresh" size={17}/>새로고침</button>
        {loading ? <p role="status">휴지통을 불러오는 중…</p> : rows.length === 0 ? <p className="empty">휴지통이 비어 있습니다.</p> :
          <ul className="document-trash-list">{rows.map(row => <li key={row.trash_id}>
            <div><strong>{row.name}</strong><small>{new Date(row.deleted_at * 1000).toLocaleString("ko-KR")} 이동</small></div>
            <button disabled={busy} onClick={() => void restore(row)} aria-label={`${row.name} 복구`}><WorkspaceIcon name="undo" size={17}/>복구</button>
          </li>)}</ul>}
      </>}
      {notice && <p className="notice" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {target && <footer><button disabled={busy} onClick={onClose}>취소</button>
        <button className="danger" disabled={busy} onClick={() => void move()}>{busy ? "이동 중…" : "휴지통으로 이동"}</button></footer>}
    </section>
  </div>;
}
