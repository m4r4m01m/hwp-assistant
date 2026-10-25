import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, errorMessage, isAbortError, jsonRequest } from "./api";
import "./ai-connection.css";

type ConnectionStatus = {
  deployment_mode: "local" | "server";
  can_configure: boolean;
  needs_setup: boolean;
  choice: "codex" | "later" | null;
  codex: {
    installed: boolean;
    auth_state: "unchecked" | "chatgpt" | "signed_out" | "unsupported" | "timeout" | "error" | "not_installed";
    checked_at: number | null;
    message: string;
  };
  features: { id: string; label: string; provider: string; configured: boolean; allowed: boolean; message: string }[];
};
type ConnectionContext = { available: boolean; open: () => void; setSafeLanding: (safe: boolean) => void };
const Context = createContext<ConnectionContext>({ available: false, open: () => {}, setSafeLanding: () => {} });

export const useAiConnection = () => useContext(Context);

/** Only a quiet library landing may introduce setup automatically. Editors stay mounted and focused. */
export function useAiSetupLanding(safe: boolean) {
  const { setSafeLanding } = useAiConnection();
  useEffect(() => { setSafeLanding(safe); return () => setSafeLanding(false); }, [safe, setSafeLanding]);
}

const authLabels: Record<ConnectionStatus["codex"]["auth_state"], string> = {
  unchecked: "로그인 확인 전", chatgpt: "ChatGPT 로그인 확인됨", signed_out: "ChatGPT 로그인 필요",
  unsupported: "ChatGPT 로그인 필요", timeout: "확인 시간 초과", error: "확인하지 못함", not_installed: "설치 필요",
};

