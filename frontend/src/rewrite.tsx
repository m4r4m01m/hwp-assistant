import { useEffect, useState } from "react";
import { api, type Document } from "./api";
import type { Snapshot } from "./editor";

export type RewriteTask = {
  id: string; document_id: string; base_version: string; requested_by: string;
  status: string; stale: boolean; error: string; instruction: string; snapshot: Snapshot;
  execution: { codex_thread_id?: string; completed?: boolean; mcp_tools?: {tool: string; status: string}[]; diagnostic?: string };
  candidates: {id: string; ordinal: number; replacement: string; reason: string}[];
  application: {id: string; candidate_id: string; status: string; saved_version: string | null; approved_by: string} | null;
};

export function RewritePanel({ thread, document: doc, requestRewrite, approveRewrite }: {
  thread: string; document: Document;
  requestRewrite?: (thread: string, instruction: string) => Promise<void>;
  approveRewrite?: (task: string, candidate: string, application: string) => Promise<void>;
}) {
  const [tasks, setTasks] = useState<RewriteTask[]>([]), [error, setError] = useState(""),
    [instruction, setInstruction] = useState("사실과 의미를 유지하면서 더 자연스럽고 명확한 업무 문체로 개선해 주세요."),
    [busy, setBusy] = useState(false);
  const refresh = async () => setTasks(await api<RewriteTask[]>(`/threads/${thread}/rewrite-tasks`));
  useEffect(() => {
    let live = true;
    const load = () => api<RewriteTask[]>(`/threads/${thread}/rewrite-tasks`).then(r => { if (live) setTasks(r); }).catch(e => { if (live) setError(e.message); });
    void load();
    const timer = setInterval(load, 2000);
    return () => { live = false; clearInterval(timer); };
  }, [thread]);
  async function run(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); } catch(e) { setError(String(e)); }
    finally { await refresh().catch(e => setError(e.message)); setBusy(false); }
  }
  return <section className="rewrite"><h2>선택 문단 · 실제 Codex</h2>
    <p>요청 당시의 미저장 문단을 기준으로 후보 두 개를 만듭니다. 후보 생성만으로 문서를 변경하지 않습니다.</p>
    {requestRewrite && <>
      <label>문체 개선 요청<textarea aria-label="문체 개선 요청" value={instruction} onChange={e => setInstruction(e.target.value)} maxLength={2000} /></label>
      <button disabled={busy || tasks.some(t => t.status === "running") || !instruction.trim()} onClick={() => void run(() => requestRewrite(thread, instruction))}>실제 Codex로 후보 2개 요청</button>
    </>}
    <button disabled={busy} onClick={() => void run(refresh)}>후보·적용 이력 새로고침</button>
    {error && <p role="alert" className="error">{error}</p>}
    {tasks.map(task => <article key={task.id} data-task-id={task.id}>
      <h3>작업 {task.id}</h3><small>요청자 {task.requested_by} · {task.status} · 편집 리비전 {task.snapshot.revision}</small>
      <small>문서 {task.document_id} · 기준 {task.base_version}</small>
      {task.stale && !task.application?.saved_version && <p>기준 버전 충돌: 문서는 덮어쓰지 않습니다. 원문과 후보를 보존했습니다.</p>}
      {task.error && <p role="alert" className="error">{task.error}</p>}
      <h3>AS-IS · 요청 당시 미저장 원문</h3><p className="text" data-testid="as-is">{task.snapshot.evidence.text}</p>
      {task.candidates.map(candidate => <div key={candidate.id} data-candidate-id={candidate.id}>
        <h3>TO-BE · 후보 {candidate.ordinal}</h3><p className="text" data-testid={`candidate-${candidate.ordinal}`}>{candidate.replacement}</p>
        <p>{candidate.reason}</p><small>후보 ID {candidate.id}</small>
        {approveRewrite && <button disabled={busy || task.status !== "ready" || (task.stale && !task.application?.saved_version) || !!task.application && task.application.candidate_id !== candidate.id}
          onClick={() => void run(() => approveRewrite(task.id, candidate.id, task.application?.id || crypto.randomUUID()))}>
          {task.application?.saved_version && task.application.candidate_id === candidate.id ? "저장 완료 확인" : `후보 ${candidate.ordinal} 승인·적용·저장`}
        </button>}
      </div>)}
      {task.application && <p data-testid="application-history">적용 이력 · {task.application.id} · 승인자 {task.application.approved_by} · {task.application.status} · 저장 버전 {task.application.saved_version || "미확정"}</p>}
      <details><summary>실행 근거</summary><p>Codex 세션: {task.execution.codex_thread_id || "미확인"}</p>
        {task.execution.mcp_tools?.map((call, i) => <p key={i}>{call.tool} · {call.status}</p>)}
        {task.execution.diagnostic && <pre>{task.execution.diagnostic}</pre>}
      </details>
    </article>)}
  </section>;
}
