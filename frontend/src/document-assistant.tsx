import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, isAbortError, type Document } from "./api";
import type { Snapshot } from "./editor";
import type { AssistantCapture } from "./document-assistant-context";
import { usePageVisible } from "./ui-state";
import { CapabilityNotice, useFeature } from "./capabilities";
import "./document-assistant.css";

export type AssistantRequest = AssistantCapture & { id: string; message: string };
export type AssistantTurn = {
  id: string; conversation_id: string; document_id: string; base_version: string; message: string;
  status: string; answer: string | null; rewrite_instruction: string | null; selection: Snapshot | null;
  document_context: { sha256: string; truncated: boolean; revision: number; epoch: number; characters: number };
  error: string; execution: { provider?: string; model?: string; completed?: boolean }; created_at: number; stale: boolean; can_cancel: boolean;
};
type Provider = { provider: string; model: string | null; configured: boolean; allowed: boolean; reason: string };
function mergeTurns(previous: AssistantTurn[], incoming: AssistantTurn[]) {
  const rows = new Map(previous.map(row => [row.id, row]));
  for (const row of incoming) {
    const current = rows.get(row.id);
    // A GET started before acceptance/cancellation/completion may arrive last.
    if (!current || current.status === "running" || row.status !== "running") rows.set(row.id, row);
  }
  return Array.from(rows.values()).sort((a, b) => a.created_at - b.created_at || a.id.localeCompare(b.id));
}
export type AssistantActions = {
  captureDocument?: () => Promise<AssistantCapture>;
  sendAssistant?: (thread: string, request: AssistantRequest) => Promise<AssistantTurn>;
  onAssistantActive?: (active: boolean) => void;
};

