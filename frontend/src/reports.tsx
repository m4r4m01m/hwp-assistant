import React, { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, isAbortError, jsonRequest, downloadBytes, type Document } from "./api";
import { EditorAdapter, type ExportSnapshot } from "./editor";
import { ReportPreview, type ReportRecovery } from "./report-preview";
import { ReportWriting, ReportTiming, ReportFailureNotice, type LiveReport, type ReportFailure } from "./report-progress";
import { ReportContent, ReportText } from "./report-content";
import { ReportReview, type ReportDraft as Draft } from "./report-review";
import { ReportResearchNotice, ReportSources, type ResearchPolicy } from "./report-research";
import type { VisualSelection } from "./standard-designer";
import { WorkspaceNav, WorkspaceIcon, type WorkspaceSection } from "./workspace-nav";
import "./report-editor-workspace.css";
import { SavedDownload } from "./saved-download";
import { CapabilityNotice, useFeature } from "./capabilities";
import { usePageVisible, useMobileDrawerCanvas } from "./ui-state";
import { ReportStructure, structureError } from "./report-structure";

type Template = { id: string; revision: number; standard_id: string; reference_sha256: string;
  standard_profile_sha256: string; profile_sha256: string;
  values: { name: string; description: string; writing_criteria: string[] } };
type Cover = { date: string; department: string };
type ReportInput = { id: string; prompt: string; research: boolean; research_policy?: ResearchPolicy; template?: Template; visual_standard?: VisualSelection; cover?: Cover; section_outline?: string[] };
type RevisionRequest = { sourceId: string; body: { id: string; draft_sha256: string; feedback: string } };
type Job = { id: string; input: Omit<ReportInput, "id">; status: string; preview_id?: string | null; draft: Draft | null;
  draft_sha256: string | null; error: string; created_at?: number; execution?: { progress?: LiveReport; timings?: Record<string, number | null>; revision_of?: string; failure?: ReportFailure; web_search_calls?: number };
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
function selectionSnapshot(value: VisualSelection): VisualSelection {
  const { id, revision, name, settings, settings_sha256, base_standard, preview_sha256 } = value;
  return { id, revision, name, settings, settings_sha256, base_standard, preview_sha256 };
}

export function Reports({ onClose, onOpen, initialVisualStandard, onNavigate }: { onClose: () => void; onOpen: (doc: Document) => void; initialVisualStandard?: VisualSelection; onNavigate?: (section: WorkspaceSection) => void }) {
  const feature = useFeature("reports"), visible = usePageVisible();
  const workspaceRoot = useRef<HTMLElement>(null);
  useMobileDrawerCanvas(workspaceRoot, ".report-workspace-canvas", ".report-workspace-drawer:not([hidden])");
  const [pollError, setPollError] = useState("");
  const [workspacePanel, setWorkspacePanel] = useState<"request" | "content" | "history" | null>("request");
  const [previewTools, setPreviewTools] = useState<HTMLDivElement | null>(null);
  const [previewDetails, setPreviewDetails] = useState<HTMLDivElement | null>(null);
  const [writingTools, setWritingTools] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setWorkspacePanel(null); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, []);
  const [previewLocked, setPreviewLocked] = useState(false);
  const [formattingTaskId, setFormattingTaskId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");
  const [pendingRevision, setPendingRevision] = useState<RevisionRequest | null>(null);
  const [coverDate, setCoverDate] = useState(""), [coverDepartment, setCoverDepartment] = useState("");
  const [sectionOutline, setSectionOutline] = useState<string[] | undefined>();
  const [restoreTaskId, setRestoreTaskId] = useState<string | null>(null), [restoreAttempt, setRestoreAttempt] = useState(0);
  const [templates, setTemplates] = useState<Template[]>([]), [defaultTemplateId, setDefaultTemplateId] = useState("standard-business"),
    [savedWithUnsaved, setSavedWithUnsaved] = useState(false);
  const recovery = useRef<ReportRecovery | null>(null);
  const [recoverySnapshot, setRecoverySnapshot] = useState<ExportSnapshot | null>(null);
  const [recoveryError, setRecoveryError] = useState("");
  const [visualStandards, setVisualStandards] = useState<VisualSelection[]>([]),
    [visualId, setVisualId] = useState(initialVisualStandard?.id || "");
  const [visualCatalogReady, setVisualCatalogReady] = useState(false);
  const visualCatalogInitialized = useRef(false);
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
    // An explicit registered standard starts a new report; the previous frozen
    // task remains available in recent requests rather than replacing this choice.
    if (initialVisualStandard) return;
    const id = sessionStorage.getItem("hwp-report-task");
    if (id) void api<Job>(`/report-tasks/${id}`).then(value => {
      if (stopped || active.current !== null) return;
      if (value.approval?.saved_version) {
        sessionStorage.removeItem("hwp-report-task"); // Keep new report creation reachable after saving.
      } else selectJob(value);
    }).catch(cause => {
      if (stopped || active.current !== null) return;
      if (cause instanceof ApiError && cause.status === 404) sessionStorage.removeItem("hwp-report-task");
      else { setRestoreTaskId(id); setError("이전 보고서를 불러오지 못했습니다. 요청과 보완 의견은 보존되어 있습니다. " + String(cause)); }
    });
    return () => { stopped = true; };
  }, [restoreAttempt]);
  function acceptVisualCatalog(value: { items: VisualSelection[]; presets?: VisualSelection[]; default_id?: string }) {
    setVisualStandards([...(value.presets || []), ...value.items]);
    if (!visualCatalogInitialized.current) {
      visualCatalogInitialized.current = true;
      setVisualId(current => current || (active.current !== null ? "" : value.default_id || ""));
    }
    setVisualCatalogReady(true);
  }
  async function loadVisualStandards() {
    acceptVisualCatalog(await api<{ items: VisualSelection[]; presets?: VisualSelection[]; default_id?: string }>("/visual-standards"));
  }
  useEffect(() => {
    let stopped = false;
    void api<{ items: VisualSelection[]; presets?: VisualSelection[]; default_id?: string }>("/visual-standards").then(value => { if (!stopped) acceptVisualCatalog(value); })
      .catch(e => { if (!stopped) setError(String(e)); });
    return () => { stopped = true; };
  }, []);
  useEffect(() => {
    let stopped = false;
    const generation = ++templateFetch.current;
    void api<{ items: Template[]; default_id: string }>("/report-templates").then(value => {
      if (!stopped && generation === templateFetch.current) {
        setTemplates(value.items); setDefaultTemplateId(value.default_id || "standard-business");
      }
    }).catch(e => { if (!stopped) setError(String(e)); });
    return () => { stopped = true; };
  }, []);
  async function refreshTemplates() {
    const generation = ++templateFetch.current;
    try {
      const value = await api<{ items: Template[]; default_id?: string }>("/report-templates");
      if (generation === templateFetch.current) { setTemplates(value.items); setDefaultTemplateId(value.default_id || "standard-business"); setError(""); }
    } catch (e) { if (generation === templateFetch.current) setError(String(e)); }
  }
  useEffect(() => {
    if (job?.status !== "running" || !visible) return;
    let stopped = false;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let delay = 800;
    const fingerprint = (value: Job) => [value.id, value.status, value.draft_sha256, value.execution?.progress?.revision, value.execution?.progress?.stage, value.execution?.progress?.activity, value.execution?.progress?.research_calls, value.approval?.saved_version, value.preview_id].join(":");
    let previous = fingerprint(job);
    const poll = async () => {
      let terminal = false;
      try {
        const result = await api<Job>(`/report-tasks/${job.id}`, { signal: controller.signal });
        if (!stopped && active.current === result.id) {
          const next = fingerprint(result), changed = previous !== next;
          previous = next;
          delay = changed ? 800 : Math.min(5000, delay * 1.7);
          setPollError("");
          if (changed) setJob(current => current?.id === result.id && current.status === "running" ? result : current);
          terminal = result.status !== "running";
          if (terminal) setJobs(values => values.map(value => value.id === result.id ? result : value));
        }
      } catch (cause) {
        if (!stopped && !isAbortError(cause)) {
          setPollError(errorMessage(cause));
          delay = Math.max(Math.min(30000, delay * 2), cause instanceof ApiError ? cause.retryAfterMs || 0 : 0);
        }
      } finally { if (!stopped && !terminal) timer = setTimeout(() => void poll(), delay); }
    };
    void poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [job?.id, job?.status, visible]);
  useEffect(() => {
    const timer = setInterval(() => { const current = lease.current; if (current) void api(`/documents/${current.documentId}/session`,
      { method: "PUT", headers: { "X-Edit-Session": current.token } }).catch(e => setError(String(e))); }, 30000);
    return () => clearInterval(timer);
  }, []);
  function selectJob(value: Job) {
    setPollError("");
    setRestoreTaskId(null);
    sessionStorage.setItem("hwp-report-task", value.id);
    setJobs(values => values.map(item => item.id === value.id ? value : item));
    active.current = value.id; setJob(value); setPrompt(value.input.prompt); setResearch(value.input.research);
    setFormattingTaskId(value.preview_id || sessionStorage.getItem("hwp-report-format-task") === value.id ? value.id : null);
    setFeedback(sessionStorage.getItem(`hwp-report-feedback:${value.id}`) || "");
    try { setPendingRevision(JSON.parse(sessionStorage.getItem(`hwp-report-revision:${value.id}`) || "null")); }
    catch { setPendingRevision(null); }
    setCoverDate(value.input.cover?.date || ""); setCoverDepartment(value.input.cover?.department || "");
    setSectionOutline(value.input.section_outline ? [...value.input.section_outline] : undefined);
    setVisualId(value.input.visual_standard?.id || "");
    setSavedWithUnsaved(false);
    recovery.current = null; setRecoverySnapshot(null); setRecoveryError("");
    setSaved(null); setError(""); setProgress(""); approval.current = null; approvalBody.current = null; output.current = null;
  }
  async function requestReport() {
    if (!pending && !feature.available) { setError(feature.message); return; }
    const template = templates.find(item => item.id === defaultTemplateId);
    const visual = visualStandards.find(item => item.id === visualId);
    if (!pending && !template) { setError("보고서 작성 기준을 불러오지 못했습니다. 연결 정보를 다시 확인해 주세요."); return; }
    if (!pending && structureError(sectionOutline)) { setError(structureError(sectionOutline)); return; }
    if (!pending && !visualCatalogReady) { setError("문서 표준을 확인한 뒤 요청하세요."); return; }
    if (!pending && visualId && !visual) { setError("선택한 문서 표준을 다시 확인하세요."); return; }
    const frozen = pending || { id: crypto.randomUUID(), prompt, research, research_policy: "allow_unverified" as const, template, ...(visual ? { visual_standard: selectionSnapshot(visual) } : {}),
      ...(sectionOutline ? { section_outline: sectionOutline.map(title => title.trim()) } : {}),
      ...(visual?.revision === 3 && (visual.settings.cover_mode === "ON" || (visual.settings.cover_mode !== "OFF" && ["appendix-2", "appendix-3"].includes(String(visual.settings.preset_id)))) ? { cover: { date: coverDate.trim(), department: coverDepartment.trim() } } : {}) };
    setPending(frozen); active.current = frozen.id; setRestoreTaskId(null); setBusy(true); setError("");
    sessionStorage.setItem("hwp-report-task", frozen.id);
    try { const result = await api<Job>("/report-tasks", jsonRequest("POST", frozen));
      if (active.current === frozen.id) { selectJob(result); setPending(null); void refresh(); }
    } catch (e) { if (active.current === frozen.id) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) setPending(null); setError(String(e));
    } }
    finally { if (active.current === frozen.id) setBusy(false); }
  }
  async function reviseDraft() {
    if (!pendingRevision && !feature.available) { setError(feature.message); return; }
    if (!job?.draft_sha256 || (!pendingRevision && !feedback.trim())) return;
    const request = pendingRevision || { sourceId: job.id, body: { id: crypto.randomUUID(), draft_sha256: job.draft_sha256, feedback: feedback.trim() } };
    const sourceId = job.id;
    sessionStorage.setItem(`hwp-report-revision:${sourceId}`, JSON.stringify(request));
    setPendingRevision(request); setBusy(true); setError("");
    try {
      const result = await api<Job>(`/report-tasks/${request.sourceId}/revise`, jsonRequest("POST", request.body));
      if (active.current === sourceId) {
        sessionStorage.removeItem(`hwp-report-revision:${sourceId}`);
        sessionStorage.removeItem(`hwp-report-feedback:${sourceId}`);
        selectJob(result); setWorkspacePanel(null); void refresh();
      }
    } catch (cause) {
      if (active.current === sourceId) {
        if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
          setPendingRevision(null); sessionStorage.removeItem(`hwp-report-revision:${sourceId}`);
        }
        setError(String(cause));
      }
    } finally { setBusy(false); }
  }
  function beginFormatting() {
    if (job?.status !== "ready" || !job.draft || !job.draft_sha256 || pendingRevision || busy || feedback.trim()) return;
    sessionStorage.setItem("hwp-report-format-task", job.id);
    setFormattingTaskId(job.id); setWorkspacePanel(null);
  }
  function changeFeedback(value: string) {
    setFeedback(value);
    if (job) sessionStorage.setItem(`hwp-report-feedback:${job.id}`, value);
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
  const locked = busy || saving || !!lease.current || !!pending || !!pendingRevision || previewLocked;
  const completedDraft = job?.status === "ready" && !!job.draft && !!job.draft_sha256;
  const sourcesUnverified = !!job?.draft?.research_notice;
  const hasPreview = completedDraft && !job?.approval && formattingTaskId === job?.id;
  useEffect(() => {
    if (completedDraft) setWorkspacePanel(null);
  }, [job?.id, job?.draft_sha256, hasPreview]);
  useEffect(() => {
    if (saved) setWorkspacePanel("content");
  }, [saved?.id]);
  useEffect(() => {
    if (job?.status === "running") setWorkspacePanel("content");
  }, [job?.id, job?.status]);
  const selectedVisual = visualStandards.find(item => item.id === visualId)
    || (initialVisualStandard?.id === visualId ? initialVisualStandard : undefined);
  function downloadRecovery() {
    setRecoveryError("");
    try {
      if (!recovery.current) throw new Error("복구용 출력을 준비한 편집기를 확인할 수 없습니다. 현재 초안을 유지합니다.");
      setRecoverySnapshot(recovery.current.download());
    } catch (cause) { setRecoverySnapshot(null); setRecoveryError(String(cause)); }
  }
  function openSaved() {
    if (!saved) return;
    if (savedWithUnsaved) {
      try {
        if (!recoverySnapshot || !recovery.current?.matches(recoverySnapshot)) {
          setRecoverySnapshot(null);
          setRecoveryError("다운로드한 출력 이후 편집 내용이 달라졌거나 복구 출력을 확인할 수 없습니다. 복구 파일을 다시 다운로드하세요.");
          return;
        }
        if (!window.confirm("복구 파일을 실제로 저장하고 확인하셨나요? 확인하면 후속 미저장 편집을 닫고 서버에 저장된 보고서를 엽니다. 다운로드를 취소했거나 파일을 확인하지 못했다면 취소를 누르세요.")) return;
        if (!recovery.current.matches(recoverySnapshot)) {
          setRecoverySnapshot(null);
          setRecoveryError("편집 내용이 다시 변경되었습니다. 현재 초안을 유지했으니 복구 파일을 다시 다운로드하세요.");
          return;
        }
      } catch (cause) { setRecoverySnapshot(null); setRecoveryError(String(cause)); return; }
    }
    onOpen(saved);
  }
  const frozenVisual = job?.input.visual_standard;
  const formatName = (visual?: VisualSelection) => visual?.name || "이전 기본 서식 (호환용)";
  const formatDescription = (visual?: VisualSelection) => {
    const settings = visual?.settings, scope = settings?.body_parenthetical_scope;
    const labels: Record<string, string> = { square: "□", circle: "ㅇ", dash: "-", dot: "․", body: "일반 문단", summary: "핵심 요약" };
    const targets = typeof scope === "string" ? scope.split(",").map(key => labels[key]).filter(Boolean) : null;
    const description = settings?.body_parenthetical_labels === false || targets?.length === 0
      ? "사용 안 함" : targets ? targets.join("·") : "모든 본문 항목";
    return `괄호 요약어: ${description} · 핵심 요약 ${settings?.show_core_summary === false ? "미포함" : "포함"}`;
  };
  return <main ref={workspaceRoot} className="report-workspace report-editor-workspace">
    <WorkspaceNav active="reports" compact className="report-workspace-rail" label="보고서 작업 도구" onBack={onClose} onNavigate={onNavigate} disabled={locked || job?.status === "running"}>
      <button aria-label="작성 요청" title="새 보고서의 주제와 양식 선택" aria-expanded={workspacePanel === "request"} aria-controls="report-request-panel" onClick={() => setWorkspacePanel(value => value === "request" ? null : "request")}><WorkspaceIcon name="rename" /><span>작성 요청</span></button>
      <button aria-label={job?.status === "running" ? "작성 진행" : "결과·출처"} title={job?.status === "running" ? "보고서 작성 진행 정보" : "보고서 결과와 출처"} aria-expanded={workspacePanel === "content"} aria-controls="report-content-panel" onClick={() => setWorkspacePanel(value => value === "content" ? null : "content")}><WorkspaceIcon name="documents" /><span>{job?.status === "running" ? "진행" : "내용"}</span></button>
      <button aria-label="최근 보고서" title="이전 요청과 보완본 이어보기" aria-expanded={workspacePanel === "history"} aria-controls="report-history-panel" onClick={() => setWorkspacePanel(value => value === "history" ? null : "history")}><WorkspaceIcon name="history"/><span>최근 보고서</span></button>
      <div className="report-preview-tool-host" ref={setPreviewTools} />
    </WorkspaceNav>
    <div className="report-preview-details-host" ref={setPreviewDetails} />
    <aside id="report-request-panel" className="report-workspace-drawer report-request-drawer" hidden={workspacePanel !== "request"} aria-label="보고서 작성 요청 도구">
      <div className="report-drawer-heading"><div><small>보고서 작성</small><h1>{job ? "새 보고서 요청" : "무엇을 정리할까요?"}</h1></div><button aria-label="작성 요청 도구 닫기" onClick={() => setWorkspacePanel(null)}><WorkspaceIcon name="close" size={18} /></button></div>
      <CapabilityNotice feature="reports" label="보고서 AI 생성" />
      <section className="report-input">
        {job && <p className="report-current-format" aria-label="현재 보고서 고정 서식"><strong>현재 초안: {formatName(frozenVisual)}</strong><br />{formatDescription(frozenVisual)}<br />대제목: {job.input.section_outline?.join(" → ") || "자동 구성"}<br />자료 조회 미완료 시: {job.input.research_policy === "allow_unverified" ? "경고와 함께 초안 검토" : "초안 생성 중단 (기존 요청 정책)"}<br />아래 설정은 다음 보고서에 적용됩니다.</p>}
        <label htmlFor="report-prompt">보고서 작성 요청</label>
        <textarea id="report-prompt" rows={5} maxLength={6000} value={prompt} disabled={locked || job?.status === "running"} onChange={e => setPrompt(e.target.value)} placeholder="보고할 주제, 대상 독자, 반드시 포함할 사실과 요청 사항을 적어 주세요." />
        <div className="report-form-options">
          <ReportStructure value={sectionOutline} onChange={setSectionOutline} disabled={locked || job?.status === "running"} />
          <label htmlFor="report-visual-standard">문서 양식</label>
          <select id="report-visual-standard" value={visualId} disabled={locked || job?.status === "running" || !visualCatalogReady} onChange={e => setVisualId(e.target.value)}>
            {visualStandards.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
            <optgroup label="이전 서식"><option value="">이전 기본 서식 (호환용)</option></optgroup>
          </select>
          <p className="hint">문서 양식은 표지·목차와 글꼴·여백을 정합니다.</p>
          <p className="hint" aria-label="새 요청에 사용할 서식">{formatDescription(selectedVisual)}</p>
          {selectedVisual?.revision === 3 && (selectedVisual.settings.cover_mode === "ON" || (selectedVisual.settings.cover_mode !== "OFF" && selectedVisual.settings.preset_id !== "appendix-1")) && <div className="report-cover-fields">
            <label htmlFor="report-cover-date">표지 작성일</label><input id="report-cover-date" value={coverDate} maxLength={60} disabled={locked || job?.status === "running"} placeholder="예: 2026. 10. 20." onChange={e => setCoverDate(e.target.value)} />
            <label htmlFor="report-cover-department">표지 작성 부서</label><input id="report-cover-department" value={coverDepartment} maxLength={100} disabled={locked || job?.status === "running"} placeholder="예: 운영지원부 문서관리팀" onChange={e => setCoverDepartment(e.target.value)} />
          </div>}
          <label className="check-label"><input type="checkbox" checked={research} disabled={locked || job?.status === "running"} onChange={e => setResearch(e.target.checked)} />공개 웹 자료를 검색하고 출처 포함</label>
          <p className="hint" aria-label="새 요청의 자료 조회 정책">{research ? "공개자료 조회를 시도합니다. 조회가 완료되지 않아도 최종 초안이 유효하면 미검증 경고와 함께 내용을 검토할 수 있습니다." : "공개자료를 조회하지 않고 제공한 자료와 일반 분석으로 작성합니다."}</p>
        </div>
        <details><summary>요청 예시</summary>
          <div className="report-examples">{["금융권 해킹 대응", "외부 업체·클라우드 보안", "고객정보 유출 대응"].map((name, i) => <button key={name} disabled={locked || job?.status === "running"} onClick={() => setPrompt(examples[i])}>{name}</button>)}</div>
        </details>
        <details><summary>양식 관리·연결 정보</summary>
          <button className="report-open-standard" disabled={locked || job?.status === "running" || !onNavigate} onClick={() => onNavigate?.("standards")}>문서작성 기준 열기</button>
          <button disabled={locked} onClick={() => void refreshTemplates()}>작성 기준 다시 확인</button>
          <button disabled={locked} onClick={() => void loadVisualStandards().then(() => setError("")).catch(e => setError(String(e)))}>문서 표준 목록 다시 확인</button>
          <p className="hint">기존 Codex 로그인으로 처리합니다. 공개자료 검색에는 검색어가 사용됩니다.</p>
        </details>
      </section>
      <div className="report-request-submit">
        <button className="primary" disabled={busy || saving || !!lease.current || previewLocked || !!pendingRevision || (!pending && (!feature.available || !visualCatalogReady || prompt.trim().length < 5 || !!structureError(sectionOutline) || !templates.some(item => item.id === defaultTemplateId))) || job?.status === "running"} onClick={() => void requestReport()}>{busy ? "요청 확인 중…" : pending ? "같은 요청 접수 재확인" : "초안 작성하기"}</button>
        <p className="hint">초안을 확인하고 보완한 뒤 문서로 만듭니다.</p>
      </div>
    </aside>
    <aside id="report-history-panel" className="report-workspace-drawer" hidden={workspacePanel !== "history"} aria-label="최근 보고서 요청">
      <div className="report-drawer-heading"><h2>최근 보고서</h2><button aria-label="최근 보고서 닫기" onClick={() => setWorkspacePanel(null)}><WorkspaceIcon name="close" size={18}/></button></div>
      <p className="hint">요청한 초안과 보완본을 이어서 확인합니다. 저장한 보고서는 문서함에서도 열 수 있습니다.</p>
      <button disabled={locked} onClick={() => void refresh()}>목록 새로고침</button>
      {!jobs.length && <p className="hint">아직 작성한 보고서가 없습니다.</p>}
      <ul className="report-history">{jobs.map(value => <li key={value.id}><button disabled={locked || job?.status === "running"} onClick={async () => {
        setBusy(true); setError(""); try { const current = await api<Job>(`/report-tasks/${value.id}`);
          if (current.approval?.saved_version) { const approved = await api<Approved>(`/report-tasks/${value.id}/approved`); onOpen(approved.document); }
          else selectJob(current);
        } catch (e) { setError(String(e)); } finally { setBusy(false); }
      }}>{value.draft?.title || value.input.prompt.slice(0, 55)}</button>
      <small>{value.execution?.revision_of ? "보완본 · " : ""}{({ ready: "초안 준비", running: "작성 중", failed: "생성 실패", cancelled: "중단" } as Record<string, string>)[value.status]}{value.approval?.saved_version ? " · 저장됨" : ""}</small></li>)}</ul>
    </aside>
    <section className="report-preview report-workspace-canvas" aria-label="보고서 초안">
      {pollError && <p className="report-workspace-error error" role="alert">{pollError}</p>}
      {error && <p className="report-workspace-error error" role="alert">{error}{restoreTaskId && <button onClick={() => { setError(""); setRestoreAttempt(value => value + 1); }}>이전 요청 다시 열기</button>}</p>}
      {!error && job?.status === "failed" && <ReportFailureNotice message={job.error} failure={job.execution?.failure} onRequest={() => setWorkspacePanel("request")} />}
      {job?.status === "running" || job?.status === "failed" || (!completedDraft && !!job?.execution?.progress?.blocks.length) ? <ReportWriting key={job.id} startedAt={job.created_at} progress={job.execution?.progress} stopped={job.status !== "running"} controlsHost={writingTools} /> : completedDraft && job?.draft ? <>
        {!job.approval && !hasPreview && <ReportReview draft={job.draft} sourcesUnverified={sourcesUnverified} formatName={formatName(frozenVisual)} includesSummary={frozenVisual?.settings.show_core_summary !== false} feedback={feedback} onFeedback={changeFeedback} onRevise={() => void reviseDraft()} onFormat={beginFormatting} busy={busy} retry={!!pendingRevision} revisionAvailable={feature.available} />}
        {hasPreview && <ReportPreview key={job.id} taskId={job.id} draftHash={job.draft_sha256!} onLock={setPreviewLocked} toolbarHost={previewTools} detailsHost={previewDetails}
          workspacePanelOpen={workspacePanel !== null} onShowDetails={() => setWorkspacePanel(null)}
          researchNotice={job.draft.research_notice}
          onSaved={(doc, options) => {
            setSaved(doc); setSavedWithUnsaved(!!options?.unsavedChanges);
            recovery.current = options?.recovery || null; setRecoverySnapshot(null); setRecoveryError("");
            // The saved staging iframe is deliberately inert. Continue in the
            // regular editor instead of leaving a seemingly frozen document.
            // A later unsaved buffer must stay in its recovery flow.
            if (!options?.unsavedChanges) onOpen(doc);
          }}
          onRecoverySnapshot={snapshot => { setRecoverySnapshot(snapshot); setRecoveryError(""); }} sourceBlocks={job.execution?.progress?.blocks}
          onExit={() => { setPreviewLocked(false); setJob(null); setSaved(null); setSavedWithUnsaved(false); active.current = null; sessionStorage.removeItem("hwp-report-task"); sessionStorage.removeItem("hwp-report-format-task"); setFormattingTaskId(null); setFeedback(""); setWorkspacePanel("request"); }} />}
        {job.approval && <div className="report-empty"><h2>{job.draft.title}</h2><p>결과·출처에서 저장 상태를 확인하고 보고서를 열 수 있습니다.</p><button onClick={() => setWorkspacePanel("content")}>보고서 결과 보기</button></div>}
      </> : <div className="report-empty"><h2>요청을 입력하면 이곳에 초안이 표시됩니다.</h2><p>핵심 요약, 본문, 출처를 함께 검토할 수 있습니다.</p><button onClick={() => setWorkspacePanel("request")}>작성 요청 열기</button></div>}
    </section>
    <aside id="report-content-panel" className="report-workspace-drawer report-content-drawer" hidden={workspacePanel !== "content"} aria-label="보고서 결과와 출처">
      <div className="report-drawer-heading"><h2>결과·출처</h2><button aria-label="보고서 결과와 출처 닫기" onClick={() => setWorkspacePanel(null)}><WorkspaceIcon name="close" size={18} /></button></div>
      <div ref={setWritingTools} />
      {job?.status === "running" && <button disabled={busy} onClick={async () => {
        const id = job.id; setBusy(true); try { const result = await api<Job>(`/report-tasks/${id}/cancel`, { method: "POST" }); if (active.current === id) setJob(result); }
        catch (cause) { setError(String(cause)); } finally { setBusy(false); }
      }}>결과 수신 중단</button>}
      {job?.status === "cancelled" && <p>작성을 중단했습니다. 새 요청으로 다시 시작할 수 있습니다.</p>}
      {completedDraft && job?.draft ? <><h1>{job.draft.title}</h1>
        <ReportResearchNotice notice={job.draft.research_notice} />
        {job.input.section_outline && <p className="hint">대제목 구성: {job.input.section_outline.join(" → ")}</p>}
        {job.input.visual_standard && <p className="hint">문서 양식: {job.input.visual_standard.name}</p>}
        <ReportTiming timings={job.execution?.timings} />
        {job.approval?.saved_version && !saved && <button className="primary" onClick={async () => {
          try { const value = await api<Approved>(`/report-tasks/${job.id}/approved`); onOpen(value.document); } catch (e) { setError(String(e)); }
        }}>저장된 보고서 편집하기</button>}
        <details className="report-text-details"><summary>AI 생성 원문·출처</summary><p className="hint">처음 생성한 내용입니다. 문서 편집기에서 수정한 내용은 여기에 반영되지 않습니다.</p><h2>내용 요약</h2><p><ReportText text={job.draft.summary} emphasis={job.draft.emphasis} /></p>
        {job.draft.sections.map((section, i) => <section key={i}><h2>{i + 1}. {section.heading}</h2><ReportContent notes={job.draft!.notes} blocks={[
          ...section.paragraphs.map(text => ({ text, kind: "body", emphasis: job.draft!.emphasis })),
          ...(section.tables || []).map(t => ({ text: t.caption, kind: "table", ...t, emphasis: job.draft!.emphasis })),
          ...(section.diagrams || []).map(d => ({ text: d.caption, kind: "diagram", ...d, emphasis: job.draft!.emphasis })),
        ]} /></section>)}
        {job.draft.notes && <p className="hint">조사 출처와 작성자 검토 메모는 보관용입니다. 문서에는 필요한 부연설명만 넣습니다.</p>}
        <h2>조사 출처·검토 메모</h2><ReportSources sources={job.draft.sources} unverified={sourcesUnverified} />
        {job.draft.limitations.map((p, i) => <p key={i} className="hint">{p}</p>)}
        </details>
        {job.approval && !job.approval.saved_version && !saved && <button className="primary" disabled={busy || saving} onClick={() => void makeDocument()}>{saving ? "HWPX 만드는 중…" : output.current ? "같은 보고서 저장 재확인" : "기존 승인 저장 계속하기"}</button>}
      </> : <p className="hint">초안이 완성되면 생성 원문과 출처를 확인할 수 있습니다.</p>}
      {progress && <p role="status">{progress}</p>}
      {!saving && !saved && lease.current && !output.current && <button onClick={async () => {
        const current = lease.current!; setBusy(true);
        try { await api(`/documents/${current.documentId}/session`, { method: "DELETE", headers: { "X-Edit-Session": current.token } });
          lease.current = null; editor.current?.destroy(); editor.current = null; setProgress("초안은 보존되어 있습니다. 다시 시도하거나 문서 목록으로 돌아갈 수 있습니다."); }
        catch (e) { setError(String(e)); } finally { setBusy(false); }
      }}>문서 만들기 종료·초안 보존</button>}
      {!saving && !saved && !!output.current && <button onClick={() => downloadBytes(decode((output.current as { output_base64: string }).output_base64), (job?.draft?.title || "보고서") + " 복구용.hwpx")}>미확인 출력 복구용 다운로드</button>}
      {saved && <div className="notice"><SavedDownload document={saved} label="완성 보고서 다운로드" />{savedWithUnsaved ? <div className="report-recovery-actions">
        <p>후속 편집은 아직 서버에 저장되지 않았습니다. 복구 파일을 다운로드하고 실제 저장을 확인한 뒤 저장본을 여세요.</p>
        <button onClick={downloadRecovery}>복구 파일 다운로드</button>
        {recoverySnapshot && <p aria-live="polite">복구 파일 다운로드를 요청했습니다. 다운로드가 취소되거나 실패했다면 현재 초안을 유지하고 다시 다운로드하세요.</p>}
        {recoveryError && <p className="error" role="alert">{recoveryError}</p>}
        <button disabled={!recoverySnapshot} onClick={openSaved}>복구 파일 저장 확인·저장본 열기</button>
      </div> : <button onClick={openSaved}>보고서 편집하기</button>}</div>}
    </aside><div ref={host} className="report-engine" aria-hidden="true" />
  </main>;
}
