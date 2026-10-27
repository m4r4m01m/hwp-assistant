import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, isAbortError, type Document } from "./api";
import type { Snapshot } from "./editor";
import type { AssistantCapture, AssistantDesignContext, AssistantDesignTarget, AssistantRewriteContext } from "./document-assistant-context";
import { DocumentRewritePreview, type DocumentRewriteActions, type ParagraphRewrite } from "./document-rewrite";
import { usePageVisible } from "./ui-state";
import { CapabilityNotice, useFeature } from "./capabilities";
import "./document-assistant.css";

export type AssistantRequest = AssistantCapture & { id: string; message: string };
export type AssistantDesignProposal = {
  kind: "design" | "standard"; id: string; sha256: string; name: string; design_kind: "table" | "title" | null;
  reason: string; target: AssistantDesignTarget | { kind: "document"; section: 0 };
};
export type AssistantTurn = {
  id: string; conversation_id: string; document_id: string; base_version: string; message: string;
  status: string; answer: string | null; rewrite_instruction: string | null; selection: Snapshot | null;
  document_context: { sha256: string; truncated: boolean; revision: number; epoch: number; characters: number };
  design_context?: AssistantDesignContext | null; design_proposals?: AssistantDesignProposal[];
  rewrite_context?: AssistantRewriteContext | null; document_rewrites?: ParagraphRewrite[];
  table_layout?: { parent_half: boolean; caption_gap_mm: number | null } | null;
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
  captureDocument?: (scope?: "document" | "selection") => Promise<AssistantCapture>;
  sendAssistant?: (thread: string, request: AssistantRequest) => Promise<AssistantTurn>;
  onAssistantActive?: (active: boolean) => void;
  onDesignProposal?: (proposal: AssistantDesignProposal, turn: AssistantTurn) => Promise<void> | void;
  documentRewrite?: DocumentRewriteActions;
};

