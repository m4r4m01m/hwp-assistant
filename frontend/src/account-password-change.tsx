import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Account } from "./account-gate";
import { api, ApiError, errorMessage, jsonRequest } from "./api";
import "./account-password-change.css";

export function AccountPasswordChange({ account }: { account: Account }) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false);
  const [current, setCurrent] = useState(""), [password, setPassword] = useState(""), [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState(""), [done, setDone] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null), submitting = useRef(false);
  useEffect(() => { if (open && !dialog.current?.open) dialog.current?.showModal(); }, [open]);
  function close() {
    if (submitting.current) return;
    setOpen(false); setCurrent(""); setPassword(""); setConfirmation(""); setError(""); setDone(false);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting.current) return;
    if (password !== confirmation) { setError("새 비밀번호 확인이 일치하지 않습니다."); return; }
    if (current === password) { setError("현재 비밀번호와 다른 새 비밀번호를 입력하세요."); return; }
    submitting.current = true; setBusy(true); setError("");
    try {
      await api("/auth/password-change", { ...jsonRequest("POST", { current_password: current, new_password: password }), timeoutMs: 20000 });
      setCurrent(""); setPassword(""); setConfirmation(""); setDone(true);
    } catch (cause) {
      const uncertain = !(cause instanceof ApiError) || cause.status >= 500;
      setError(errorMessage(cause) + (uncertain ? " 변경됐을 수 있습니다. 반복해서 요청하지 말고 새 비밀번호로 다시 로그인해 확인하세요." : ""));
    } finally { submitting.current = false; setBusy(false); }
  }
  if (!account.can_change_password) return null;
  return <><button type="button" onClick={() => setOpen(true)}>비밀번호 변경</button>{open && createPortal(
    <dialog ref={dialog} className="account-password-dialog" aria-labelledby="account-password-title" onCancel={event => { event.preventDefault(); close(); }}>
      <h2 id="account-password-title">비밀번호 변경</h2>
      {done ? <><p role="status">비밀번호를 변경했습니다. 이 브라우저에서는 계속 사용할 수 있습니다.</p><button type="button" className="primary" onClick={close}>닫기</button></> : <>
        <p>다른 브라우저·기기는 다시 로그인해야 합니다. 진행 중인 문서는 먼저 저장하세요.</p>
        <form onSubmit={event => void submit(event)}>
          <input type="text" autoComplete="username" value={account.user_id} readOnly hidden />
          <label>현재 비밀번호<input type="password" autoComplete="current-password" value={current} disabled={busy} required minLength={8} maxLength={128} onChange={event => setCurrent(event.target.value)} autoFocus /></label>
          <label>새 비밀번호<input type="password" autoComplete="new-password" value={password} disabled={busy} required minLength={8} maxLength={128} onChange={event => setPassword(event.target.value)} /></label>
          <label>새 비밀번호 확인<input type="password" autoComplete="new-password" value={confirmation} disabled={busy} required minLength={8} maxLength={128} onChange={event => setConfirmation(event.target.value)} /></label>
          <small>비밀번호는 8~128자로 입력하세요.</small>
          {error && <p role="alert" className="error">{error}</p>}
          <div className="account-password-actions"><button type="button" disabled={busy} onClick={close}>취소</button><button type="submit" className="primary" disabled={busy}>{busy ? "변경 중…" : "변경"}</button></div>
        </form>
      </>}
    </dialog>, document.body)}</>;
}