export function DocumentAssistantPanel({ thread, document: doc, captureDocument, sendAssistant, onDirty, onActive, requestRewrite, onShowRewrite, active: panelActive = true }: AssistantActions & {
  thread: string; document: Document; onDirty: (dirty: boolean) => void; onActive: (active: boolean) => void;
  requestRewrite?: (thread: string, instruction: string, contextRevision?: number, expectedSelection?: Snapshot) => Promise<void>;
  onShowRewrite: () => void; active?: boolean;
}) {
  const visible = usePageVisible(), rewriteFeature = useFeature("rewrite");
  const [turns, setTurns] = useState<AssistantTurn[]>([]), [provider, setProvider] = useState<Provider | null>(null);
  const [message, setMessage] = useState(""), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [permissionLost, setPermissionLost] = useState(false);
  const [pending, setPending] = useState<AssistantRequest | null>(null), [preview, setPreview] = useState<AssistantCapture | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const frozen = useRef<AssistantRequest | null>(null), alive = useRef(true);
  const panelContent = useRef<HTMLElement | null>(null);
  const revealTurn = useRef<string | null>(null), followLatest = useRef(false);
  const previousLatest = useRef<{ id: string; status: string; answer: string | null; error: string } | null>(null);
  const automaticScrollTop = useRef<number | null>(null);
  const active = busy || !permissionLost && (!!pending || turns.some(turn => turn.status === "running" && turn.can_cancel));
  const running = turns.some(turn => turn.status === "running");
  const shouldPoll = useRef(false), schedulePoll = useRef<() => void>(() => {}), readError = useRef("");
  shouldPoll.current = !permissionLost && (running || !!pending);
  const writable = doc.role !== "reader" && !!sendAssistant && !!captureDocument;
  useEffect(() => { onDirty(!!message.trim() || !!pending); }, [message, pending, onDirty]);
  useEffect(() => { onActive(active); }, [active, onActive]);
  useEffect(() => {
    const panel = panelContent.current?.closest<HTMLElement>(".conversation-panel");
    if (!panel) return;
    let previousTop = panel.scrollTop;
    const scrolled = () => {
      const top = panel.scrollTop;
      if (automaticScrollTop.current !== null && Math.abs(top - automaticScrollTop.current) < 1) {
        automaticScrollTop.current = null; previousTop = top; return;
      }
      automaticScrollTop.current = null;
      // Reading older messages takes priority over a later answer arriving.
      if (top < previousTop - 1) followLatest.current = false;
      else if (top > previousTop + 1) {
        const latest = panelContent.current?.querySelector<HTMLElement>(".assistant-turns > article:last-child .assistant-answer");
        const composer = panelContent.current?.querySelector<HTMLElement>(".assistant-composer");
        const bottom = Math.min(panel.getBoundingClientRect().bottom, composer?.getBoundingClientRect().top ?? Infinity);
        if (latest && latest.getBoundingClientRect().bottom <= bottom + 24) followLatest.current = true;
      }
      previousTop = top;
    };
    panel.addEventListener("scroll", scrolled, { passive: true });
    return () => panel.removeEventListener("scroll", scrolled);
  }, []);
  useLayoutEffect(() => {
    const latest = turns.at(-1), previous = previousLatest.current;
    previousLatest.current = latest ? { id: latest.id, status: latest.status, answer: latest.answer, error: latest.error } : null;
    if (!latest) return;
    const completed = previous?.id === latest.id && previous.status === "running" && latest.status !== "running";
    if (revealTurn.current !== latest.id && !(completed && followLatest.current)) return;
    const panel = panelContent.current?.closest<HTMLElement>(".conversation-panel");
    if (!panel || !panel.clientHeight) { revealTurn.current = latest.id; return; }
    const article = panelContent.current?.querySelector<HTMLElement>(".assistant-turns > article:last-child");
    const target = article?.querySelector<HTMLElement>(latest.status === "running" ? ".assistant-user-message" : ".assistant-answer");
    if (!target) return;
    revealTurn.current = null; followLatest.current = true;
    const bounds = panel.getBoundingClientRect();
    const composer = panelContent.current?.querySelector<HTMLElement>(".assistant-composer");
    const visibleTop = bounds.top + 12;
    const visibleBottom = Math.min(bounds.bottom, composer?.getBoundingClientRect().top ?? Infinity) - 12;
    const text = target.getBoundingClientRect();
    // Move only this sidebar. Long answers start at the top; short answers fit
    // entirely above the composer. Identical polling results never scroll.
    const delta = text.height > visibleBottom - visibleTop || text.top < visibleTop
      ? text.top - visibleTop : Math.max(0, text.bottom - visibleBottom);
    if (Math.abs(delta) < 1) return;
    const top = Math.max(0, Math.min(panel.scrollHeight - panel.clientHeight, panel.scrollTop + delta));
    automaticScrollTop.current = top;
    panel.scrollTop = top;
  }, [turns]);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; onDirty(false); onActive(false); };
  }, [thread, onDirty, onActive]);
  useEffect(() => {
    if (!panelActive || !visible || permissionLost) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined, loading = false, denied = false, delay = 1500;
    const schedule = () => {
      clearTimeout(timer);
      if (!controller.signal.aborted && !denied && !loading && shouldPoll.current)
        timer = setTimeout(() => void load(), delay);
    };
    const load = async () => {
      if (controller.signal.aborted || denied || loading) return;
      loading = true;
      let retry = false;
      try {
        const rows = await api<AssistantTurn[]>(`/threads/${thread}/assistant-turns`, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setTurns(previous => mergeTurns(previous, rows)); delay = 1500;
          const recovered = readError.current;
          readError.current = "";
          if (recovered) setError(current => current === recovered ? "" : current);
        }
      } catch (cause) {
        if (controller.signal.aborted || isAbortError(cause)) return;
        if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) {
          denied = true; setPermissionLost(true); setPending(null);
        } else {
          retry = true;
          delay = Math.max(Math.min(30000, delay * 2), cause instanceof ApiError ? cause.retryAfterMs || 0 : 0);
        }
        readError.current = errorMessage(cause);
        setError(readError.current);
      } finally {
        loading = false;
        if (!controller.signal.aborted) {
          if (retry) timer = setTimeout(() => void load(), delay);
          else schedule();
        }
      }
    };
    schedulePoll.current = schedule;
    void api<Provider>("/document-assistant-provider", { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setProvider(value); })
      .catch(cause => { if (!controller.signal.aborted && !isAbortError(cause)) setError(errorMessage(cause)); });
    // Refresh on opening/returning to the panel. Completed and empty histories
    // remain idle; running requests use one cancellable request at a time.
    void load();
    return () => {
      controller.abort(); clearTimeout(timer);
      if (schedulePoll.current === schedule) schedulePoll.current = () => {};
    };
  }, [thread, panelActive, visible, permissionLost]);
  useEffect(() => { schedulePoll.current(); }, [running, !!pending]);
  async function send() {
    if (!captureDocument || !sendAssistant || busy) return;
    setBusy(true); setError("");
    try {
      const request = frozen.current || { ...(await captureDocument()), id: crypto.randomUUID(), message: message.trim() };
      frozen.current = request; setPending(request); setPreview(request); setPreviewOpen(false);
      const turn = await sendAssistant(thread, request);
      if (!alive.current) return;
      revealTurn.current = turn.id;
      setTurns(rows => mergeTurns(rows, [turn]));
      setMessage(current => current.trim() === request.message ? "" : current);
      frozen.current = null; setPending(null);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) { frozen.current = null; setPending(null); }
      if (alive.current) setError(String(cause));
    } finally { if (alive.current) setBusy(false); }
  }
  async function inspect() {
    if (!captureDocument) return;
    setBusy(true); setError("");
    try { setPreview(await captureDocument()); setPreviewOpen(true); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <section ref={panelContent} className="document-assistant" aria-label="문서와 AI 대화">
    <div className="assistant-context-card">
      <strong title={doc.name}>{doc.name}</strong>
      <span>요청할 때마다 현재 본문과 표의 텍스트를 읽습니다. 미저장 내용도 포함됩니다.</span>
      <button disabled={!captureDocument || busy || !!pending || permissionLost} onClick={() => void inspect()}>AI가 읽을 내용 확인</button>
      <small>그림·화면 배치는 읽지 않습니다. 수정은 본문 문단에 커서를 놓고 요청하세요.</small>
      {preview && <details open={previewOpen} onToggle={event => setPreviewOpen(event.currentTarget.open)}><summary>최근 확인한 내용 · {preview.document_context.text.length.toLocaleString()}자{preview.document_context.truncated ? " · 일부 발췌" : ""}</summary>
        <p className="hint">{pending ? "재확인은 보관한 질문과 이 문서 내용을 사용합니다." : "새 질문을 보낼 때 문서를 다시 읽습니다. 이전 응답은 요청 당시 내용을 기준으로 합니다."}</p>
        {preview.document_context.truncated && <p className="notice">문서가 길어 앞부분만 포함됩니다. 포함되지 않은 부분은 AI가 확인할 수 없습니다.</p>}
        <pre className="assistant-context-text" data-testid="assistant-context-preview">{preview.document_context.text}</pre>
        <p className="hint">{preview.selection ? `수정 대상 문단: ${preview.selection.evidence.text}` : "수정 가능한 본문 문단이 선택되지 않았습니다. 문서 질문은 할 수 있습니다."}</p>
      </details>}
    </div>
    <div className="assistant-turns" aria-label="AI 대화 기록">
      {!turns.length && <div className="assistant-welcome"><strong>이 문서에 대해 이야기해 보세요.</strong><p>“핵심을 요약해줘”, “현재 문단을 보고서 문체로 바꿔줘”처럼 요청하고, 답변에 이어서 의견을 보탤 수 있습니다.</p></div>}
      {turns.map(turn => <article key={turn.id} data-assistant-turn-id={turn.id}>
        <div className="assistant-user-message"><small>질문</small><p className="text">{turn.message}</p></div>
        <div className="assistant-answer"><small>{turn.execution.provider === "llm" ? "연결한 AI" : "Codex"}</small>
          {turn.status === "running" ? <p role="status">문서 내용을 바탕으로 답변 작성 중…</p> : turn.answer ? <p className="text">{turn.answer}</p> : <p role="alert">{turn.error || (turn.status === "cancelled" ? "응답 수신을 중단했습니다." : "답변을 완료하지 못했습니다. 다시 요청해 주세요.")}</p>}
          {turn.stale && <p className="hint">이 답변 이후 문서 저장본이 바뀌었습니다. 새 요청은 현재 문서를 다시 읽습니다.</p>}
          {turn.document_context.truncated && <p className="hint">긴 문서의 앞부분을 읽고 작성한 답변입니다.</p>}
          {turn.rewrite_instruction && turn.selection && requestRewrite && <div className="assistant-rewrite-action">
            <CapabilityNotice feature="rewrite" label="문단 수정 후보" />
            <details><summary>제안한 수정 방향</summary><p className="text">{turn.rewrite_instruction}</p></details>
            <button disabled={permissionLost || active || !rewriteFeature.available || turn.stale || turn.base_version !== doc.current_version} onClick={async () => {
              if (!rewriteFeature.available) return;
              setBusy(true); setError("");
              try { await requestRewrite(thread, turn.rewrite_instruction!, undefined, turn.selection!); }
              catch (cause) { setError(String(cause)); }
              finally { onShowRewrite(); setBusy(false); }
            }}>이 문단 수정 후보 만들기</button>
            <small>후보를 비교한 뒤 승인하면 문서에 적용·저장합니다.</small>
          </div>}
          {turn.can_cancel && !permissionLost && <button disabled={busy} onClick={async () => {
            setBusy(true); setError("");
            try { const value = await api<AssistantTurn>(`/assistant-turns/${turn.id}/cancel`, { method: "POST" }); setTurns(rows => rows.map(row => row.id === value.id ? value : row)); }
            catch (cause) { setError(String(cause)); }
            finally { setBusy(false); }
          }}>응답 수신 중단</button>}
        </div>
      </article>)}
    </div>
    {writable && <div className="assistant-composer">
      <label htmlFor={`assistant-message-${thread}`}>AI에게 요청</label>
      <textarea id={`assistant-message-${thread}`} aria-label="AI에게 요청" rows={3} maxLength={12000} value={message} placeholder="이 문서를 요약해줘. / 이 문단을 더 간결하게 바꿔줘."
        onChange={event => setMessage(event.target.value)} onKeyDown={event => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !permissionLost && !active && message.trim() && provider?.configured && provider.allowed) { event.preventDefault(); void send(); }
        }} />
      {pending && <div className="notice"><p>접수 결과를 확인하지 못했습니다. 같은 질문과 문서 내용을 보관 중입니다.</p><p className="text">{pending.message}</p></div>}
      <button className="primary" disabled={permissionLost || busy || (!pending && (active || !message.trim() || !provider?.configured || !provider.allowed))} onClick={() => void send()}>{busy ? "요청 확인 중…" : pending ? "같은 질문 접수 재확인" : "보내기"}</button>
      <small>{provider ? `사용 AI: ${provider.provider === "llm" ? "연결한 모델" : "Codex"}${provider.model ? ` · ${provider.model}` : ""}` : "AI 연결 정보 확인 중…"} · Ctrl+Enter로 보내기</small>
      {provider?.reason && <p role="alert">{provider.reason}</p>}
    </div>}
    {permissionLost && <p className="notice">문서 접근 권한을 확인할 수 없어 새 요청을 보낼 수 없습니다. 작성 중인 입력은 그대로 보관됩니다. 권한을 확인한 뒤 문서를 다시 여세요.</p>}
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
