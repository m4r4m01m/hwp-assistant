import { useEffect, useRef, useState } from "react";
import { api, ApiError, downloadFile, errorMessage, fileUrl, jsonRequest, type Document, type Version } from "./api";
import { EditorAdapter } from "./editor";
import { safeSvg } from "./block-insert";
import { publicPath } from "./public-path";
import { causeLabel, dateLabel, useComparisonDialog, VersionComparison } from "./version-comparison";
import { SavedDownload } from "./saved-download";
import { textDifference } from "./text-diff";
import "./document-branches.css";

type Branch = { document_id: string; source_document_id: string; base_version_id: string; owner_id: string;
  name: string; current_version: string | null; created_at: number; can_edit: boolean; can_submit?: boolean; can_manage_participants?: boolean };
type Integration = { id: string; branch_document_id: string; source_document_id: string; source_version_id: string;
  target_version_id: string; author_id: string; status: "pending" | "merged" | "rejected"; created_at: number;
  reviewed_by: string | null; reviewed_at: number | null; merged_version_id: string | null; can_review: boolean };
type GraphNode = Omit<Partial<Version>, "author"> & {id: string; document_id: string; kind: "version" | "anchor"; author?: {user_id:string} | null; current?: boolean; can_read?: boolean; can_edit?: boolean; can_restore?: boolean; file_url?: string | null; document?:Document|null};
type Graph = {nodes: GraphNode[]; edges: {from:string;to:string;kind:"save"|"branch"|"merge"|"restore"}[]; truncated:boolean};
type Flow = { graph?: Graph; source_document: Document; branches: Branch[]; requests: Integration[];
  versions: { id: string; base_version: string | null; created_at: number; author?: {user_id: string} | null }[] };
type Review = { request: Integration; merged_sha256: string; changes: {path: string}[];
  before_text: string; after_text: string; truncated: boolean };
type Pending = { path: string; body: unknown; label: string; action: "create" | "submit" | "accept" | "reject" };
const statusName = {pending: "검토 대기", merged: "통합 완료", rejected: "반려"};

