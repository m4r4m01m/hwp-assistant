import React, { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api, ApiError, errorMessage, jsonRequest } from "./api";
import { scheduleEngineWarmup, startEngineWarmup, subscribeWarmup, warmState } from "./engine-warmup";
import "./account-gate.css";

export type Account = { user_id: string; authentication_required: boolean; name?: string; kind?: string; is_admin?: boolean; library_access_mode?: "private" | "shared" };
type SampleAccount = { user_id: string; label: string };
type Policy = { accounts_enabled: boolean; registration_enabled: boolean; guest_enabled: boolean; sample_accounts?: SampleAccount[]; registration_requires_approval?: boolean; token_login_enabled?: boolean; library_access_mode?: "private" | "shared" };
const rememberedKey = "hwp-remembered-usernames-v1";
function remembered(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(rememberedKey) || "[]");
    return Array.isArray(value) ? [...new Set(value.filter((item): item is string => typeof item === "string" && /^[a-z0-9][a-z0-9_.@-]{0,79}$/.test(item) && item !== "guest"))].slice(0, 5) : [];
  } catch { return []; }
}
function remember(username: string) {
  if (username === "guest") return;
  try { localStorage.setItem(rememberedKey, JSON.stringify([username, ...remembered().filter(value => value !== username)].slice(0, 5))); } catch { /* Private mode: login still works. */ }
}
export function clearAccountWorkspaceState() {
  try {
    for (const key of Object.keys(sessionStorage)) if (key.startsWith("hwp-report-")) sessionStorage.removeItem(key);
  } catch { /* Storage can be disabled. Never make logout depend on it. */ }
}
function WarmupNotice() {
  const value = useSyncExternalStore(subscribeWarmup, warmState);
  const text = value.phase === "ready" ? "편집 자원 다운로드 완료 · WASM 컴파일 완료 · 문서를 열 때 엔진 초기화·문서 배치를 진행합니다."
    : value.phase === "loading" && value.stage === "compile" ? "편집 자원 다운로드 완료 · WASM을 컴파일합니다. 로그인은 계속할 수 있습니다."
    : value.phase === "loading" ? `로그인하는 동안 한글 편집 자원을 다운로드합니다${value.total ? ` · ${value.completed}/${value.total}개` : "…"}`
    : value.phase === "deferred" ? "데이터 절약·오프라인·비활성 화면에서는 미리 받기를 보류합니다."
    : value.phase === "failed" && value.stage === "compile" ? "편집 자원 다운로드는 완료했지만 WASM 사전 컴파일을 완료하지 못했습니다. 문서를 열 때 다시 준비하며 로그인은 계속할 수 있습니다."
    : value.phase === "failed" ? "편집 자원을 미리 받지 못했습니다. 문서를 열 때 다시 준비하며 로그인은 계속할 수 있습니다."
    : "로그인 화면에서 한글 편집 자원을 미리 준비합니다.";
  return <div className="engine-warmup" aria-live="polite"><p>{text}</p>{["failed", "deferred"].includes(value.phase) && <button type="button" onClick={() => void startEngineWarmup(true)}>편집 자원 미리 받기</button>}</div>;
}

