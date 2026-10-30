import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, jsonRequest, type Document } from "./api";
import type { TeamSpace } from "./team-workspaces";

type Pending = { mode: "move" | "copy"; target: string; url: string; body: object };
export function DocumentSend({ document: doc, onClose, onSent }: {
  document: Document; onClose: () => void; onSent: (target: string, copied: boolean) => void;
}) {
  const [spaces, setSpaces] = useState<TeamSpace[]>([]), [target, setTarget] = useState("");
  const [mode, setMode] = useState<"move" | "copy">("move");
  const [name, setName] = useState(doc.name), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true);
  const [error, setError] = useState(""), [pending, setPending] = useState<Pending | null>(null);
  const gate = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    api<TeamSpace[]>("/workspaces/send-targets", { signal: controller.signal }).then(rows => {
      setSpaces(rows);
    }).catch(e => { if (!controller.signal.aborted) setError(errorMessage(e)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [doc.id, doc.workspace_id]);
  async function send() {
    if (gate.current) return;
    const frozen: Pending = pending || { mode, target,
      url: mode === "copy" ? `/documents/${doc.id}/copies` : `/documents/${doc.id}/workspace`,
      body: mode === "copy" ? { workspace_id: target, document_id: crypto.randomUUID(), source_version: doc.current_version,
        request_id: crypto.randomUUID(), name: name.trim() } : { workspace_id: target, expected_version: doc.current_version } };
    gate.current = true; setBusy(true); setPending(frozen); setError("");
    try {
      await api(frozen.url, jsonRequest(frozen.mode === "copy" ? "POST" : "PUT", frozen.body));
      setPending(null); onSent(frozen.target, frozen.mode === "copy");
    } catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) setPending(null);
      setError(errorMessage(e));
    } finally { gate.current = false; setBusy(false); }
  }
  const blocked = busy || !!pending;
  const destination = spaces.find(s => s.id === target);
  return <dialog className="library-send-dialog library-folder-dialog" aria-labelledby="document-send-title"
    ref={el => { if (el && !el.open) el.showModal(); }} onCancel={e => { e.preventDefault(); if (!blocked) onClose(); }}>
    <form onSubmit={e => { e.preventDefault(); void send(); }}>
      <h2 id="document-send-title">부서·팀으로 보내기</h2><p className="library-send-source">{doc.name}</p>
      <fieldset disabled={blocked}><legend>보내는 방법</legend>
        <label><input type="radio" name="send-mode" checked={mode === "move"} onChange={() => setMode("move")}/>문서 이동 <small>원본과 이력을 함께 옮깁니다.</small></label>
        <label><input type="radio" name="send-mode" checked={mode === "copy"} onChange={() => setMode("copy")}/>사본 보내기 <small>원본은 유지하고 현재 저장본만 복사합니다.</small></label>
      </fieldset>
      <label>받는 문서함<select required value={target} disabled={blocked || loading} onChange={e => setTarget(e.target.value)}>
        <option value="">{loading ? "문서함을 불러오는 중…" : "부서 또는 팀을 선택하세요"}</option>
        {spaces.map(s => <option key={s.id} value={s.id} disabled={!(["manager","editor"].includes(s.role||""))}>{s.kind === "department" ? "부서" : "팀"} · {s.name}{!(["manager","editor"].includes(s.role||"")) ? " · 편집 권한 필요" : ""}</option>)}
      </select></label>
      {!loading&&!spaces.length&&<p role="status">지정된 소속 부서·팀이 없습니다. 관리자에게 소속 지정을 요청하세요.</p>}
      {mode === "copy" && <label>사본 이름<input required maxLength={235} value={name} disabled={blocked} onChange={e => setName(e.target.value)}/></label>}
      <p>{mode === "move" ? "받는 문서함 구성원에게 공개됩니다. 이전 문서함과 개별 초대자의 공유 권한은 해제됩니다. 저장 후 편집을 종료해 주세요." : "사본은 받는 문서함 구성원에게 공개됩니다. 원본의 위치·공유 권한·이력은 유지되고, 사본에는 과거 이력과 대화가 포함되지 않습니다."}</p>
      <p className="hint">본인 문서만 자기 소속 부서·팀에 보낼 수 있으며, 받는 문서함의 편집 권한이 필요합니다. 비밀 문서와 문서 수정안은 보낼 수 없습니다.</p>
      {error && <p role="alert">{error}</p>}
      {pending && !busy && <p role="status">응답을 확인하지 못했습니다. 같은 요청을 다시 확인하면 중복 사본을 만들지 않습니다.</p>}
      <div className="library-folder-dialog-actions"><button type="button" disabled={blocked} onClick={onClose}>취소</button>
        <button className="primary" disabled={busy || loading || !destination || !(["manager","editor"].includes(destination.role||"")) || (!pending && (mode === "move" ? target === doc.workspace_id : !name.trim()))}>
          {busy ? "보내는 중…" : pending ? "전송 결과 다시 확인" : mode === "copy" ? "사본 보내기" : "이동하기"}
        </button></div>
    </form>
  </dialog>;
}