export function DocumentBranches({ document: doc, onClose, onOpen, onUpdated, onCoedit, onRestore }: {
  document: Document; onClose: () => void; onOpen: (document: Document) => void; onUpdated: () => void;
  onCoedit?: (document: Document) => void; onRestore?: (document: Document, versionId: string, requestId: string) => Promise<Version>;
}) {
  const dialog = useRef<HTMLElement>(null), locked = useRef(false), alive = useRef(true), pending = useRef<Pending | null>(null);
  const generation = useRef(0);
  const [flow, setFlow] = useState<Flow | null>(null), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [name, setName] = useState("내 수정안"), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [review, setReview] = useState<Review | null>(null), [reviewId, setReviewId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creatingFrom, setCreatingFrom] = useState<string | null>(null);
  const [comparisonBase, setComparisonBase] = useState<{documentId:string;versionId:string;label:string}|null>(null);
  const [comparison, setComparison] = useState<{document:Document;left:Version;right:Version}|null>(null);
  const pendingRestore = useRef<{document: Document; version: string; key: string} | null>(null);
  const [restoreRetry, setRestoreRetry] = useState(false);
  const [preview, setPreview] = useState(false), [confirmed, setConfirmed] = useState(false), [retry, setRetry] = useState(false);
  const close = () => { if (!locked.current && (!retry && !restoreRetry || confirm("처리 결과가 확인되지 않은 요청이 있습니다. 닫기 전에 같은 요청으로 확인하는 것이 좋습니다. 닫을까요?"))) onClose(); };
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
    if (locked.current || restoreRetry) return;
    locked.current = true; setBusy(true); setError(""); setNotice(""); pending.current = operation;
    try {
      const result = await api<{document?: Document; merged_version_id?: string}>(operation.path, jsonRequest("POST", operation.body));
      pending.current = null; setRetry(false);
      if (!alive.current) return;
      setReview(null); setReviewId(null); setPreview(false); setConfirmed(false);
      if (operation.action === "create" && result.document) setSelectedId(result.document.current_version);
      if (operation.action === "accept" && result.merged_version_id) setSelectedId(result.merged_version_id);
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
  async function restoreSelected(document: Document, version: string) {
    if (!onRestore || locked.current || retry) return;
    if (!pendingRestore.current) {
      if (!confirm("선택한 저장본을 새 버전으로 복원할까요? 현재 버전과 과거 기록은 그대로 보존됩니다.")) return;
      pendingRestore.current = {document: {...document}, version, key: crypto.randomUUID()};
    }
    locked.current = true; setBusy(true); setError("");
    const frozen = pendingRestore.current;
    try {
      const restored = await onRestore(frozen.document, frozen.version, frozen.key);
      pendingRestore.current = null; setRestoreRetry(false); setSelectedId(restored.id);
      setNotice("선택한 저장본을 새 버전으로 복원했습니다. 이전 이력은 보존됩니다.");
      onUpdated(); await reload().catch(cause => setError(errorMessage(cause)));
    } catch (cause) {
      setError(errorMessage(cause));
      const uncertain = !(cause instanceof ApiError) || cause.status >= 500;
      setRestoreRetry(uncertain); if (!uncertain) pendingRestore.current = null;
      if (cause instanceof ApiError && cause.status === 409) await reload().catch(() => {});
    } finally { locked.current = false; if (alive.current) setBusy(false); }
  }
  const disabled = busy || retry || restoreRetry;
  const graph: Graph = flow?.graph || {nodes: (flow?.versions || []).map(version => ({...version, document_id: flow!.source_document.id, kind: "version", current: version.id === flow!.source_document.current_version, can_read: true, can_restore: flow!.source_document.role !== "reader", can_edit: version.id === flow!.source_document.current_version && flow!.source_document.role !== "reader"})), edges: (flow?.versions || []).filter(version => version.base_version).map(version => ({from:version.base_version!,to:version.id,kind:"save"})), truncated:false};
  const selected = graph.nodes.find(node => node.id === selectedId) || graph.nodes.find(node => node.document_id === doc.id && node.current) || graph.nodes.find(node => node.current) || graph.nodes.find(node => node.kind === "version");
  const selectedBranch = flow?.branches.find(branch => branch.document_id === selected?.document_id);
  const selectedDocument: Document | null = !selected || !flow ? null : selected.document || (selected.document_id === flow.source_document.id ? flow.source_document : selectedBranch ? {
    id:selectedBranch.document_id, name:selectedBranch.name, current_version:selectedBranch.current_version || selected.id, created_at:selectedBranch.created_at,
    role: selectedBranch.can_edit ? "editor" : "reader", branch_source_id:selectedBranch.source_document_id,
    workspace_id:flow.source_document.workspace_id, workspace_name:flow.source_document.workspace_name,
  } : null);
  const branchRequests = flow?.requests.filter(item => item.branch_document_id === selected?.document_id) || [];
  const selectedRequests = (flow?.requests || []).filter(item => item.source_version_id === selected?.id || item.merged_version_id === selected?.id);
  const lastRequest = [...branchRequests].sort((a,b) => b.created_at - a.created_at)[0];
  function selectVersion(id: string | null) {
    if(disabled)return;
    setSelectedId(id);setReview(null);setReviewId(null);setPreview(false);setConfirmed(false);setCreatingFrom(null);
  }
  const versionName = (id: string) => {
    const version = flow?.versions.find(item => item.id === id);
    return version ? `${dateLabel(version.created_at)} 저장본` : "이전 저장본";
  };
  async function compareSaved(document: Document, leftId: string, rightId: string) {
    if (locked.current || disabled) return;
    locked.current = true; setBusy(true); setError("");
    try {
      const versions = await api<Version[]>(`/documents/${document.id}/versions`);
      const left=versions.find(version=>version.id===leftId),right=versions.find(version=>version.id===rightId);
      if(!left || !right) throw new Error("비교할 저장본을 찾지 못했습니다. 문서 이력을 다시 열어 주세요.");
      if(alive.current) {setPreview(false);setComparison({document,left,right});}
    } catch(cause){if(alive.current)setError(errorMessage(cause));}
    finally{locked.current=false;if(alive.current)setBusy(false);}
  }
  if (comparison) return <VersionComparison document={comparison.document} left={comparison.left} right={comparison.right} onClose={()=>setComparison(null)} />;
  return <div className="branch-overlay"><section ref={dialog} role="dialog" aria-modal="true" aria-labelledby="branch-title" className="branch-dialog">
    <HistoryKeyboard dialog={dialog} onClose={close} />
    <header><div><h2 id="branch-title">문서 이력</h2><p>{flow?.source_document.name || doc.name}</p></div><button onClick={close} disabled={busy}>닫기</button></header>
    <p className="branch-intro">기록을 선택하면 오른쪽에서 그때의 문서를 볼 수 있습니다. 최근 기록은 위에 있습니다.</p>
    {error && <p role="alert" className="branch-error">{error}</p>}
    {notice && <p role="status" className="branch-notice">{notice}</p>}
    {retry && <div className="branch-error"><p>처리 결과가 불확실합니다. 같은 요청으로 확인합니다.</p><button disabled={busy} onClick={() => pending.current && void mutate(pending.current)}>같은 요청으로 결과 확인</button></div>}
    {restoreRetry && <div className="branch-error"><p>복원 결과를 아직 확인하지 못했습니다. 선택과 다른 작업을 보류합니다.</p><button disabled={busy} onClick={() => pendingRestore.current && void restoreSelected(pendingRestore.current.document, pendingRestore.current.version)}>같은 복원 요청으로 확인</button></div>}
    {loading && <p role="status">문서 이력을 불러오는 중…</p>}
    {!loading && !flow && <button disabled={busy} onClick={() => { setLoading(true); setError(""); void reload().catch(cause => setError(errorMessage(cause))).finally(() => setLoading(false)); }}>다시 불러오기</button>}
    {flow && <>
      <div className="history-workspace">
        <section className="history-tree-pane" aria-label="문서 분기와 통합 흐름">
          <div className="history-tree-legend"><strong>최근 ↑</strong><span className="save-key">저장</span><span className="branch-key">분기</span><span className="merge-key">통합</span><span className="restore-key">복원</span></div>
          <HistoryTree graph={graph} source={flow.source_document} branches={flow.branches} selected={selected?.id} disabled={disabled} onSelect={selectVersion} />
          {graph.truncated && <p className="branch-muted">최근 이력을 표시합니다. 생략된 구간은 연결을 추정하지 않고 별도로 표시합니다.</p>}
        </section>
        <aside className="history-inspector" aria-label="선택한 저장본">
          {!selected || !selectedDocument ? <p>이력에서 저장본을 선택하세요.</p> : <>
            <header><div><h3>{selectedBranch ? selectedDocument.name : "기준 문서"}</h3><span>{selected.current ? "현재 사용 중인 버전" : selected.document === null ? "검토를 요청한 버전" : "이전 버전"} · {selected.author?.user_id || "수정자 기록 없음"} · {selected.created_at ? dateLabel(selected.created_at) : "기록 시각 미상"}</span></div></header>
            <div className="history-node-actions">
              {selected.current && selected.can_edit && <button className="history-primary" disabled={disabled} onClick={() => onOpen(selectedDocument)}>편집</button>}
              {onCoedit && selected.current && selected.can_edit && <button disabled={disabled} onClick={() => onCoedit(selectedDocument)}>공동편집</button>}
              {onRestore && !selected.current && selected.can_restore && <button disabled={disabled} onClick={() => void restoreSelected(selectedDocument,selected.id)}>이 저장본 복원</button>}
              {selected.can_read && selected.document !== null && selected.base_version && <button disabled={disabled} onClick={()=>void compareSaved(selectedDocument,selected.base_version!,selected.id)}>변경 내용 보기</button>}
              {selected.document_id === flow.source_document.id && selected.kind === "version" && selected.can_read && <button disabled={disabled} aria-expanded={creatingFrom===selected.id} onClick={()=>setCreatingFrom(value=>value===selected.id?null:selected.id)}>작업본 만들기</button>}
            </div>
            {comparisonBase && selected.can_read && selected.document !== null && <div className="history-comparison-selection"><span>비교 기준: {comparisonBase.label}</span><button disabled={disabled || comparisonBase.documentId!==selected.document_id || comparisonBase.versionId===selected.id} onClick={()=>void compareSaved(selectedDocument,comparisonBase.versionId,selected.id)}>선택한 두 저장본 비교</button><button disabled={disabled} onClick={()=>setComparisonBase(null)}>기준 해제</button>{comparisonBase.documentId!==selected.document_id ? <small>같은 문서의 다른 버전을 선택하세요.</small> : comparisonBase.versionId===selected.id && <small>왼쪽에서 비교할 다른 버전을 선택하세요.</small>}</div>}
            {creatingFrom === selected.id && selected.document_id === flow.source_document.id && selected.kind === "version" && selected.can_read && <form className="branch-create" onSubmit={event => {event.preventDefault(); if(disabled || !name.trim()) return; void mutate({path:`/documents/${flow.source_document.id}/branches`,body:{document_id:crypto.randomUUID(),source_version:selected.id,request_id:crypto.randomUUID(),name:name.trim()},action:"create",label:"작업본 만들기"});}}>
              <p>선택한 버전에서 별도로 수정할 작업본을 만듭니다.</p><label>새 작업본 이름<input value={name} maxLength={200} disabled={disabled} onChange={event => setName(event.target.value)} placeholder="예: 대응방안 보완" /></label><button disabled={disabled || !name.trim()}>이 저장본에서 작업본 만들기</button><button type="button" disabled={disabled} onClick={()=>setCreatingFrom(null)}>취소</button>
            </form>}
            {selectedBranch && <>
              {selectedBranch.can_submit !== false && selectedBranch.can_edit && selected.current && !(lastRequest?.status === "pending" && lastRequest.target_version_id === flow.source_document.current_version && lastRequest.source_version_id === selected.id) && <button className="history-primary" disabled={disabled} onClick={() => void mutate({path:`/documents/${selectedBranch.document_id}/integration-requests`,body:{request_id:crypto.randomUUID(),source_version:selected.id,target_version:flow.source_document.current_version},action:"submit",label:"통합 요청"})}>통합 요청</button>}
            </>}
            {selectedRequests.length > 0 && <div className="history-integration-list">{selectedRequests.map(item => <article key={item.id} className={`branch-request ${item.status}`}><strong>{statusName[item.status]}</strong><span>{item.status==="merged" ? `${item.reviewed_by} 승인` : `${item.author_id} 요청`} · {dateLabel(item.created_at)}</span>{item.status === "pending" && <button disabled={disabled} onClick={() => void showReview(item)}>{item.can_review ? "수정안 검토" : "제출 내용 확인"}</button>}{item.status === "merged" && item.merged_version_id!==selected.id && <button disabled={disabled} onClick={() => selectVersion(item.merged_version_id)}>통합된 문서 보기</button>}</article>)}</div>}
            <details key={`more:${selected.id}`} className="history-more"><summary>더보기</summary><div className="history-more-content">
              {selected.restore_source && <p className="history-restore-source">{dateLabel(selected.restore_source.created_at)} 저장본에서 복원 <button disabled={disabled || !graph.nodes.some(node=>node.id===selected.restore_source!.version_id && node.kind==="version")} onClick={()=>selectVersion(selected.restore_source!.version_id)}>복원 원본 보기</button></p>}
              {selected.can_read && selected.document !== null && <><div className="history-saved-actions"><SavedDownload document={selectedDocument} versionId={selected.id} icon={false} label="이 버전 다운로드" /><button disabled={disabled} onClick={()=>setComparisonBase({documentId:selected.document_id,versionId:selected.id,label:selected.created_at?dateLabel(selected.created_at):"선택 저장본"})}>비교 기준으로 지정</button></div><VersionStructure documentId={selected.document_id} versionId={selected.id} /></>}
              {selected.can_read && selected.document === null && selected.file_url && <a className="button" href={publicPath(`api${selected.file_url}`)} download>이 버전 다운로드</a>}
              {selectedBranch?.can_manage_participants && <BranchParticipants branch={selectedBranch} disabled={disabled} onUpdated={() => {onUpdated();void reload().catch(cause=>setError(errorMessage(cause)));}} />}
              {selected.cause && <p className="branch-muted">{causeLabel(selected.cause)}{selected.size != null && ` · ${Math.max(1,Math.round(selected.size/1024)).toLocaleString()} KB`}</p>}
            </div></details>
            {!preview && (selected.can_read && selected.kind === "version" ? <BranchPreview key={`preview:${selected.id}`} title="선택한 저장본 미리보기" url={selected.file_url ? publicPath(`api${selected.file_url}`) : fileUrl(selectedDocument,selected.id)} /> : <p className="branch-muted">이 저장본은 내용 열람 권한이 없습니다.</p>)}
          </>}
        </aside>
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
          {review.request.can_review ? <div className="branch-decision"><label><input type="checkbox" checked={confirmed} disabled={disabled} onChange={event => setConfirmed(event.target.checked)} />수정안을 확인했습니다.</label>
            <button disabled={disabled || !confirmed} onClick={() => void mutate({path: `/integration-requests/${review.request.id}/accept`, body: {merged_sha256: review.merged_sha256}, action: "accept", label: "승인·통합"})}>승인·통합</button>
            <button disabled={disabled} onClick={() => void mutate({path: `/integration-requests/${review.request.id}/reject`, body: {}, action: "reject", label: "반려"})}>반려</button></div>
            : <p className="branch-muted">수정안 작성·편집 참여자는 직접 승인할 수 없습니다. 작업에 참여하지 않은 기준 문서 관리자에게 검토를 요청하세요.</p>}
        </>}
      </section>}
    </>}
  </section></div>;
}