export function DocumentAssistantPanel({ thread, document: doc, captureDocument, sendAssistant, onDirty, onActive, requestRewrite, onShowRewrite, onDesignProposal, documentRewrite, active: panelActive = true }: AssistantActions & {
  thread: string; document: Document; onDirty: (dirty: boolean) => void; onActive: (active: boolean) => void;
  requestRewrite?: (thread: string, instruction: string, contextRevision?: number, expectedSelection?: Snapshot) => Promise<string | void>;
  onShowRewrite: (taskId?: string) => void; active?: boolean;
}) {
  const visible = usePageVisible(), rewriteFeature = useFeature("rewrite");
  const [turns, setTurns] = useState<AssistantTurn[]>([]), [provider, setProvider] = useState<Provider | null>(null);
  const [message, setMessage] = useState(""), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [permissionLost, setPermissionLost] = useState(false);
  const [pending, setPending] = useState<AssistantRequest | null>(null), [preview, setPreview] = useState<AssistantCapture | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [scope, setScope] = useState<"document" | "selection">("document"), [wholePreview, setWholePreview] = useState<AssistantTurn | null>(null);
  const frozen = useRef<AssistantRequest | null>(null), alive = useRef(true);
  const panelContent = useRef<HTMLElement | null>(null);
  const revealTurn = useRef<string | null>(null), followLatest = useRef(false);
  const previousLatest = useRef<{ id: string; status: string; answer: string | null; error: string } | null>(null);
  const requestedHere = useRef(new Set<string>()), preparedTurns = useRef(new Set<string>());
  const [rewriteStates, setRewriteStates] = useState<Record<string, "preparing" | "ready" | "failed">>({});
  const automaticScrollTop = useRef<number | null>(null);
  const active = busy || !permissionLost && (!!pending || turns.some(turn => turn.status === "running" && turn.can_cancel));
  const running = turns.some(turn => turn.status === "running");
  const shouldPoll = useRef(false), schedulePoll = useRef<() => void>(() => {}), readError = useRef("");
  shouldPoll.current = !permissionLost && (running || !!pending);
  const writable = doc.role !== "reader" && !!sendAssistant && !!captureDocument;
  useEffect(() => { onDirty(!!message.trim() || !!pending); }, [message, pending, onDirty]);
  useEffect(() => { onActive(active); }, [active, onActive]);
  useEffect(() => {
    const panel = panelContent.current?.querySelector<HTMLElement>(".assistant-scroll");
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
    const panel = panelContent.current?.querySelector<HTMLElement>(".assistant-scroll");
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
  useEffect(() => {
    if (!panelActive || busy || permissionLost || running || wholePreview || !documentRewrite || !writable) return;
    const turn = turns.find(item => requestedHere.current.has(item.id) && !preparedTurns.current.has(item.id) &&
      item.status === "ready" && (!!item.document_rewrites?.length || !!item.table_layout) && !!item.rewrite_context && !item.stale && item.base_version === doc.current_version);
    if (turn) { preparedTurns.current.add(turn.id); setWholePreview(turn); }
  }, [turns, panelActive, busy, permissionLost, running, wholePreview, documentRewrite, writable, doc.current_version]);
  async function prepareRewrite(turn: AssistantTurn) {
    if (!requestRewrite || !turn.rewrite_instruction || !turn.selection || !rewriteFeature.available || busy) return;
    setBusy(true); setError(""); setRewriteStates(states => ({ ...states, [turn.id]: "preparing" }));
    try {
      const taskId = await requestRewrite(thread, turn.rewrite_instruction, undefined, turn.selection);
      if (!alive.current) return;
      setRewriteStates(states => ({ ...states, [turn.id]: "ready" }));
      onShowRewrite(typeof taskId === "string" ? taskId : undefined);
    } catch (cause) {
      if (alive.current) { setRewriteStates(states => ({ ...states, [turn.id]: "failed" })); setError(errorMessage(cause)); }
    } finally { if (alive.current) setBusy(false); }
  }
  useEffect(() => {
    if (!panelActive || busy || permissionLost || running || !rewriteFeature.available || !requestRewrite || !writable) return;
    const turn = turns.find(item => requestedHere.current.has(item.id) && !preparedTurns.current.has(item.id) &&
      item.status === "ready" && item.rewrite_instruction && item.selection && !item.stale && item.base_version === doc.current_version);
    if (!turn) return;
    // Only a fresh request from this panel can prepare candidates automatically.
    // Opening history or a failed request never silently repeats model generation.
    preparedTurns.current.add(turn.id);
    void prepareRewrite(turn);
  }, [turns, panelActive, busy, permissionLost, running, rewriteFeature.available, doc.current_version]);
  async function send() {
    if (!captureDocument || !sendAssistant || busy) return;
    setBusy(true); setError("");
    try {
      const request = frozen.current || { ...(await captureDocument(scope)), id: crypto.randomUUID(), message: message.trim() };
      frozen.current = request; setPending(request); setPreview(request); setPreviewOpen(false);
      requestedHere.current.add(request.id);
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
    try { setPreview(await captureDocument(scope)); setPreviewOpen(true); }
    catch (cause) { setError(String(cause)); }
    finally { setBusy(false); }
  }
  return <section ref={panelContent} className="document-assistant" aria-label="문서와 AI 대화">
    <div className="assistant-scroll">
    <div className="assistant-context-card">
      <strong title={doc.name}>{doc.name}</strong>
      <button title="미저장 내용을 포함한 현재 문서를 읽습니다. 수정안은 비교 후 직접 적용합니다." disabled={!captureDocument || busy || !!pending || permissionLost} onClick={() => void inspect()}>AI가 읽을 내용 확인</button>
      {preview && <details open={previewOpen} onToggle={event => setPreviewOpen(event.currentTarget.open)}><summary>최근 확인한 내용 · {preview.document_context.text.length.toLocaleString()}자{preview.document_context.truncated ? " · 일부 발췌" : ""}</summary>
        <p className="hint">{pending ? "재확인은 보관한 질문과 이 문서 내용을 사용합니다." : "새 질문을 보낼 때 문서를 다시 읽습니다. 이전 응답은 요청 당시 내용을 기준으로 합니다."}</p>
        {preview.document_context.truncated && <p className="notice">문서가 길어 앞부분만 포함됩니다. 포함되지 않은 부분은 AI가 확인할 수 없습니다.</p>}
        <pre className="assistant-context-text" data-testid="assistant-context-preview">{preview.document_context.text}</pre>
        <p className="hint">{preview.rewrite_context ? `본문 ${preview.rewrite_context.paragraphs.length}개 문단 · 표 ${preview.rewrite_context.table_targets?.length || 0}개 배치 수정 가능. ${preview.rewrite_context.unavailable}` : preview.selection ? `수정 대상 문단: ${preview.selection.evidence.text}` : "현재 위치는 문단 수정 대상이 아닙니다. ‘문서 전체’ 범위로 요청하거나 문서에 대해 질문할 수 있습니다."}</p>
        {preview.design_context && <p className="hint">디자인 대상: {preview.design_context.target?.kind === "table" ? "현재 표" : preview.design_context.target?.kind === "paragraph" ? "현재 문단" : "문서 전체 표준"}. 제안은 미리보기를 확인한 뒤 적용합니다.</p>}
        {preview.design_context?.target_text && <details><summary>선택한 부분의 내용{preview.design_context.target_text_truncated ? " · 일부 발췌" : ""}</summary><pre className="assistant-context-text">{preview.design_context.target_text}</pre></details>}
      </details>}
    </div>
    <div className="assistant-turns" aria-label="AI 대화 기록">
      {!turns.length && <div className="assistant-welcome"><strong>이 문서에 대해 이야기해 보세요.</strong><p>“핵심을 요약해줘”, “현재 문단을 보고서 문체로 바꿔줘”, “이 표에 어울리는 디자인을 추천해줘”처럼 요청하세요.</p></div>}
      {turns.map(turn => <article key={turn.id} data-assistant-turn-id={turn.id}>
        <div className="assistant-user-message"><small>질문</small><p className="text">{turn.message}</p></div>
        <div className="assistant-answer"><small>{turn.execution.provider === "llm" ? "연결한 AI" : "Codex"}</small>
          {turn.status === "running" ? <p role="status">문서 내용을 바탕으로 답변 작성 중…</p> : turn.answer ? <p className="text">{turn.answer}</p> : <p role="alert">{turn.error || (turn.status === "cancelled" ? "응답 수신을 중단했습니다." : "답변을 완료하지 못했습니다. 다시 요청해 주세요.")}</p>}
          {turn.stale && <p className="hint">이 답변 이후 문서 저장본이 바뀌었습니다. 새 요청은 현재 문서를 다시 읽습니다.</p>}
          {turn.document_context.truncated && <p className="hint">긴 문서의 앞부분을 읽고 작성한 답변입니다.</p>}
          {turn.rewrite_context && <p className="hint">전체 문장 수정 대상 {turn.rewrite_context.paragraphs.length}개 문단. {turn.rewrite_context.unavailable || Object.entries(turn.rewrite_context.preserved).filter(([, count]) => count > 0).map(([name, count]) => `${name} ${count}개 유지`).join(" · ")}</p>}
          {(!!turn.document_rewrites?.length || !!turn.table_layout) && turn.rewrite_context && <div className="assistant-rewrite-action">
            <strong>본문 {turn.document_rewrites?.length || 0}개 문단 수정안{turn.table_layout ? ` · 표 ${turn.rewrite_context.table_targets?.length || 0}개 배치` : ""}</strong>
            <button disabled={!writable || !documentRewrite || permissionLost || active || turn.status !== "ready" || turn.stale || turn.base_version !== doc.current_version}
              onClick={() => setWholePreview(turn)}>문서 전체 전후 비교</button>
            <small>별도 문서에 반영한 결과를 확인하고, 원문에 적용할지 선택합니다.</small>
          </div>}
          {!!turn.design_proposals?.length && <div className="assistant-design-proposals" aria-label="디자인 제안">
            {turn.design_proposals.map(proposal => <section className="assistant-rewrite-action" key={`${proposal.kind}:${proposal.id}`}>
              <strong>{proposal.name}</strong>
              <small>{proposal.kind === "standard" ? "문서 전체 표준" : proposal.target.kind === "table" ? "선택한 표" : "선택한 문단"}</small>
              <p className="text">{proposal.reason}</p>
              <button disabled={doc.role === "reader" || !onDesignProposal || !turn.design_context || permissionLost || active || turn.status !== "ready" || turn.stale || turn.base_version !== doc.current_version} onClick={async () => {
                if (!onDesignProposal || doc.role === "reader") return;
                setBusy(true); setError("");
                try { await onDesignProposal(proposal, turn); }
                catch (cause) { setError(errorMessage(cause)); }
                finally { if (alive.current) setBusy(false); }
              }}>디자인 제안 보기</button>
              <small>{doc.role === "reader" ? "읽기 권한으로는 디자인을 적용할 수 없습니다." : "미리보기를 확인한 뒤 적용할 수 있습니다. 제안만으로 문서가 바뀌지는 않습니다."}</small>
            </section>)}
          </div>}
          {turn.rewrite_instruction && turn.selection && requestRewrite && <div className="assistant-rewrite-action">
            <CapabilityNotice feature="rewrite" label="문단 수정 후보" />
            <details><summary>제안한 수정 방향</summary><p className="text">{turn.rewrite_instruction}</p></details>
            {rewriteStates[turn.id] === "ready" ? <button onClick={() => onShowRewrite()}>수정 후보 확인</button> :
              <button disabled={!writable || permissionLost || active || !rewriteFeature.available || turn.stale || turn.base_version !== doc.current_version}
                onClick={() => { preparedTurns.current.add(turn.id); void prepareRewrite(turn); }}>이 문단 수정 후보 만들기</button>}
            <small>{rewriteStates[turn.id] === "preparing" ? "수정 후보와 전후 비교를 준비하고 있습니다…" : rewriteStates[turn.id] === "ready" ? "수정 후보를 준비했습니다. 문서에서 비교한 뒤 적용할 수 있습니다." : "후보를 비교한 뒤 승인하면 문서에 적용·저장합니다."}</small>
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
    {permissionLost && <p className="notice">문서 접근 권한을 확인할 수 없어 새 요청을 보낼 수 없습니다. 작성 중인 입력은 그대로 보관됩니다. 권한을 확인한 뒤 문서를 다시 여세요.</p>}
    {error && <p className="error" role="alert">{error}</p>}
    </div>
    {writable && <div className="assistant-composer">
      {documentRewrite && <label className="assistant-scope">수정 범위 <select aria-label="AI 수정 범위" value={scope} disabled={active || !!pending} onChange={event => setScope(event.target.value as typeof scope)}>
        <option value="document">문서 전체 · 문장과 표 배치</option><option value="selection">현재 문단</option>
      </select></label>}
      <label htmlFor={`assistant-message-${thread}`}>AI에게 요청</label>
      <textarea id={`assistant-message-${thread}`} aria-label="AI에게 요청" rows={3} maxLength={12000} value={message} placeholder="전체 문장을 짧고 간결한 보고서 문체로 다듬어줘."
        onChange={event => setMessage(event.target.value)} onKeyDown={event => {
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !permissionLost && !active && message.trim() && provider?.configured && provider.allowed) { event.preventDefault(); void send(); }
        }} />
      {pending && <div className="notice"><p>접수 결과를 확인하지 못했습니다. 같은 질문과 문서 내용을 보관 중입니다.</p><p className="text">{pending.message}</p></div>}
      <button className="primary" disabled={permissionLost || busy || (!pending && (active || !message.trim() || !provider?.configured || !provider.allowed))} onClick={() => void send()}>{busy ? "요청 확인 중…" : pending ? "같은 질문 접수 재확인" : "보내기"}</button>
      <small>{provider ? `사용 AI: ${provider.provider === "llm" ? "연결한 모델" : "Codex"}${provider.model ? ` · ${provider.model}` : ""}` : "AI 연결 정보 확인 중…"} · Ctrl+Enter로 보내기</small>
      {provider?.reason && <p role="alert">{provider.reason}</p>}
    </div>}
    {wholePreview && documentRewrite && <DocumentRewritePreview key={wholePreview.id} turn={wholePreview} actions={documentRewrite} onClose={() => setWholePreview(null)} />}
  </section>;
}