export function AccountGate({ children }: { children: (account: Account) => React.ReactNode }) {
  const [account, setAccount] = useState<Account | null>(null), [policy, setPolicy] = useState<Policy | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(""), [attempt, setAttempt] = useState(0);
  const [mode, setMode] = useState<"choose" | "guest" | "login" | "register" | "token">("choose");
  const [username, setUsername] = useState(""), [password, setPassword] = useState(""), [confirmation, setConfirmation] = useState("");
  const [name, setName] = useState(""), [employee, setEmployee] = useState(""), [token, setToken] = useState("");
  const [registered, setRegistered] = useState("");
  const [busy, setBusy] = useState(false), [rememberId, setRememberId] = useState(false), [profiles, setProfiles] = useState(remembered);
  const lock = useRef(false);
  useEffect(scheduleEngineWarmup, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    void (async () => {
      try {
        const value = await api<Account>("/auth/me", { signal: controller.signal, timeoutMs: 12000 });
        if (!controller.signal.aborted) setAccount(value);
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (!(cause instanceof ApiError && cause.status === 401)) { setError(errorMessage(cause)); return; }
        try {
          const value = await api<Policy>("/auth/status", { signal: controller.signal, timeoutMs: 12000 });
          if (!controller.signal.aborted) setPolicy(value);
        } catch (reason) { if (!controller.signal.aborted) setError(errorMessage(reason)); }
      } finally { if (!controller.signal.aborted) setLoading(false); }
    })();
    return () => controller.abort();
  }, [attempt]);
  function select(next: typeof mode, user = "") {
    if (lock.current) return;
    setRegistered(""); setMode(next); setUsername(user); setPassword(""); setConfirmation(""); setToken(""); setError("");
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (lock.current) return;
    const legacy = !policy?.accounts_enabled || mode === "token";
    if (legacy && !token.trim()) return;
    if (mode === "register" && password !== confirmation) { setError("비밀번호 확인이 일치하지 않습니다."); return; }
    lock.current = true; setBusy(true); setError("");
    try {
      const route = legacy ? "/auth/login" : mode === "register" ? "/auth/register" : "/auth/password-login";
      const input = legacy ? { token } : { username: mode === "guest" ? "guest" : username, password,
        ...(mode === "register" ? { name, employee_number: employee } : {}) };
      const result = await api<{ status?: string; user_id?: string }>(route, { ...jsonRequest("POST", input), timeoutMs: 20000 });
      setPassword(""); setConfirmation(""); setToken("");
      if (mode === "register" && result.status === "pending") {
        setRegistered(result.user_id || username); setName(""); setEmployee("");
        return;
      }
      const value = await api<Account>("/auth/me", { timeoutMs: 12000 });
      if (rememberId && !legacy) remember(value.user_id);
      clearAccountWorkspaceState(); setAccount(value);
    } catch (cause) {
      const unknownRegistration = mode === "register" && (!(cause instanceof ApiError) || cause.status >= 500);
      setError(errorMessage(cause) + (unknownRegistration ? " · 가입 요청이 접수됐을 수 있습니다. 다시 가입하지 말고 관리자에게 확인하거나 승인 후 같은 아이디로 로그인하세요." : ""));
    } finally { lock.current = false; setBusy(false); }
  }
  if (account) return children(account);
  if (loading) return <main className="drive auth-screen"><p role="status">문서함 연결 중…</p><WarmupNotice /></main>;
  const legacy = !policy?.accounts_enabled || mode === "token";
  const samples = policy?.sample_accounts || [];
  const personalProfiles = profiles.filter(user => !samples.some(sample => sample.user_id === user));
  const selectedSample = samples.find(sample => sample.user_id === username);
  const sharedLibrary = policy?.library_access_mode === "shared";
  return <main className="account-page"><section className="account-panel" aria-label="문서함 로그인">
    <header><span className="account-eyebrow">HWP ASSISTANT · PoC</span><h1>HWPX 문서함 로그인</h1><p>한글 문서 작성부터 AI 검토까지, 사용할 작업 공간을 선택하세요.</p></header>
    {registered ? <section className="account-pending" aria-label="가입 요청 접수">
      <h2>가입 승인을 기다리고 있습니다</h2>
      <p role="status"><strong>{registered}</strong> 계정의 가입 요청을 접수했습니다. 관리자 승인 후 로그인할 수 있습니다.</p>
      <p className="account-note">비밀번호를 다시 보내거나 같은 아이디로 재가입할 필요가 없습니다.</p>
      <button type="button" className="primary" onClick={() => select("login", registered)}>로그인 화면으로</button>
    </section> : !legacy && mode === "choose" ? <>
      {!!samples.length && <section aria-label="샘플 계정 선택">
        <h2>샘플 계정 선택</h2>
        <div className="account-choices">
          {samples.map(sample => <button className="account-card" key={sample.user_id} onClick={() => select("login", sample.user_id)}><span aria-hidden="true" className="account-avatar">S</span><strong>{sample.label}</strong><small>{sample.user_id} · 비밀번호 필요</small></button>)}
        </div>
        <p className="account-note">{sharedLibrary ? "모든 계정이 같은 공용 문서함에서 작업합니다. 작업 이력은 로그인한 계정으로 기록됩니다." : "각 샘플 계정은 별도 문서함을 사용하며 같은 계정으로 접속한 체험자끼리는 문서를 공유합니다."} AI 이용 권한은 관리자 설정에 따릅니다.</p>
      </section>}
      <div className="account-choices">
        <button className="account-card" disabled={!policy?.guest_enabled} onClick={() => select("guest")}><span aria-hidden="true" className="account-avatar">G</span><strong>guest로 체험</strong><small>{policy?.guest_enabled ? "공용 체험 공간 · 비밀번호 필요" : "관리자의 guest 설정이 필요합니다"}</small></button>
        {personalProfiles.map(user => <button className="account-card" key={user} onClick={() => select("login", user)}><span aria-hidden="true" className="account-avatar">{user[0].toUpperCase()}</span><strong>{user}</strong><small>기억한 아이디 · 비밀번호 필요</small></button>)}
        <button className="account-card" onClick={() => select("login")}><span aria-hidden="true" className="account-avatar">ID</span><strong>내 계정으로 로그인</strong><small>{sharedLibrary ? "같은 공용 문서함에서 작업" : "개인 문서와 공유받은 문서"}</small></button>
        <button className="account-card" disabled={!policy?.registration_enabled} onClick={() => select("register")}><span aria-hidden="true" className="account-avatar">+</span><strong>계정 만들기</strong><small>{policy?.registration_enabled ? "가입 신청 · 관리자 승인 후 이용" : "현재 계정 생성이 닫혀 있습니다"}</small></button>
      </div>
      <p className="account-note">{sharedLibrary ? "모든 계정이 같은 공용 문서함을 사용합니다. 작업 이력은 계정별로 남습니다." : "guest에 저장한 문서는 다른 guest 체험자도 볼 수 있습니다."} 실제 회사 자료나 개인정보는 올리지 마세요.</p>
      {!!profiles.length && <button className="account-text-button" onClick={() => { try { localStorage.removeItem(rememberedKey); } catch {} setProfiles([]); }}>이 브라우저에 기억한 아이디 지우기</button>}
      {policy?.token_login_enabled !== false && <details className="account-legacy"><summary>기존 인증 토큰으로 로그인</summary><button onClick={() => select("token")}>토큰 입력 화면</button></details>}
    </> : <>
      {!legacy && <h2>{mode === "guest" ? "guest 공용 체험" : mode === "register" ? "새 계정 가입 신청" : "내 계정 로그인"}</h2>}
      {!legacy && mode === "login" && selectedSample && <p className="account-note">선택한 샘플: {selectedSample.label} ({selectedSample.user_id}). 공유받은 비밀번호로 로그인하세요.</p>}
      <form onSubmit={event => void submit(event)} aria-label={mode === "register" ? "회원가입" : "로그인"}>
        {legacy ? <label>인증 토큰<input aria-label="인증 토큰" type="password" autoComplete="off" value={token} disabled={busy} onChange={event => setToken(event.target.value)} required /></label> : <>
          {mode !== "guest" && <label>아이디<input aria-label="아이디" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} value={username} minLength={mode === "register" ? 3 : 1} maxLength={mode === "register" ? 40 : 80} disabled={busy} onChange={event => setUsername(event.target.value)} required /></label>}
          <label>비밀번호<input aria-label="비밀번호" name="password" type="password" autoComplete={mode === "register" ? "new-password" : "current-password"} value={password} minLength={8} maxLength={128} disabled={busy} onChange={event => setPassword(event.target.value)} required /></label>
          {mode === "register" && <>
            <label>비밀번호 확인<input aria-label="비밀번호 확인" type="password" autoComplete="new-password" value={confirmation} minLength={8} maxLength={128} disabled={busy} onChange={event => setConfirmation(event.target.value)} required /></label>
            <label>이름<input aria-label="이름" name="name" autoComplete="name" value={name} maxLength={80} disabled={busy} onChange={event => setName(event.target.value)} required /></label>
            <label>사번<input aria-label="사번" name="employee_number" autoComplete="off" value={employee} maxLength={32} pattern="[A-Za-z0-9-]+" disabled={busy} onChange={event => setEmployee(event.target.value)} required /></label>
            <p className="account-note">아이디는 영문·숫자·._@-를 사용하세요. 비밀번호는 8~128자입니다. 사번은 계정 구분용이며 재직 여부를 검증하지 않습니다. 가입 후 관리자 승인이 필요합니다. PoC에서는 가상 이름·사번을 권장합니다.</p>
          </>}
          {mode === "login" && <label className="account-remember"><input type="checkbox" checked={rememberId} onChange={event => setRememberId(event.target.checked)} />이 브라우저에 아이디 기억 (공용 기기에서는 해제)</label>}
          {mode === "guest" && <p className="account-note">관리자가 부여한 guest 계정의 비밀번호를 입력하세요. 여러 체험자의 작업이 모두 guest로 기록됩니다. 공용 문서함이므로 민감한 자료는 올리지 마세요.</p>}
        </>}
        <div className="account-actions"><button type="submit" className="primary" disabled={busy || legacy && !token.trim()}>{busy ? "로그인 확인 중…" : mode === "register" ? "가입 승인 요청" : "로그인"}</button>{policy?.accounts_enabled && <button type="button" disabled={busy} onClick={() => select("choose")}>계정 선택으로</button>}</div>
      </form>
      {mode === "register" && <button type="button" className="account-text-button" disabled={busy} onClick={() => select("login", username)}>이미 만든 계정으로 로그인</button>}
    </>}
    {error && <div className="error" role="alert"><p>{error}</p><button disabled={busy} onClick={() => setAttempt(value => value + 1)}>연결 다시 확인</button></div>}
    <WarmupNotice />
  </section></main>;
}