function HistoryKeyboard({dialog,onClose}:{dialog:React.RefObject<HTMLElement|null>;onClose:()=>void}) { useComparisonDialog(dialog,onClose);return null; }
function VersionStructure({documentId,versionId}:{documentId:string;versionId:string}) {
  const [open,setOpen]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState("");
  const [attempt,setAttempt]=useState(0);
  const [changes,setChanges]=useState<{recorded:boolean;version:Version;entries:{path:string;before_sha256:string|null;after_sha256:string|null}[]}|null>(null);
  useEffect(()=>{
    if(!open)return;const controller=new AbortController();setLoading(true);setError("");setChanges(null);
    void api<NonNullable<typeof changes>>(`/documents/${documentId}/versions/${versionId}/changes`,{signal:controller.signal})
      .then(value=>{if(value?.version?.id!==versionId || !Array.isArray(value.entries))throw new Error("선택한 버전의 정보를 확인하지 못했습니다. 다시 불러와 주세요.");if(!controller.signal.aborted)setChanges(value);})
      .catch(cause=>{if(!controller.signal.aborted)setError(errorMessage(cause));})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();
  },[open,documentId,versionId,attempt]);
  return <details className="history-structure-detail" onToggle={event=>{event.stopPropagation();setOpen(event.currentTarget.open);}}><summary>파일 진단 정보</summary>
    <p>문제 확인을 위한 파일 구성 기록입니다. 글·표·서식은 ‘변경 내용 보기’에서 비교하세요.</p>
    {loading&&<p role="status">파일 정보를 불러오는 중…</p>}{error&&<><p role="alert">{error}</p><button onClick={()=>setAttempt(value=>value+1)}>다시 불러오기</button></>}
    {changes?.version.id===versionId&&<>
      {!changes.recorded?<p>이 버전에는 파일 변경 기록이 없습니다. 최초 저장 또는 기록 도입 전 버전입니다.</p>:!changes.entries.length?<p>직전 버전과 파일 구성의 차이가 없습니다.</p>:<><p>직전 버전에서 변경된 파일 {changes.entries.length}개</p><ul>{changes.entries.map(entry=><li key={entry.path}><code>{entry.path}</code> · {!entry.before_sha256?"추가":!entry.after_sha256?"삭제":"변경"}</li>)}</ul></>}
      </>}
  </details>;
}

