import { useEffect, useRef, useState } from "react";
import { api, ApiError, downloadFile, errorMessage, fileUrl, jsonRequest, type Document } from "./api";
import { EditorAdapter } from "./editor";
import { safeSvg } from "./block-insert";
import { publicPath } from "./public-path";
import { dateLabel, useComparisonDialog } from "./version-comparison";
import { textDifference } from "./text-diff";
import "./document-branches.css";

type Branch = { document_id: string; source_document_id: string; base_version_id: string; owner_id: string;
  name: string; current_version: string | null; created_at: number; can_edit: boolean };
type Integration = { id: string; branch_document_id: string; source_document_id: string; source_version_id: string;
  target_version_id: string; author_id: string; status: "pending" | "merged" | "rejected"; created_at: number;
  reviewed_by: string | null; reviewed_at: number | null; merged_version_id: string | null; can_review: boolean };
type Flow = { source_document: Document; branches: Branch[]; requests: Integration[];
  versions: { id: string; base_version: string | null; created_at: number; author?: {user_id: string} | null }[] };
type Review = { request: Integration; merged_sha256: string; changes: {path: string}[];
  before_text: string; after_text: string; truncated: boolean };
type Pending = { path: string; body: unknown; label: string; action: "create" | "submit" | "accept" | "reject" };
const statusName = {pending: "검토 대기", merged: "통합 완료", rejected: "반려"};