export function AiConnectionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<ConnectionStatus | null>(null);
  const [open, setOpen] = useState(false), [safeLanding, setSafeLanding] = useState(false);
  const [busy, setBusy] = useState<"" | "loading" | "checking" | "saving">("");
  const [error, setError] = useState(""), [allowDiscard, setAllowDiscard] = useState(false);
  const statusRef = useRef(status), busyRef = useRef(busy), openRef = useRef(open);
  statusRef.current = status; busyRef.current = busy; openRef.current = open;
  const introduced = useRef(false), requestId = useRef(0), controller = useRef<AbortController | null>(null);
  const dialog = useRef<HTMLDialogElement | null>(null), title = useRef<HTMLHeadingElement | null>(null);

  const begin = useCallback((kind: typeof busy) => {
    controller.current?.abort();
    const pending = new AbortController(); controller.current = pending;
    const id = ++requestId.current;
    busyRef.current = kind; setBusy(kind); setError("");
    return { pending, id };
  }, []);
  const finish = useCallback((id: number) => {
    if (id === requestId.current) { busyRef.current = ""; setBusy(""); }
  }, []);
  const load = useCallback(async () => {
    const { pending, id } = begin("loading");
    try {
      const value = await api<ConnectionStatus>("/ai-connection", { signal: pending.signal, timeoutMs: 12000 });
      if (id === requestId.current && !pending.signal.aborted) { statusRef.current = value; setStatus(value); }
    } catch (cause) {
      if (id === requestId.current && !isAbortError(cause)) setError(errorMessage(cause));
    } finally { finish(id); }
  }, [begin, finish]);
  useEffect(() => { void load(); return () => { requestId.current++; controller.current?.abort(); }; }, [load]);

  useEffect(() => {
    if (!introduced.current && safeLanding && status?.deployment_mode === "local" && status.can_configure && status.needs_setup && !busy) {
      introduced.current = true; openRef.current = true; setOpen(true);
    }
  }, [safeLanding, status, busy]);

  const openDialog = useCallback(() => {
    if (openRef.current) return;
    introduced.current = true; openRef.current = true; setOpen(true); setAllowDiscard(false);
    void load();
  }, [load]);
  const closeNow = useCallback(() => {
    requestId.current++; controller.current?.abort(); busyRef.current = ""; setBusy("");
    openRef.current = false; setOpen(false); setAllowDiscard(false); setError("");
  }, []);
  useEffect(() => {
    if (!open || !dialog.current) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const element = dialog.current;
    element.showModal(); title.current?.focus();
    return () => { element.close(); if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, [open]);

  async function check() {
    if (busyRef.current || !statusRef.current?.can_configure) return;
    const { pending, id } = begin("checking");
    try {
      const value = await api<ConnectionStatus>("/ai-connection/check", { ...jsonRequest("POST", {}), signal: pending.signal, timeoutMs: 15000 });
      if (id === requestId.current && !pending.signal.aborted) { statusRef.current = value; setStatus(value); }
    } catch (cause) {
      if (id === requestId.current && !isAbortError(cause)) setError(errorMessage(cause));
    } finally { finish(id); }
  }
  async function choose(choice: "codex" | "later") {
    if (busyRef.current === "saving" || !statusRef.current?.can_configure) return;
    const { pending, id } = begin("saving"); setAllowDiscard(false);
    try {
      const value = await api<ConnectionStatus>("/ai-connection/setup", { ...jsonRequest("POST", { choice }), signal: pending.signal, timeoutMs: 15000 });
      if (id === requestId.current && !pending.signal.aborted) {
        statusRef.current = value; setStatus(value); closeNow();
      }
    } catch (cause) {
      if (id === requestId.current && !isAbortError(cause)) { setError(errorMessage(cause)); setAllowDiscard(true); }
    } finally { finish(id); }
  }
  function dismiss() {
    if (busyRef.current === "saving") return;
    if (statusRef.current?.can_configure && statusRef.current.needs_setup) void choose("later");
    else closeNow();
  }

  const local = status?.deployment_mode === "local" && status.can_configure;
  const canChooseCodex = local && status.codex.installed && status.codex.auth_state === "chatgpt" && !busy;
  const checkedAt = status?.codex.checked_at ? new Date(status.codex.checked_at * 1000).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }) : "";
  return <Context.Provider value={{ available: true, open: openDialog, setSafeLanding }}>
    {children}
    {open && <dialog ref={dialog} className="ai-connection-dialog" aria-labelledby="ai-connection-title" aria-describedby="ai-connection-description"
      onKeyDown={event => event.stopPropagation()}
      onCancel={event => { event.preventDefault(); dismiss(); }}>
      <header className="ai-connection-header">
        <div><p className="ai-connection-eyebrow">작업 공간 설정</p><h2 ref={title} tabIndex={-1} id="ai-connection-title">AI 연결 설정</h2></div>
        <button type="button" className="ai-connection-close" aria-label="닫기" disabled={busy === "saving"} onClick={dismiss}>닫기</button>
      </header>
      <div className="ai-connection-content" aria-busy={!!busy}>
        <p id="ai-connection-description" className="ai-connection-description">{!status ? "현재 작업 공간의 AI 설정을 확인합니다." : local
          ? "이 PC의 Codex CLI를 보고서 작성과 문서 수정에 사용할 수 있습니다. 설치와 ChatGPT 로그인 상태를 먼저 확인하세요."
          : "이 작업 공간은 서버에 설정된 AI를 사용합니다. 아래에서 기능별 연결 설정을 확인하세요."}</p>
        {!status && busy === "loading" && <p role="status">연결 설정을 불러오는 중…</p>}
        {status && <>
          <section className="ai-connection-status" aria-label={local ? "이 PC의 Codex" : "서버의 AI 연결"}>
            <div className="ai-connection-status-heading"><h3>{local ? "이 PC의 Codex" : "서버의 AI 연결"}</h3>
              <span className={`ai-connection-badge${local && status.codex.auth_state === "chatgpt" ? " is-verified" : ""}`}>{local ? authLabels[status.codex.auth_state] : "서버 설정"}</span></div>
            {local ? <>
              <p>{status.codex.message}</p>
              {status.choice === "codex" && <p className="ai-connection-muted">Codex 사용을 선택했습니다.</p>}
              {status.choice === "later" && <p className="ai-connection-muted">첫 실행 설정을 나중으로 미뤘습니다. 여기서 언제든 다시 확인할 수 있습니다.</p>}
              <div className="ai-connection-check"><button type="button" disabled={!!busy} onClick={() => void check()}>{busy === "checking" ? "연결 상태 확인 중…" : "연결 상태 확인"}</button>
                {checkedAt && <span className="ai-connection-muted">{checkedAt} 확인</span>}</div>
              {(status.codex.auth_state === "not_installed" || !status.codex.installed) && <p className="ai-connection-help">Codex CLI를 설치한 뒤 이 앱을 다시 실행하세요. <a href="https://developers.openai.com/codex/cli/" target="_blank" rel="noreferrer">설치 안내</a></p>}
              {status.codex.installed && status.codex.auth_state !== "chatgpt" && <p className="ai-connection-help">로그인이 필요하면 터미널에서 <code>codex login</code>을 실행해 ChatGPT 계정으로 로그인한 뒤 다시 확인하세요. <a href="https://developers.openai.com/codex/auth/" target="_blank" rel="noreferrer">로그인 안내</a></p>}
            </> : <>
              <p>연결 변경은 서버 관리자에게 요청하세요. 대화 답변·문서 검토는 서버에서 Codex 또는 HTTP AI를 선택하며, Codex는 서버의 ChatGPT 로그인과 별도 사용 권한이 필요합니다.</p>
              <button type="button" disabled={!!busy} onClick={() => void load()}>{busy === "loading" ? "상태 확인 중…" : "상태 새로고침"}</button>
            </>}
          </section>
          <section className="ai-connection-features" aria-labelledby="ai-connection-features-title">
            <h3 id="ai-connection-features-title">기능별 연결</h3>
            <ul>{status.features.map(feature => <li key={feature.id}>
              <div><strong>{feature.label}</strong><span>{feature.provider === "codex" ? "Codex" : feature.provider === "llm" ? "서버 AI" : "미설정"}</span></div>
              <div><b className={feature.configured && feature.allowed ? "is-configured" : ""}>{!feature.configured ? "설정 필요" : !feature.allowed ? "사용 제한" : "설정됨"}</b><p>{feature.message}</p></div>
            </li>)}</ul>
            <p className="ai-connection-muted">여기서는 설치·로그인과 기능 설정을 확인합니다. 실제 모델의 응답 여부는 보고서 작성이나 문서 수정 요청 시 확인됩니다.</p>
          </section>
        </>}
        {error && <div className="ai-connection-error" role="alert"><p>{error}</p>{!status && <button type="button" disabled={!!busy} onClick={() => void load()}>설정 다시 불러오기</button>}
          {allowDiscard && <><p>선택이 저장되지 않았을 수 있습니다. 다시 시도하거나 화면을 닫고 나중에 확인하세요.</p><button type="button" disabled={!!busy} onClick={closeNow}>저장하지 않고 닫기</button></>}</div>}
      </div>
      <footer className="ai-connection-actions">
        {local ? <><button type="button" disabled={busy === "saving"} onClick={() => void choose("later")}>나중에 설정</button>
          <button type="button" className="primary" disabled={!canChooseCodex} onClick={() => void choose("codex")}>{busy === "saving" ? "선택 저장 중…" : "이 PC의 Codex 사용"}</button></>
          : <button type="button" onClick={closeNow}>확인</button>}
      </footer>
    </dialog>}
  </Context.Provider>;
}