function HistoryTree({graph, source, branches, selected, disabled, onSelect}: {graph:Graph;source:Document;branches:Branch[];selected?:string;disabled:boolean;onSelect:(id:string)=>void}) {
  const lanes = [source.id,...branches.map(branch => branch.document_id)];
  // New children precede their actual parents. Private anchors have no timestamp;
  // sorting them as epoch zero would make fork lines grow backwards.
  const remaining = new Map(graph.nodes.map(node=>[node.id,node]));
  const children = new Map(graph.nodes.map(node=>[node.id,new Set<string>()]));
  const parents = new Map(graph.nodes.map(node=>[node.id,new Set<string>()]));
  for(const edge of graph.edges) if(remaining.has(edge.from)&&remaining.has(edge.to)&&edge.from!==edge.to){children.get(edge.from)!.add(edge.to);parents.get(edge.to)!.add(edge.from);}
  const newest = (a:GraphNode,b:GraphNode)=>(b.created_at||0)-(a.created_at||0)||a.id.localeCompare(b.id);
  const ready=graph.nodes.filter(node=>!children.get(node.id)!.size).sort(newest),ordered:GraphNode[]=[];
  while(ready.length){const node=ready.shift()!;if(!remaining.delete(node.id))continue;ordered.push(node);for(const parent of parents.get(node.id)!){children.get(parent)!.delete(node.id);if(!children.get(parent)!.size&&remaining.has(parent))ready.push(remaining.get(parent)!);}ready.sort(newest);}
  // Defensive fallback keeps every node visible even if malformed graph input cycles.
  ordered.push(...[...remaining.values()].sort(newest));
  const laneWidth = 200, rowHeight = 88, width = Math.max(360,lanes.length*laneWidth), height = Math.max(240,ordered.length*rowHeight+90);
  const positions = new Map(ordered.map((node,index) => [node.id,{x:Math.max(0,lanes.indexOf(node.document_id))*laneWidth+laneWidth/2,y:60+index*rowHeight}]));
  const laneName = (identity:string) => identity === source.id ? "기준 문서" : branches.find(branch => branch.document_id === identity)?.name || "작업본";
  return <div className="history-tree-scroll"><div className="history-tree" style={{width,height}}>
    {lanes.map((lane,index) => <div key={lane} className="history-lane-label" style={{left:index*laneWidth,width:laneWidth}}>{laneName(lane)}</div>)}
    <svg className="history-tree-edges" width={width} height={height} aria-hidden="true">{graph.edges.map((edge,index) => {
      const from=positions.get(edge.from),to=positions.get(edge.to);if(!from || !to) return null;
      const mid=(from.y+to.y)/2;
      return <path key={`${edge.from}-${edge.to}-${index}`} data-edge-kind={edge.kind} className={`edge-${edge.kind}`} d={`M ${from.x} ${from.y} C ${from.x} ${mid}, ${to.x} ${mid}, ${to.x} ${to.y}`} />;
    })}</svg>
    {ordered.map(node => {const position=positions.get(node.id)!; const label=node.kind === "anchor" ? "생략되거나 비공개인 이전 기록" : `${laneName(node.document_id)} · ${node.current ? "최신 저장" : "저장"} · ${node.created_at ? dateLabel(node.created_at) : "시각 미상"}`;
      return node.kind === "anchor" ? <div key={node.id} data-anchor-id={node.id} className="history-anchor" style={{left:position.x-76,top:position.y-15}} title={label}>··· 이전 기록</div> :
      <button key={node.id} type="button" className={`history-save-node ${selected===node.id ? "selected" : ""} ${node.current ? "current" : ""}`} data-version-id={node.id} aria-label={label} aria-pressed={selected===node.id} disabled={disabled} style={{left:position.x-83,top:position.y-24,width:166}} onClick={() => onSelect(node.id)}>
        <span className="history-save-dot"/><strong>{node.current ? "최신 저장" : node.cause === "restore" ? "복원 저장" : graph.edges.some(edge => edge.to===node.id && edge.kind==="merge") ? "통합 저장" : "저장"}</strong>
        <span>{node.created_at ? new Date(node.created_at*1000).toLocaleString("ko-KR",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}) : "시각 미상"}</span>
        <small>{node.author?.user_id || "수정자 미상"}</small>
      </button>;
    })}
  </div></div>;
}

