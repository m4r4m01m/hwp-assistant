import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, isAbortError, type Document } from "./api";
import type { Snapshot } from "./editor";
import { ContextSnapshot, type FrozenWritingContext, type WritingContext } from "./context";
import { CandidateText } from "./candidate-text";
import { CapabilityNotice, useFeature } from "./capabilities";
import { usePageVisible } from "./ui-state";

export type RewriteTask = {
  id: string; document_id: string; base_version: string; requested_by: string;
  status: string; stale: boolean; context_stale?: boolean; error: string; instruction: string; snapshot: Snapshot; can_cancel?: boolean;
  execution: { provider?: string; model?: string; executor?: string; outcome?: string; context_used?: boolean;
    original_sha256?: string; input_sha256?: string; generation_calls?: number; context_revision?: number;
    codex_thread_id?: string; completed?: boolean; mcp_tools?: {tool: string; status: string}[]; diagnostic?: string };
  candidates: {id: string; ordinal: number; replacement: string; reason: string}[];
  application: {id: string; candidate_id: string; status: string; saved_version: string | null; approved_by: string} | null;
};

type RewriteProvider = { provider: string; model: string | null; configured: boolean; allowed: boolean; reason: string };
export type PendingRewrite = { id: string; thread: string; instruction: string; contextRevision?: number };
const providerLabel = (provider?: string) => provider === "llm" ? "로컬·사내 모델" : provider === "codex" ? "Codex" : "미확인 경로";