export function DocumentBranches({ document: doc, onClose, onOpen, onUpdated }: {
  document: Document; onClose: () => void; onOpen: (document: Document) => void; onUpdated: () => void;
}) {
  const dialog = useRef<HTMLElement>(null), locked = useRef(false), alive = useRef(true), pending = useRef<Pending | null>(null);
  const generation = useRef(0);
  const [flow, setFlow] = useState<Flow | null>(null), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [name, setName] = useState("내 수정안"), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [review, setReview] = useState<Review | null>(null), [reviewId, setReviewId] = useState<string | null>(null);
  const [preview, setPreview] = useState(false), [confirmed, setConfirmed] = useState(false), [retry, setRetry] = useState(false);
  const close = () => { if (!locked.current) onClose(); };
  useComparisonDialog(dialog, close);
  async function reload() {
    const request = ++generation.current;
    const value = await api<Flow>(`/documents/${doc.id}/branches`);
    if (alive.current && request === generation.current) setFlow(value);
  }
  useEffect(() => {
    alive.current = true;
    void reload().catch(cause => { if (alive.current) setError(errorMessage(cause)); })
      .finally(() => { if (alive.current) setLoading(false); });
    return () => { alive.current = false; generation.current++; };
  }, [doc.id]);
  async function mutate(operation: Pending) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(""); setNotice(""); pending.current = operation;
    try {
      await api(operation.path, jsonRequest("POST", operation.body));
      pending.current = null; setRetry(false);
      if (!alive.current) return;
      setReview(null); setReviewId(null); setPreview(false); setConfirmed(false);
      setNotice(operation.action === "create" ? "내 작업본을 만들었습니다. 원본과 별도로 편집·저장하세요."
        : operation.action === "submit" ? "저장된 작업본으로 통합을 요청했습니다. 이후 편집은 이 요청에 포함되지 않습니다."
        : operation.action === "accept" ? "검토한 수정안을 기준 문서의 새 버전으로 통합했습니다." : "통합 요청을 반려했습니다. 작업본은 보존됩니다.");
      onUpdated(); await reload().catch(cause => { if (alive.current) setError(`처리는 완료됐지만 목록을 다시 읽지 못했습니다. ${errorMessage(cause)}`); });
    } catch (cause) {
      if (!alive.current) return;
      setError(errorMessage(cause));
      const uncertain = !(cause instanceof ApiError) || cause.status >= 500;
      setRetry(uncertain);
      if (!uncertain) pending.current = null;
      if (cause instanceof ApiError && cause.status === 409) { setReview(null); setConfirmed(false); await reload().catch(() => {}); }
    } finally { locked.current = false; if (alive.current) setBusy(false); }
  }
  async function showReview(item: Integration) {
    if (locked.current || retry) return;
    locked.current = true; setBusy(true); setError(""); setReview(null); setReviewId(item.id); setPreview(false); setConfirmed(false);
    try { const value = await api<Review>(`/integration-requests/${item.id}/review`); if (alive.current) setReview(value); }
    catch (cause) { if (alive.current) setError(errorMessage(cause)); }
    finally { locked.current = false; if (alive.current) setBusy(false); }
  }
  const disabled = busy || retry;
  const versionName = (id: string) => {
    const version = flow?.versions.find(item => item.id === id);
    return version ? `${dateLabel(version.created_at)} 저장본` : "이전 저장본";
  };
  return <div className="branch-overlay"><section ref={dialog} role="dialog" aria-modal="true" aria-labelledby="branch-title" className="branch-dialog">
    <header><div><h2 id="branch-title">작업본·통합</h2><p>{flow?.source_document.name || doc.name}</p></div><button onClick={close} disabled={busy}>닫기</button></header>
    <p className="branch-intro">각자 작업본에서 수정한 뒤, 저장한 내용을 통합 요청하세요. 기준 문서는 다른 검토자가 승인할 때 바뀝니다.</p>
    {error && <p role="alert" className="branch-error">{error}</p>}
    {notice && <p role="status" className="branch-notice">{notice}</p>}
    {retry && <div className="branch-error"><p>처리 결과가 불확실합니다. 새 요청을 만들지 않고 같은 요청으로 확인합니다.</p><button disabled={busy} onClick={() => pending.current && void mutate(pending.current)}>같은 요청으로 결과 확인</button></div>}
    {loading && <p role="status">작업 흐름을 불러오는 중…</p>}
    {!loading && !flow && <button disabled={busy} onClick={() => { setLoading(true); setError(""); void reload().catch(cause => setError(errorMessage(cause))).finally(() => setLoading(false)); }}>다시 불러오기</button>}
    {flow && <>
      <form className="branch-create" onSubmit={event => { event.preventDefault(); if (!name.trim() || disabled) return;
        void mutate({path: `/documents/${flow.source_document.id}/branches`, body: {document_id: crypto.randomUUID(), source_version: flow.source_document.current_version, request_id: crypto.randomUUID(), name: name.trim()}, action: "create", label: "작업본 만들기"}); }}>
        <label>작업본 이름<input value={name} maxLength={200} disabled={disabled} onChange={event => setName(event.target.value)} placeholder="예: 대응방안 보완" /></label>
        <button disabled={disabled || !name.trim()}>내 작업본 만들기</button><span>기준 {versionName(flow.source_document.current_version)}에서 분기</span>
      </form>
      <div className="branch-map" aria-label="문서 분기와 통합 흐름">
        <div className="branch-main-node"><strong>기준 문서 · {versionName(flow.source_document.current_version)}</strong><span>승인된 결과와 기존 버전 보존</span></div>
        {!flow.branches.length && <p className="branch-empty">아직 작업본이 없습니다. 위에서 첫 작업본을 만들어 주세요.</p>}
        <ul className="branch-lanes">{flow.branches.map(branch => {
          const requests = flow.requests.filter(item => item.branch_document_id === branch.document_id);
          const latest = [...requests].sort((a,b) => b.created_at - a.created_at)[0];
          return <li key={branch.document_id} className="branch-lane" data-branch-id={branch.document_id}>
            <div className="branch-origin" title={`분기 기준: ${versionName(branch.base_version_id)}`} aria-label={`분기 기준: ${versionName(branch.base_version_id)}`}>분기<span aria-hidden="true">↘</span></div>
            <article className="branch-node"><div><b>{branch.name}</b><span>{branch.can_edit ? "내 작업본" : branch.owner_id} · {dateLabel(branch.created_at)}</span></div>
              {branch.can_edit ? <div className="branch-actions"><button disabled={disabled || !branch.current_version} onClick={() => onOpen({id: branch.document_id, name: branch.name, current_version: branch.current_version!, created_at: branch.created_at, role: "owner", branch_source_id: branch.source_document_id})}>작업본 편집</button>
                <button disabled={disabled || !branch.current_version || (latest?.status === "pending" && latest.target_version_id === flow.source_document.current_version && latest.source_version_id === branch.current_version)} onClick={() => void mutate({path: `/documents/${branch.document_id}/integration-requests`, body: {request_id: crypto.randomUUID(), source_version: branch.current_version, target_version: flow.source_document.current_version}, action: "submit", label: "통합 요청"})}>{latest?.status === "pending" ? "새 저장본으로 통합 요청" : "통합 요청"}</button></div>
                : <span className="branch-muted">개인 작업 내용은 제출 후 검토할 수 있습니다.</span>}
              {latest?.status === "pending" && branch.can_edit && <small>제출 이후 수정 내용은 기존 요청에 포함되지 않습니다.</small>}
            </article>
            <span className="branch-arrow" aria-hidden="true">→</span>
            <div className="branch-requests">{!requests.length ? <span className="branch-draft">작성 중</span> : requests.map(item => <article key={item.id} className={`branch-request ${item.status}`}>
              <strong>{statusName[item.status]}</strong><span>{dateLabel(item.created_at)}</span>
              {item.status === "merged" && <span>↗ 기준 {versionName(item.merged_version_id!)} · {item.reviewed_by} 승인</span>}
              {item.status === "rejected" && <span>{item.reviewed_by} 검토 · 작업본 유지</span>}
              {item.status === "pending" && <button disabled={disabled} onClick={() => void showReview(item)}>{item.can_review ? "수정안 검토" : "제출 내용 확인"}</button>}
            </article>)}</div>
          </li>;
        })}</ul>
      </div>
      {reviewId && <section className="branch-review" aria-label="통합 수정안 검토">
        <h3>기준 {review ? versionName(review.request.target_version_id) : "문서"}에 반영할 수정안</h3>
        {busy && !review && <p role="status">분기 이후 변경을 병합하고 확인하는 중…</p>}
        {review && <>
          <p>{review.request.author_id}의 제출 당시 저장본입니다. 기준 문서가 변경되면 승인을 중단하고 다시 검토합니다.</p>
          <div className="branch-text-changes"><div><span className="branch-change-label">삭제한 내용은 붉은 취소선, 추가한 내용은 초록색으로 표시합니다.</span><p>{textDifference(review.before_text, review.after_text).map((part, j) => part.kind === "added" ? <ins key={j}>{part.text}</ins> : part.kind === "removed" ? <del key={j}>{part.text}</del> : <span key={j}>{part.text}</span>)}</p></div></div>
          {review.truncated && <p>긴 문서의 텍스트 비교는 일부만 표시됩니다. 문서 모양으로 전체를 확인하세요.</p>}
          <button disabled={busy} onClick={() => setPreview(value => !value)}>{preview ? "문서 비교 접기" : "문서 모양으로 전후 비교"}</button>
          {preview && <div className="branch-preview-pair"><BranchPreview title="현재 기준 문서" url={fileUrl(flow.source_document, review.request.target_version_id)} /><BranchPreview title="통합 후 예상 문서" url={publicPath(`api/integration-requests/${review.request.id}/preview`)} /></div>}
          <details><summary>변경된 문서 구성 {review.changes.length}개</summary><ul>{review.changes.map(change => <li key={change.path}>{change.path}</li>)}</ul></details>
          {review.request.can_review ? <div className="branch-decision"><label><input type="checkbox" checked={confirmed} disabled={disabled} onChange={event => setConfirmed(event.target.checked)} />수정안을 확인했습니다.</label>
            <button disabled={disabled || !confirmed} onClick={() => void mutate({path: `/integration-requests/${review.request.id}/accept`, body: {merged_sha256: review.merged_sha256}, action: "accept", label: "승인·통합"})}>승인·통합</button>
            <button disabled={disabled} onClick={() => void mutate({path: `/integration-requests/${review.request.id}/reject`, body: {}, action: "reject", label: "반려"})}>반려</button></div>
            : <p className="branch-muted">본인이 제출한 수정안은 직접 승인할 수 없습니다. 기준 문서의 다른 관리자에게 검토를 요청하세요.</p>}
        </>}
      </section>}
    </>}
  </section></div>;
}

