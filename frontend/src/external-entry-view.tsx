import { useEffect, useRef, useState } from "react";
import { api, errorMessage, isAbortError, jsonRequest, type Document } from "./api";
import type { Account } from "./account-gate";
import type { ExternalEntry } from "./external-entry";
import { SessionViewer } from "./session-viewer";
import "./external-entry.css";
import {WorkspaceNav,WorkspaceIcon} from "./workspace-nav";

type LinkStatus = { enabled: boolean; linked: boolean; mattermost_user_id?: string };
export function MattermostLink({ account, token, onClose }: { account: Account; token?: string; onClose: () => void }) {
  const [status, setStatus] = useState<LinkStatus | null>(null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), [complete, setComplete] = useState(false), [attempt, setAttempt] = useState(0);
  const lock = useRef(false);
  const personal = account.authentication_required && !["guest", "local"].includes(account.user_id) && account.kind !== "guest";
  useEffect(() => {
    const controller = new AbortController();
    setError("");
    void api<LinkStatus>("/mattermost/link", { signal: controller.signal }).then(setStatus)
      .catch(cause => { if (!isAbortError(cause)) setError(errorMessage(cause)); });
    return () => controller.abort();
  }, [attempt]);
  async function change(method: "POST" | "DELETE") {
    if (lock.current || !personal || !status?.enabled) return;
    lock.current = true; setBusy(true); setError("");
    try {
      const result = await api<{ linked: boolean }>("/mattermost/link", method === "POST" ? jsonRequest("POST", { token }) : { method });
      setStatus({ enabled: true, linked: result.linked }); setComplete(true);
    } catch (cause) { setError(errorMessage(cause)); }
    finally { lock.current = false; setBusy(false); }
  }
  return <div className="workspace-shell"><WorkspaceNav active="documents" onBack={onClose} backLabel={complete?"문서함으로":"닫기"} disabled={busy}>
    {personal&&status?.enabled&&token&&!complete&&<button className="workspace-nav-route" aria-label="이 계정으로 연결" title="이 계정으로 연결" disabled={busy||status.linked} onClick={()=>void change("POST")}><WorkspaceIcon name="share"/><span>이 계정으로 연결</span></button>}
    {personal&&status?.enabled&&status.linked&&!complete&&<button className="workspace-nav-route" aria-label="연결 해제" title="연결 해제" disabled={busy} onClick={()=>void change("DELETE")}><WorkspaceIcon name="close"/><span>연결 해제</span></button>}
  </WorkspaceNav><main className="external-entry"><section aria-label="매터모스트 계정 연결">
    <h1>매터모스트 계정 연결</h1><p>현재 HWP Assistant 계정: <strong>{account.user_id}</strong></p>
    {!personal ? <p role="alert">승인된 개인 계정으로 로그인한 뒤 매터모스트에서 연결 링크를 다시 요청해 주세요. guest와 개인 작업 공간은 연결할 수 없습니다.</p>
      : complete ? <p role="status">{status?.linked ? "계정을 연결했습니다. 매터모스트에서 검색과 초안 요청을 사용할 수 있습니다." : "계정 연결을 해제했습니다."}</p>
      : !status ? <p role="status">연결 상태를 확인합니다.</p>
      : !status.enabled ? <p>이 서버에서 매터모스트 봇 연결을 사용하지 않습니다.</p>
      : token ? <><p>이 링크를 직접 요청한 경우에만 연결하세요. 매터모스트 요청은 위 계정의 문서 권한과 AI 이용 권한으로 처리됩니다.</p>
        {status.linked && <p>이미 연결된 계정이 있습니다. 다른 계정으로 연결하려면 기존 연결을 해제한 뒤 새 링크를 요청하세요.</p>}
        </>
      : <><p>{status.linked ? "매터모스트 계정이 연결되어 있습니다." : "연결된 계정이 없습니다. 매터모스트 봇에서 계정 연결을 요청하세요."}</p></>}

    {error && <p role="alert">{error}</p>}
    {error && !status && <button disabled={busy} onClick={() => setAttempt(value => value + 1)}>연결 상태 다시 확인</button>}
  </section></main></div>;
}

export function SavedDocumentEntry({ entry, onClose, onEdit, onCoedit }: {
  entry: Extract<ExternalEntry, { kind: "document" }>; onClose: () => void;
  onEdit: (document: Document) => void; onCoedit: (document: Document) => void;
}) {
  const [doc, setDoc] = useState<Document | null>(null), [error, setError] = useState(""), [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false), lock = useRef(false), alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setError("");
    void api<Document>(`/documents/${entry.documentId}`, { signal: controller.signal })
      .then(value => setDoc({ ...value, current_version: entry.versionId || value.current_version }))
      .catch(cause => { if (!isAbortError(cause)) setError(errorMessage(cause)); });
    return () => controller.abort();
  }, [entry.documentId, entry.versionId, attempt]);
  async function edit(collaborative: boolean) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try {
      // A search result pins a saved version. Editing always reopens the current
      // authorized document and acquires its own browser-bound editing session.
      const current = await api<Document>(`/documents/${entry.documentId}`);
      if (current.role === "reader") throw new Error("이 문서는 열람만 할 수 있습니다.");
      if (alive.current) (collaborative ? onCoedit : onEdit)(current);
    } catch (cause) { if (alive.current) setError(errorMessage(cause)); }
    finally { lock.current = false; if (alive.current) setBusy(false); }
  }
  if (doc) return <div className="external-saved-document">
    {(error || busy) && <div className="external-entry-status" role={error ? "alert" : "status"}>{error || "최신 저장본의 편집 권한을 확인합니다."}</div>}
    <SessionViewer document={doc} onClose={onClose} onRetry={() => void edit(false)} onCoedit={() => void edit(true)} linkedSnapshot />
  </div>;
  return <main className="external-entry"><section><h1>저장된 문서 열기</h1>
    {error ? <><p role="alert">{error}</p><button onClick={() => setAttempt(value => value + 1)}>다시 확인</button></> : <p role="status">문서 접근 권한을 확인합니다.</p>}
    <button onClick={onClose}>문서함으로</button></section></main>;
}