export function RewritePanel({ thread, document: doc, context, contextDirty, requestRewrite, approveRewrite, cancelRewrite, getPendingRewrite, active = true }: {
  thread: string; document: Document; context?: WritingContext; contextDirty?: boolean;
  requestRewrite?: (thread: string, instruction: string, contextRevision?: number) => Promise<void>;
  approveRewrite?: (task: string, candidate: string, application: string) => Promise<void>;
  cancelRewrite?: (task: string) => Promise<void>;
  getPendingRewrite?: () => PendingRewrite | null;
  active?: boolean;
}) {
  const feature = useFeature("rewrite"), visible = usePageVisible();
  const mounted = useRef(true), actionLock = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const [tasks, setTasks] = useState<RewriteTask[]>([]), [error, setError] = useState(""),
    [instruction, setInstruction] = useState("사실과 의미를 유지하면서 더 자연스럽고 명확한 업무 문체로 개선해 주세요."),
    [provider, setProvider] = useState<RewriteProvider | null>(null),
    [useContext, setUseContext] = useState(false),
    [highlight, setHighlight] = useState(true), [copied, setCopied] = useState(""),
    [inputs, setInputs] = useState<Record<string, FrozenWritingContext>>({}),
    [busy, setBusy] = useState(false);
  const contextReady = !!context?.confirmation && !context.stale && context.base_version === doc.current_version;
  const pendingRequest = getPendingRewrite?.();
  const refresh = async (signal?: AbortSignal) => {
    const [history, settings] = await Promise.all([
      api<RewriteTask[]>(`/threads/${thread}/rewrite-tasks`, { signal }), api<RewriteProvider>("/rewrite-provider", { signal }),
    ]);
    if (mounted.current && !signal?.aborted) { setTasks(history); setProvider(settings); }
  };
  useEffect(() => {
    if (!active || !visible) return;
    const controller = new AbortController();
    void refresh(controller.signal).catch(cause => {
      if (!controller.signal.aborted && !isAbortError(cause)) setError(errorMessage(cause));
    });
    return () => controller.abort();
  }, [thread, active, visible]);
  const running = tasks.some(task => task.status === "running");
  useEffect(() => {
    if (!active || !visible || !running) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>, delay = 2000;
    const poll = async () => {
      try {
        const values = await api<RewriteTask[]>(`/threads/${thread}/rewrite-tasks`, { signal: controller.signal });
        if (!controller.signal.aborted) { setTasks(values); delay = 2000; }
      } catch (cause) {
        if (!controller.signal.aborted && !isAbortError(cause)) {
          setError(errorMessage(cause));
          delay = Math.max(Math.min(30000, delay * 2), cause instanceof ApiError ? cause.retryAfterMs || 0 : 0);
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => void poll(), delay);
      }
    };
    timer = setTimeout(() => void poll(), delay);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [thread, active, visible, running]);
  async function run(fn: () => Promise<void>, refreshAfter = true) {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy(true); setError("");
    try { await fn(); } catch(e) { if (mounted.current) setError(errorMessage(e)); }
    finally {
      if (mounted.current && refreshAfter) await refresh().catch(e => { if (mounted.current) setError(errorMessage(e)); });
      actionLock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return <section className="rewrite"><h2>선택 문단 수정 후보</h2>
    <p>요청 당시의 미저장 문단을 기준으로 후보 두 개를 만듭니다. 후보 생성만으로 문서를 변경하지 않습니다.</p>
    <CapabilityNotice feature="rewrite" label="선택 문단 AI 교정" />
    <p data-testid="rewrite-provider">현재 생성 경로: {provider ? providerLabel(provider.provider) : "확인 중"}{provider?.model ? ` · ${provider.model}` : ""}</p>
    <p>{useContext ? `선택 문단·개선 요청과 저장된 작성 정보 리비전 ${context?.revision}을 전달합니다.` : "선택 문단과 개선 요청만 전달합니다."} 전체 대화는 자동으로 포함하지 않습니다.</p>
    {provider?.reason && <p role="alert">{provider.reason}</p>}
    {requestRewrite && pendingRequest?.thread === thread && <div className="notice" data-testid="pending-rewrite">
      <p>접수 결과가 확인되지 않은 생성 요청을 보관 중입니다. 현재 입력을 바꿔도 재확인은 보관한 원문·지시·작성 정보로 진행합니다.</p>
      <p className="text">{pendingRequest.instruction}</p>
      <button disabled={busy} onClick={() => void run(() => requestRewrite(thread, pendingRequest.instruction, pendingRequest.contextRevision))}>보관한 생성 요청 재확인</button>
    </div>}
    {requestRewrite && <>
      <label className="check-label"><input type="checkbox" checked={useContext} onChange={e => setUseContext(e.target.checked)} />저장한 작성 정보 사용</label>
      {useContext && !contextReady && <p className="notice">아래 작성 정보를 확인하고, 확인 체크와 함께 저장하세요. 문서 기준이 바뀌었다면 다시 확인해야 합니다.</p>}
      {useContext && contextDirty && <p className="notice">작성 정보의 변경을 저장한 뒤 후보를 요청하세요.</p>}
      <label>문체 개선 요청<textarea aria-label="문체 개선 요청" value={instruction} onChange={e => setInstruction(e.target.value)} maxLength={2000} /></label>
      <button disabled={busy || !feature.available || !provider?.configured || !provider.allowed || tasks.some(t => t.status === "running") || !instruction.trim() || useContext && (!contextReady || contextDirty)} onClick={() => void run(() => requestRewrite(thread, instruction, useContext ? context?.revision : undefined))}>
        {provider?.provider === "codex" ? "실제 Codex로 후보 2개 요청" : "로컬·사내 모델로 후보 2개 요청"}
      </button>
    </>}
    <button disabled={busy} onClick={() => void run(() => refresh(), false)}>후보·적용 이력 새로고침</button>
    <label className="check-label"><input type="checkbox" checked={highlight} onChange={e => setHighlight(e.target.checked)} />후보 변경 부분 강조</label>
    {error && <p role="alert" className="error">{error}</p>}
    {tasks.map(task => <article key={task.id} data-task-id={task.id}>
      <h3>작업 {task.id}</h3><small>요청자 {task.requested_by} · {task.status} · 편집 리비전 {task.snapshot.revision}</small>
      <small>문서 {task.document_id} · 기준 {task.base_version}</small>
      <small data-testid="task-provider">생성 경로: {providerLabel(task.execution.provider || "codex")}{task.execution.model ? ` · ${task.execution.model}` : ""}</small>
      {task.stale && !task.application?.saved_version && <p>{task.context_stale ? "작성 정보가 변경되었습니다. 현재 정보로 새 후보를 요청하세요." : "기준 버전 충돌: 문서는 덮어쓰지 않습니다. 원문과 후보를 보존했습니다."}</p>}
      <p className="hint">작성 정보: {task.execution.context_used ? `리비전 ${task.execution.context_revision} 요청에 포함` : "사용 안 함"}</p>
      <details><summary>요청한 개선 지시</summary><p className="text">{task.instruction}</p>
        {requestRewrite && <button disabled={busy} onClick={() => setInstruction(task.instruction)}>개선 요청 불러오기</button>}
      </details>
      {task.execution.context_used && <details onToggle={event => {
        if (event.currentTarget.open && !inputs[task.id]) void api<{generation_input: {writing_context: FrozenWritingContext}}>(`/rewrite-tasks/${task.id}/input`)
          .then(value => setInputs(old => ({ ...old, [task.id]: value.generation_input.writing_context }))).catch(e => setError(e.message));
      }}><summary>이 후보에 전달한 작성 정보</summary>{inputs[task.id] ? <ContextSnapshot value={inputs[task.id]} /> : <p>불러오는 중…</p>}</details>}
      {task.error && <p role="alert" className="error">{task.error}</p>}
      {task.can_cancel && <>
        <button disabled={busy} onClick={() => void run(async () => {
          if (cancelRewrite) await cancelRewrite(task.id);
          else await api(`/rewrite-tasks/${task.id}/cancel`, { method: "POST" });
        })}>이 작업 결과 수신 중단</button>
        <p>시작된 모델 요청은 끝날 수 있지만, 중단한 작업의 새 결과는 게시하거나 적용하지 않습니다.</p>
      </>}
      <h3>AS-IS · 요청 당시 미저장 원문</h3><p className="text" data-testid="as-is">{task.snapshot.evidence.text}</p>
      {task.candidates.map(candidate => <div key={candidate.id} data-candidate-id={candidate.id}>
        <h3>TO-BE · 후보 {candidate.ordinal}</h3><CandidateText original={task.snapshot.evidence.text} replacement={candidate.replacement} ordinal={candidate.ordinal} highlight={highlight} />
        <p>{candidate.reason}</p><small>후보 ID {candidate.id}</small>
        <button onClick={() => void navigator.clipboard.writeText(candidate.replacement).then(() => setCopied(candidate.id)).catch(() => setError("클립보드에 복사하지 못했습니다. 후보 문장을 직접 선택해 복사하세요."))}>{copied === candidate.id ? "복사 완료" : `후보 ${candidate.ordinal} 복사`}</button>
        {approveRewrite && <button disabled={busy || task.status !== "ready" || (task.stale && !task.application?.saved_version) || !!task.application && task.application.candidate_id !== candidate.id}
          onClick={() => void run(() => approveRewrite(task.id, candidate.id, task.application?.id || crypto.randomUUID()))}>
          {task.application?.saved_version && task.application.candidate_id === candidate.id ? "저장 완료 확인" : `후보 ${candidate.ordinal} 승인·적용·저장`}
        </button>}
      </div>)}
      {task.application && <p data-testid="application-history">적용 이력 · {task.application.id} · 승인자 {task.application.approved_by} · {task.application.status} · 저장 버전 {task.application.saved_version || "미확정"}</p>}
      <details><summary>실행 근거</summary>
        <p>실행 결과: {task.status === "expired" ? "expired" : task.execution.outcome || task.status} · 완료 확인: {task.execution.completed ? "예" : "아니오"}</p>
        {task.execution.provider !== "llm" && <p>Codex 세션: {task.execution.codex_thread_id || "미확인"}</p>}
        {task.execution.generation_calls !== undefined && <p>모델 생성 호출: {task.execution.generation_calls}회</p>}
        {task.execution.original_sha256 && <p>요청 원문 SHA-256: {task.execution.original_sha256}</p>}
        {task.execution.input_sha256 && <p>입력 SHA-256: {task.execution.input_sha256}</p>}
        {task.execution.mcp_tools?.map((call, i) => <p key={i}>{call.tool} · {call.status}</p>)}
        {task.execution.diagnostic && <pre>{task.execution.diagnostic}</pre>}
      </details>
    </article>)}
  </section>;
}
