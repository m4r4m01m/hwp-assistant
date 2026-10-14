import React, { useEffect, useRef, useState } from "react";
import { api, ApiError, jsonRequest, fileUrl, downloadBytes, type Document } from "./api";
import { EditorAdapter } from "./editor";
import { ReportPreview } from "./report-preview";
import { ReportWriting, ReportTiming, type LiveReport } from "./report-progress";
import { ReportContent, ReportText, type Emphasis } from "./report-content";

type Draft = { title: string; summary: string; emphasis?: Emphasis[]; sections: { heading: string; paragraphs: string[];
  tables?: { caption: string; columns: string[]; rows: string[][] }[]; diagrams?: { caption: string; steps: string[] }[] }[];
  sources: { id: string; title: string; url: string; published: string }[]; limitations: string[] };
type Template = { id: string; revision: number; standard_id: string; reference_sha256: string;
  standard_profile_sha256: string; profile_sha256: string;
  values: { name: string; description: string; writing_criteria: string[] } };
type ReportInput = { id: string; prompt: string; research: boolean; template?: Template };
type Job = { id: string; input: { prompt: string; research: boolean; template?: Template }; status: string; draft: Draft | null;
  draft_sha256: string | null; error: string; created_at?: number; execution?: { progress?: LiveReport; timings?: Record<string, number | null> };
  approval: { id: string; document_id: string; saved_version?: string } | null };
type Approved = { id: string; document_id: string; base_version: string; saved_version?: string; document: Document;
  scaffold_base64: string; plan: { paragraph: number; before: string; text: string; command_id: string }[] };
const examples = [
  "최근 금융권 해킹 문제에 대한 경영진 보고서를 만들어줘. 국내 공개 사례와 주요 위험, 경영진 결정 사항, 30·60·90일 대응 계획을 정리해줘. 사실과 제안을 구분하고 공식 출처와 날짜를 표시해줘.",
  "금융회사 외부 업체·클라우드 보안 위험 보고서를 만들어줘. 공급망 침해와 서비스 중단 관점에서 점검 항목, 책임자, 우선순위를 제안하고 공식 출처를 제시해줘.",
  "금융권 고객정보 유출 사고의 고객 보호 대응 보고서를 만들어줘. 사고 인지부터 초기 대응, 안내, 피해 예방, 사후 개선까지 설명하고 확인되지 않은 사항은 구분해줘.",
];
function encode(bytes: Uint8Array) {
  const parts = []; for (let i = 0; i < bytes.length; i += 8192) parts.push(String.fromCharCode(...bytes.subarray(i, i + 8192)));
  return btoa(parts.join(""));
}
function decode(value: string) { return Uint8Array.from(atob(value), c => c.charCodeAt(0)); }