function BranchParticipants({branch, disabled, onUpdated}: {branch:Branch;disabled:boolean;onUpdated:()=>void}) {
  const [rows,setRows]=useState<{user_id:string;role:string}[]>([]),[user,setUser]=useState(""),[error,setError]=useState(""),[busy,setBusy]=useState(false),[opened,setOpened]=useState(false);
  const operation=useRef(false);
  async function refresh() {setRows(await api<{user_id:string;role:string}[]>(`/documents/${branch.document_id}/branch-participants`));}
  async function change(identity:string,method:"PUT"|"DELETE") {
    if(operation.current || disabled)return;operation.current=true;setBusy(true);setError("");
    try {await api(`/documents/${branch.document_id}/branch-participants/${encodeURIComponent(identity)}`,jsonRequest(method,{}));await refresh();setUser("");onUpdated();}
    catch(cause){setError(errorMessage(cause));}finally{operation.current=false;setBusy(false);}
  }
  return <details className="history-participants" onToggle={event=>{const open=event.currentTarget.open;setOpened(open);if(open)void refresh().catch(cause=>setError(errorMessage(cause)));}}><summary>작업본 참여자 관리</summary>
    {opened && <><p className="branch-muted">기준 문서의 편집 권한이 있는 계정만 추가할 수 있습니다. 참여자는 이 작업본을 함께 편집합니다.</p>{error && <p role="alert">{error}</p>}
    <p>소유자: {branch.owner_id}</p><ul>{rows.filter(row=>row.user_id!==branch.owner_id).map(row=><li key={row.user_id}>{row.user_id}<button disabled={busy||disabled} onClick={()=>void change(row.user_id,"DELETE")}>참여 해제</button></li>)}</ul>
    <form onSubmit={event=>{event.preventDefault();if(user.trim())void change(user.trim(),"PUT");}}><label>추가할 계정 ID<input value={user} disabled={busy||disabled} onChange={event=>setUser(event.target.value)} /></label><button disabled={busy||disabled||!user.trim()}>참여자 추가</button></form></>}
  </details>;
}

export function BranchPreview({title, url}: {title: string; url: string}) {
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