function BranchPreview({title, url}: {title: string; url: string}) {
  const host = useRef<HTMLDivElement>(null), adapter = useRef<EditorAdapter | null>(null);
  const [page, setPage] = useState(0), [pages, setPages] = useState(0), [svg, setSvg] = useState(""), [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController(); let alive = true; let instance: EditorAdapter | undefined;
    void (async () => {
      const bytes = await downloadFile(url, {signal: controller.signal});
      instance = await EditorAdapter.mount(host.current!, {signal: controller.signal});
      if (!alive) { instance.destroy(); return; } adapter.current = instance;
      await instance.load(bytes, "통합 검토.hwpx");
      const count = await instance.sdk.pageCount(); if (alive) setPages(count);
    })().catch(cause => { if (alive) setError(errorMessage(cause)); });
    return () => { alive = false; controller.abort(); instance?.destroy(); adapter.current = null; };
  }, [url]);
  useEffect(() => {
    let alive = true;
    if (pages && adapter.current) void adapter.current.sdk.getPageSvg(page).then(value => {if (alive) setSvg(safeSvg(value));}).catch(cause => { if (alive) setError(errorMessage(cause)); });
    return () => { alive = false; };
  }, [page, pages]);
  return <section className="branch-preview"><h4>{title}</h4>{error && <p role="alert">{error}</p>}
    {!pages && !error && <p role="status">실제 문서 배치 확인 중…</p>}
    <nav aria-label={`${title} 쪽 이동`}><button disabled={!page} onClick={() => setPage(page - 1)}>이전</button><span>{pages ? page + 1 : 0} / {pages}</span><button disabled={!pages || page + 1 >= pages} onClick={() => setPage(page + 1)}>다음</button></nav>
    <div className="branch-preview-paper" dangerouslySetInnerHTML={{__html: svg}} /><div ref={host} className="branch-preview-engine" aria-hidden="true" inert />
  </section>;
}