export function Reports({ onClose, onOpen }: { onClose: () => void; onOpen: (doc: Document) => void }) {
  const [previewLocked, setPreviewLocked] = useState(false);
  const [templates, setTemplates] = useState<Template[]>([]), [templateId, setTemplateId] = useState("standard-business"),
    [savedWithUnsaved, setSavedWithUnsaved] = useState(false);
  const [standard, setStandard] = useState<{ name: string; id: string; reference_url: string } | null>(null);
  const [prompt, setPrompt] = useState(""), [research, setResearch] = useState(true), [job, setJob] = useState<Job | null>(null),
    [jobs, setJobs] = useState<Job[]>([]), [busy, setBusy] = useState(false), [saving, setSaving] = useState(false),
    [error, setError] = useState(""), [progress, setProgress] = useState(""), [saved, setSaved] = useState<Document | null>(null),
    [pending, setPending] = useState<ReportInput | null>(null);
  const active = useRef<string | null>(null), host = useRef<HTMLDivElement>(null), editor = useRef<EditorAdapter | null>(null),
    templateFetch = useRef(0),
    approvalBody = useRef<unknown>(null), approval = useRef<Approved | null>(null), output = useRef<unknown>(null),
    lease = useRef<{ documentId: string; token: string } | null>(null);
  const refresh = () => api<Job[]>("/report-tasks").then(setJobs).catch(e => setError(String(e)));
  useEffect(() => { void refresh(); return () => {
    editor.current?.destroy();
    if (lease.current) void api(`/documents/${lease.current.documentId}/session`, { method: "DELETE", headers: { "X-Edit-Session": lease.current.token } }).catch(() => {});
  }; }, []);
  useEffect(() => {
    let stopped = false;
    const id = sessionStorage.getItem("hwp-report-task");
    if (id) void api<Job>(`/report-tasks/${id}`).then(value => {
      if (stopped || active.current !== null) return;
      if (value.approval?.saved_version) {
        sessionStorage.removeItem("hwp-report-task"); // Keep new report creation reachable after saving.
      } else selectJob(value);
    }).catch(() => sessionStorage.removeItem("hwp-report-task"));
    return () => { stopped = true; };
  }, []);
  useEffect(() => { void api<{ name: string; id: string; reference_url: string }>("/document-standard").then(setStandard).catch(e => setError(String(e))); }, []);
  useEffect(() => {
    let stopped = false;
    const generation = ++templateFetch.current;
    void api<{ items: Template[]; default_id: string }>("/report-templates").then(value => {
      if (!stopped && generation === templateFetch.current) setTemplates(value.items);
    }).catch(e => { if (!stopped) setError(String(e)); });
    return () => { stopped = true; };
  }, []);
  async function refreshTemplates() {
    const generation = ++templateFetch.current;
    try {
      const value = await api<{ items: Template[] }>("/report-templates");
      if (generation === templateFetch.current) { setTemplates(value.items); setError(""); }
    } catch (e) { if (generation === templateFetch.current) setError(String(e)); }
  }
  useEffect(() => {
    if (job?.status !== "running") return;
    let stopped = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { const result = await api<Job>(`/report-tasks/${job.id}`, { signal: controller.signal }); if (!stopped && active.current === result.id) {
        setJob(current => current?.id === result.id && current.status === "running" ? result : current);
        setJobs(values => values.map(value => value.id === result.id ? result : value));
      } }
      catch (e) { if (!stopped) setError(String(e)); }
      finally { if (!stopped) timer = setTimeout(() => void poll(), 400); }
    };
    void poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [job?.id, job?.status]);
  useEffect(() => {
    const timer = setInterval(() => { const current = lease.current; if (current) void api(`/documents/${current.documentId}/session`,
      { method: "PUT", headers: { "X-Edit-Session": current.token } }).catch(e => setError(String(e))); }, 30000);
    return () => clearInterval(timer);
  }, []);
  function selectJob(value: Job) {
    sessionStorage.setItem("hwp-report-task", value.id);
    setJobs(values => values.map(item => item.id === value.id ? value : item));
    active.current = value.id; setJob(value); setPrompt(value.input.prompt); setResearch(value.input.research);
    setTemplateId(value.input.template?.id || "standard-business"); setSavedWithUnsaved(false);
    setSaved(null); setError(""); setProgress(""); approval.current = null; approvalBody.current = null; output.current = null;
  }
  async function requestReport() {
    const template = templates.find(item => item.id === templateId);
    if (!pending && !template) { setError("보고서 템플릿을 확인한 뒤 요청하세요."); return; }
    const frozen = pending || { id: crypto.randomUUID(), prompt, research, template };
    setPending(frozen); active.current = frozen.id; setBusy(true); setError("");
    sessionStorage.setItem("hwp-report-task", frozen.id);
    try { const result = await api<Job>("/report-tasks", jsonRequest("POST", frozen));
      if (active.current === frozen.id) { selectJob(result); setPending(null); void refresh(); }
    } catch (e) { if (active.current === frozen.id) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) setPending(null); setError(String(e));
    } }
    finally { if (active.current === frozen.id) setBusy(false); }
  }
  async function makeDocument() {
    if (!job?.draft || !host.current) return;
    setSaving(true); setError("");
    try {
      if (output.current) {
        const result = await api<Approved>(`/report-tasks/${job.id}/approved`);
        if (result.saved_version) {
          setSaved({ ...result.document, current_version: result.saved_version }); setProgress("HWPX 서버 저장 완료");
          const current = lease.current; lease.current = null;
          if (current) await api(`/documents/${current.documentId}/session`, { method: "DELETE", headers: { "X-Edit-Session": current.token } });
          return;
        }
      }
      if (!approval.current && job.approval) approval.current = await api<Approved>(`/report-tasks/${job.id}/approved`);
      if (approval.current?.saved_version) { setSaved(approval.current.document); setProgress("HWPX 서버 저장 완료"); return; }
      if (!output.current) {
        setProgress("새 보고서 편집기를 준비합니다.");
        editor.current?.destroy(); editor.current = await EditorAdapter.mount(host.current);
        const adapter = editor.current;
        if (!approval.current) {
          if (!approvalBody.current) {
            await adapter.bridge.newDocument();
            const seed = adapter.exportHwpx();
            if (seed.contentLoss.count) throw new Error("빈 문서 출력 검사에 실패했습니다.");
            approvalBody.current = { id: crypto.randomUUID(), draft_sha256: job.draft_sha256, seed_base64: encode(seed.bytes) };
          }
          approval.current = await api<Approved>(`/report-tasks/${job.id}/approve`, jsonRequest("POST", approvalBody.current));
        }
        const approved = approval.current;
        if (!lease.current) {
          const session = await api<{ token: string }>(`/documents/${approved.document_id}/session`, { method: "POST" });
          lease.current = { documentId: approved.document_id, token: session.token };
        }
        await adapter.load(decode(approved.scaffold_base64).buffer, approved.document.name);
        const receipts = [];
        for (const [index, item] of approved.plan.entries()) {
          setProgress(`승인한 보고서 문단 적용 ${index + 1}/${approved.plan.length}`);
          const target = { kind: "body_paragraph" as const, section: 0, paragraph: item.paragraph, charOffset: 0 as const, length: [...item.before].length };
          await adapter.sdk.focusTarget(target);
          const snapshot = adapter.selection();
          if (snapshot.evidence.text !== item.before || snapshot.selection.target?.paragraph !== item.paragraph)
            throw new Error("보고서 틀의 적용 위치가 달라졌습니다. 저장하지 않았습니다.");
          receipts.push(await adapter.apply(snapshot, item.text, item.command_id));
        }
        const exported = adapter.exportHwpx(), state = await adapter.sdk.getDocumentState();
        if (exported.contentLoss.count) throw new Error("편집기 출력에서 내용 손실이 감지되어 저장하지 않았습니다.");
        output.current = { approval_id: approved.id, token: lease.current.token, output_base64: encode(exported.bytes), receipts, document_sha256: state.documentSha256 };
      }
      setProgress("보고서 파일과 서버 저장 결과를 확인합니다.");
      const doc = await api<Document>(`/report-tasks/${job.id}/save`, jsonRequest("POST", output.current));
      setSaved(doc); setProgress("HWPX 서버 저장 완료");
      const current = lease.current; lease.current = null;
      if (current) await api(`/documents/${current.documentId}/session`, { method: "DELETE", headers: { "X-Edit-Session": current.token } });
      void refresh();
    } catch (e) { setError(String(e)); setProgress(output.current ? "출력은 유지 중입니다. 같은 저장 결과를 재확인할 수 있습니다." : "보고서 저장을 완료하지 못했습니다. 초안은 보존되어 있습니다."); }
    finally { setSaving(false); }
  }
  const locked = busy || saving || !!lease.current || !!pending || previewLocked;
  return <main className="report-workspace">
    <header><button disabled={busy || saving || !!lease.current || previewLocked} onClick={onClose}>← 문서 목록</button><div><small>Codex 보고서 작성</small><h1>주제에서 보고서까지</h1>
      <p>내용 작성 → 문서 입력 → 서식 적용 → 바로 편집. 초안 확정 시 새 HWPX 문서로 저장합니다.</p></div></header>
    <div className="report-layout"><section className="report-input">
      <h2>어떤 보고서가 필요한가요?</h2>
      <p className="notice">기본 서식 자동 적용: {standard?.name || "문서작성 표준 확인 중"}<br />
        제목의 파란 가로선과 □·ㅇ·- 항목의 들여쓰기를 적용합니다. □ 항목은 2~3줄 내외로 정리하고 비교·계획은 표, 절차는 흐름도로 표현합니다. 괄호 요약은 굵게, 핵심 문구는 굵기·파란색으로 강조합니다.
        {standard && <> <a href={standard.reference_url} download>기준서 원본 보기</a></>}</p>
      <label htmlFor="report-template">보고서 템플릿</label>
      <select id="report-template" value={templateId} disabled={locked || !templates.length} onChange={e => setTemplateId(e.target.value)}>
        {templates.map(item => <option key={item.id} value={item.id}>{item.values.name}</option>)}
      </select>
      <p className="hint">{templates.find(item => item.id === templateId)?.values.description || "보고서 템플릿 확인 중"} · 모든 템플릿에 문서작성 표준을 적용합니다.</p>
      <button disabled={locked} onClick={() => void refreshTemplates()}>템플릿 목록 다시 확인</button>
      <label htmlFor="report-prompt">보고서 작성 요청</label><textarea id="report-prompt" rows={9} maxLength={6000} value={prompt} disabled={locked}
        onChange={e => setPrompt(e.target.value)} placeholder="금융권 해킹 문제에 대한 경영진 보고서를 만들어줘. 주요 사례와 대응 계획, 출처를 포함해줘." />
      <div className="report-examples">{["금융권 해킹 대응", "외부 업체·클라우드 보안", "고객정보 유출 대응"].map((name, i) => <button key={name} disabled={locked} onClick={() => setPrompt(examples[i])}>{name}</button>)}</div>
      <label className="check-label"><input type="checkbox" checked={research} disabled={locked} onChange={e => setResearch(e.target.checked)} />공개 웹 자료를 검색하고 출처 포함</label>
      <p className="hint">입력한 요청은 기존 Codex 로그인으로 처리합니다. 공개자료 검색에는 검색어가 사용됩니다. 민감한 고객정보는 입력하지 마세요.</p>
      <button className="primary" disabled={busy || saving || !!lease.current || previewLocked || (!pending && (prompt.trim().length < 5 || !templates.some(item => item.id === templateId))) || job?.status === "running"} onClick={() => void requestReport()}>
        {busy ? "요청 확인 중…" : pending ? "같은 요청 접수 재확인" : "Codex로 보고서 초안 만들기"}</button>
      {job?.status === "running" && <><p role="status">Codex가 보고서 초안을 작성하고 있습니다.</p><button disabled={busy} onClick={async () => {
        const id = job.id; setBusy(true); try { const result = await api<Job>(`/report-tasks/${id}/cancel`, { method: "POST" }); if (active.current === id) setJob(result); }
        catch (e) { setError(String(e)); } finally { setBusy(false); }
      }}>결과 수신 중단</button><small>수신 중단 후 이 요청의 로컬 실행을 정리합니다.</small></>}
      {job?.status === "failed" && <p role="alert">{job.error}</p>}
      {job?.status === "cancelled" && <p>결과 수신을 중단했습니다. 새 지시로 다시 시작할 수 있습니다.</p>}
      <h3>최근 보고서 요청</h3><button disabled={locked} onClick={() => void refresh()}>목록 새로고침</button>
      <ul className="report-history">{jobs.map(value => <li key={value.id}><button disabled={locked || job?.status === "running"} onClick={async () => {
        setBusy(true); setError(""); try { const current = await api<Job>(`/report-tasks/${value.id}`);
          if (current.approval?.saved_version) { const approved = await api<Approved>(`/report-tasks/${value.id}/approved`); onOpen(approved.document); }
          else selectJob(current);
        } catch (e) { setError(String(e)); } finally { setBusy(false); }
      }}>{value.draft?.title || value.input.prompt.slice(0, 55)}</button>
        <small>{({ ready: "초안 준비", running: "작성 중", failed: "생성 실패", cancelled: "중단" } as Record<string, string>)[value.status]}{value.approval?.saved_version ? " · 저장됨" : ""}</small></li>)}</ul>
    </section><section className="report-preview" aria-label="보고서 초안">
      {error && <p className="error" role="alert">{error}</p>}
      {job?.status === "running" || (!job?.draft && !!job?.execution?.progress?.blocks.length) ? <ReportWriting key={job.id} startedAt={job.created_at} progress={job.execution?.progress} stopped={job.status !== "running"} /> : job?.draft ? <><h1>{job.draft.title}</h1>
        {job.input.template && <p className="hint">작성 기준: {job.input.template.values.name}</p>}
        <ReportTiming timings={job.execution?.timings} />
        {!job.approval && <ReportPreview key={job.id} taskId={job.id} draftHash={job.draft_sha256!} onLock={setPreviewLocked}
          onSaved={(doc, options) => { setSaved(doc); setSavedWithUnsaved(!!options?.unsavedChanges); }} sourceBlocks={job.execution?.progress?.blocks}
          onExit={() => { setPreviewLocked(false); setJob(null); setSaved(null); setSavedWithUnsaved(false); active.current = null; sessionStorage.removeItem("hwp-report-task"); }} />}
        {job.approval?.saved_version && !saved && <button className="primary" onClick={async () => {
          try { const value = await api<Approved>(`/report-tasks/${job.id}/approved`); onOpen(value.document); } catch (e) { setError(String(e)); }
        }}>저장된 보고서 편집하기</button>}
        <details className="report-text-details"><summary>생성 원문·출처 보기</summary><h2>핵심 요약</h2><p><ReportText text={job.draft.summary} emphasis={job.draft.emphasis} /></p>
        {job.draft.sections.map((section, i) => <section key={i}><h2>{i + 1}. {section.heading}</h2><ReportContent blocks={[
          ...section.paragraphs.map(text => ({ text, kind: "body", emphasis: job.draft!.emphasis })),
          ...(section.tables || []).map(t => ({ text: t.caption, kind: "table", ...t, emphasis: job.draft!.emphasis })),
          ...(section.diagrams || []).map(d => ({ text: d.caption, kind: "diagram", ...d, emphasis: job.draft!.emphasis })),
        ]} /></section>)}
        <h2>출처 및 확인 사항</h2><ol>{job.draft.sources.map(s => <li key={s.id}>[{s.id}] <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> · {s.published || "발행일 미확인"}</li>)}</ol>
        {job.draft.limitations.map((p, i) => <p key={i} className="hint">{p}</p>)}
        </details>
        {job.approval && !job.approval.saved_version && !saved && <button className="primary" disabled={busy || saving} onClick={() => void makeDocument()}>{saving ? "HWPX 만드는 중…" : output.current ? "같은 보고서 저장 재확인" : "기존 승인 저장 계속하기"}</button>}
      </> : <div className="report-empty"><h2>요청을 입력하면 이곳에 초안이 표시됩니다.</h2><p>핵심 요약, 본문, 출처를 함께 검토할 수 있습니다.</p></div>}
      {progress && <p role="status">{progress}</p>}
      {!saving && !saved && lease.current && !output.current && <button onClick={async () => {
        const current = lease.current!; setBusy(true);
        try { await api(`/documents/${current.documentId}/session`, { method: "DELETE", headers: { "X-Edit-Session": current.token } });
          lease.current = null; editor.current?.destroy(); editor.current = null; setProgress("초안은 보존되어 있습니다. 다시 시도하거나 문서 목록으로 돌아갈 수 있습니다."); }
        catch (e) { setError(String(e)); } finally { setBusy(false); }
      }}>문서 만들기 종료·초안 보존</button>}
      {!saving && !saved && !!output.current && <button onClick={() => downloadBytes(decode((output.current as { output_base64: string }).output_base64), (job?.draft?.title || "보고서") + " 복구용.hwpx")}>미확인 출력 복구용 다운로드</button>}
      {saved && <div className="notice"><a className="button" href={fileUrl(saved)}>완성 보고서 다운로드</a><button onClick={() => {
        if (!savedWithUnsaved || window.confirm("저장한 출력 이후의 편집은 아직 서버에 저장되지 않았습니다. 현재 초안을 다운로드한 뒤 저장된 문서를 열까요?")) onOpen(saved);
      }}>보고서 편집하기</button></div>}
    </section></div><div ref={host} className="report-engine" aria-hidden="true" />
  </main>;
}
