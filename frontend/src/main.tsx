import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { History } from "./version-history";
import { EditorAdapter, type Snapshot, type ExportSnapshot } from "./editor";
import {
  api,
  ApiError,
  upload,
  fileUrl,
  downloadBytes,
  deltaBody,
  jsonRequest,
  type Document,
  type Version,
} from "./api";
import "./style.css";
import "./viewport.css";
import { AccessPanel, CollaborationPanel } from "./collaboration";
import { Reports } from "./reports";
import { TemplateMarket } from "./template-market";
import { StandardDesigner, type VisualSelection } from "./standard-designer";
import { BlockInsert, BlockFenceError, blockJson, type BlockCapture, type PreparedBlock } from "./block-insert";
import { TocPanel, type TocCapture, type PreparedToc } from "./toc";
import "./editor-workspace.css";
import { WorkspaceNav, WorkspaceIcon, type WorkspaceSection } from "./workspace-nav";
import "./workspace-shell.css";
import { assistantDocumentContext } from "./document-assistant-context";
import type { AssistantTurn } from "./document-assistant";
import { SavedDownload } from "./saved-download";

function App({
  account,
}: {
  account: { user_id: string; authentication_required: boolean };
}) {
  const [docs, setDocs] = useState<Document[]>([]),
    [query, setQuery] = useState(""),
    [sort, setSort] = useState("newest"),
    [expandedDocument, setExpandedDocument] = useState<string | null>(null),
    [copying, setCopying] = useState(false),
    [copyNotice, setCopyNotice] = useState<Document | null>(null),
    [copyPending, setCopyPending] = useState<{ source: Document; body: { document_id: string; source_version: string; request_id: string; name: string } } | null>(null),
    [uploading, setUploading] = useState(false),
    [uploadPending, setUploadPending] = useState(""),
    [uploadNotice, setUploadNotice] = useState(""),
    [dragOver, setDragOver] = useState(false),
    [active, setActive] = useState<Document | null>(null),
    [creating, setCreating] = useState(false),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [history, setHistory] = useState<Document | null>(null),
    [sharing, setSharing] = useState<Document | null>(null),
    [templates, setTemplates] = useState(false),
    [standardDesigner, setStandardDesigner] = useState(false),
    [reportStandard, setReportStandard] = useState<VisualSelection | undefined>(),
    [registerTemplate, setRegisterTemplate] = useState<Document | undefined>(),
    [reports, setReports] = useState(false);
  const listGeneration = useRef(0);
  const uploadBusy = useRef(false), dragDepth = useRef(0);
  const frozenUpload = useRef<{ bytes: Uint8Array; name: string; id: string; key: string } | null>(null);
  const inLibrary = !active && !creating && !standardDesigner && !reports && !templates;
  useEffect(() => {
    if (!inLibrary) return;
    const clearDrag = () => { dragDepth.current = 0; setDragOver(false); };
    const filesDragged = (event: DragEvent) => Array.from(event.dataTransfer?.types || []).includes("Files");
    const preventFileNavigation = (event: DragEvent) => {
      if (!filesDragged(event)) return;
      event.preventDefault();
      if (event.type === "drop") clearDrag();
      else if (!(event.target instanceof Element) || !event.target.closest(".library-drop-target") || history || sharing)
        event.dataTransfer!.dropEffect = "none";
    };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") clearDrag(); };
    window.addEventListener("dragover", preventFileNavigation);
    window.addEventListener("drop", preventFileNavigation);
    window.addEventListener("dragend", clearDrag);
    window.addEventListener("blur", clearDrag);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("dragover", preventFileNavigation); window.removeEventListener("drop", preventFileNavigation);
      window.removeEventListener("dragend", clearDrag); window.removeEventListener("blur", clearDrag); window.removeEventListener("keydown", escape);
      dragDepth.current = 0;
    };
  }, [inLibrary, history, sharing]);
  const sortedDocs = [...docs].sort((a, b) => sort === "name" ? a.name.localeCompare(b.name, "ko", { numeric: true }) || a.id.localeCompare(b.id)
    : sort === "oldest" ? a.created_at - b.created_at || a.id.localeCompare(b.id) : b.created_at - a.created_at || a.id.localeCompare(b.id));
  const refresh = () => {
    const generation = ++listGeneration.current;
    setLoading(true);
    api<Document[]>(`/documents?q=${encodeURIComponent(query)}`)
      .then(rows => { if (generation === listGeneration.current) setDocs(rows); })
      .catch((e) => { if (generation === listGeneration.current) setError(e.message); })
      .finally(() => { if (generation === listGeneration.current) setLoading(false); });
  };
  useEffect(refresh, [query]);
  function navigate(section: WorkspaceSection) {
    setDragOver(false); dragDepth.current = 0;
    setActive(null); setCreating(false); setHistory(null); setSharing(null);
    setRegisterTemplate(undefined); setReportStandard(undefined);
    setTemplates(section === "templates"); setReports(section === "reports");
    setStandardDesigner(section === "standards");
    if (section === "documents") refresh();
  }
  async function copySaved(source: Document) {
    if (copying) return;
    let frozen = copyPending;
    if (!frozen) {
      const name = prompt("저장본을 복제할 새 문서 이름", source.name.replace(/\.hwpx$/i, "") + " 사본.hwpx");
      if (!name) return;
      frozen = { source, body: { document_id: crypto.randomUUID(), source_version: source.current_version, request_id: crypto.randomUUID(), name } };
      setCopyPending(frozen);
    }
    setCopying(true); setError(""); setCopyNotice(null);
    try {
      const result = await api<Document>(`/documents/${frozen.source.id}/copies`, jsonRequest("POST", frozen.body));
      setCopyPending(null); setCopyNotice(result); refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) setCopyPending(null);
      setError(`${String(e)} · 문서 목록과 복제 결과를 확인하세요.`);
    } finally { setCopying(false); }
  }
  async function incoming(file?: File) {
    if (uploadBusy.current) { setError("파일을 업로드하고 있습니다. 완료된 뒤 다음 파일을 가져오세요."); return; }
    if (file && frozenUpload.current) { setError("먼저 보관한 파일의 업로드 결과를 재확인하세요. 새 파일은 업로드하지 않았습니다."); return; }
    if (!file && !frozenUpload.current) return;
    uploadBusy.current = true; setUploading(true); setUploadNotice("");
    try {
      setError("");
      if (file) {
        if (!file.name.toLowerCase().endsWith(".hwpx")) throw new Error("HWPX 파일만 업로드할 수 있습니다. .hwp 파일은 HWPX로 저장한 뒤 가져오세요.");
        if (file.size > 30 * 1024 * 1024) throw new Error("30MB 이하의 HWPX 파일을 가져오세요.");
        frozenUpload.current = { bytes: new Uint8Array(await file.arrayBuffer()), name: file.name,
          id: crypto.randomUUID(), key: crypto.randomUUID() };
      }
      const frozen = frozenUpload.current!;
      setUploadPending(frozen.name);
      await upload(frozen.bytes, frozen.name, frozen.id, frozen.key);
      frozenUpload.current = null; setUploadPending("");
      setUploadNotice(`${frozen.name} 파일을 새 문서로 저장했습니다.`);
      refresh();
    } catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) { frozenUpload.current = null; setUploadPending(""); }
      setError(`${String(e)}${frozenUpload.current ? " · 파일과 요청을 보관했습니다. 같은 업로드 결과를 재확인하세요." : ""}`);
    } finally { uploadBusy.current = false; setUploading(false); }
  }
  if (standardDesigner) return <StandardDesigner onNavigate={navigate} onClose={() => { setStandardDesigner(false); refresh(); }}
    onReport={standard => { setReportStandard(standard); setStandardDesigner(false); setReports(true); }} />;
  if (reports) return <Reports onNavigate={navigate} initialVisualStandard={reportStandard} onClose={() => { setReports(false); setReportStandard(undefined); refresh(); }} onOpen={doc => { setReports(false); setReportStandard(undefined); setActive(doc); }} />;
  if (templates) return <TemplateMarket onNavigate={navigate} registerDocument={registerTemplate}
    onClose={() => { setTemplates(false); setRegisterTemplate(undefined); refresh(); }}
    onApply={doc => { setTemplates(false); setRegisterTemplate(undefined); setActive(doc); }}
    onDesignStandard={() => { setTemplates(false); setRegisterTemplate(undefined); setStandardDesigner(true); }}
    onReport={() => { setTemplates(false); setRegisterTemplate(undefined); setReports(true); }} />;
  if (active?.role === "reader")
    return (
      <div className="workspace-shell"><WorkspaceNav active="documents" onNavigate={navigate} onBack={() => { setActive(null); refresh(); }} />
      <main className="drive drive-workspace">
        <h1>{active.name}</h1>
        <p>열람 권한 · 저장본과 문서 공유 대화를 확인할 수 있습니다.</p>
        <SavedDownload document={active} label="저장본 다운로드" />
        <CollaborationPanel document={active} />
      </main></div>
    );
  if (active || creating)
    return (
      <Editor
        initial={active}
        onClose={() => {
          setActive(null);
          setCreating(false);
          refresh();
        }}
        onHistory={setHistory}
        history={history}
        closeHistory={() => setHistory(null)}
      />
    );
  return (
    <div className="workspace-shell library-shell">
      <WorkspaceNav active="documents" onNavigate={navigate} disabled={copying || uploading} footer={<><strong>{account.authentication_required ? account.user_id : "개인 작업 공간"}</strong><details className="library-account-note"><summary>이용 안내</summary><p>{account.authentication_required ? "문서별 열람·편집·소유자 권한과 대화 공개 범위가 적용됩니다." : "현재 이 컴퓨터의 개인 문서함입니다. 사용자 간 공유는 서버 인증 설정 후 사용할 수 있습니다."}</p><p>저장본은 이전 버전을 보존합니다. 한컴 호환성은 실제 파일로 확인하세요.</p></details>{account.authentication_required && <button onClick={async () => { await api("/auth/logout", { method: "POST" }); location.reload(); }}>로그아웃</button>}</>}>
        <button className="primary workspace-nav-action" aria-label="빈 문서 만들기" disabled={uploading} onClick={() => setCreating(true)}><WorkspaceIcon name="plus"/><span>빈 문서 만들기</span></button>
        <label className="button workspace-nav-action" title="HWPX 업로드"><WorkspaceIcon name="upload"/><span>HWPX 업로드</span><input aria-label="HWPX 업로드" type="file" accept=".hwpx" disabled={uploading || !!uploadPending} onChange={event => { if (event.target.files?.[0]) void incoming(event.target.files[0]); event.target.value = ""; }} /></label>
        <p className="workspace-nav-note">문서를 만들거나 기존 HWPX를 가져와 작업하세요.</p>
      </WorkspaceNav>
    <main className={`drive drive-workspace library-drop-target${dragOver ? " is-file-dragging" : ""}`}
      onDragEnter={event => {
        if (!Array.from(event.dataTransfer.types).includes("Files") || history || sharing) return;
        event.preventDefault(); dragDepth.current++; setDragOver(true);
      }}
      onDragOver={event => {
        if (!Array.from(event.dataTransfer.types).includes("Files")) return;
        event.preventDefault(); event.dataTransfer.dropEffect = history || sharing || uploading || uploadPending ? "none" : "copy";
      }}
      onDragLeave={() => { dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragOver(false); }}
      onDrop={event => {
        if (!Array.from(event.dataTransfer.types).includes("Files")) return;
        event.preventDefault(); event.stopPropagation(); dragDepth.current = 0; setDragOver(false);
        if (history || sharing) return;
        const files = Array.from(event.dataTransfer.files);
        if (files.length !== 1) { setError("한 번에 HWPX 파일 한 개씩 가져오세요. 여러 파일이나 폴더는 업로드하지 않았습니다."); return; }
        void incoming(files[0]);
      }}>
      <header className="library-heading">
        <div>
          <h1>HWPX 문서함</h1>
          <p>문서 이름을 눌러 열고 편집하세요.</p>
        </div>
        <span className="library-document-count">{docs.length}개 문서</span>
      </header>
      <div className="library-drop-hint" role="region" aria-label="HWPX 파일 놓기" aria-busy={uploading}>
        <WorkspaceIcon name="upload" size={22}/><div><strong>{uploading ? "파일을 업로드하고 있습니다…" : dragOver ? "여기에 놓아 새 문서로 가져오세요" : "HWPX 파일을 이 문서함에 끌어 놓으세요"}</strong>
          <p>파일 한 개씩 · 최대 30MB · 기존 문서는 그대로 유지됩니다.</p></div>
      </div>
      <div className="library-toolbar">
      <input
        className="search"
        aria-label="파일명 검색"
        placeholder="파일명 검색"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <label className="document-sort">문서 정렬 <select aria-label="문서 정렬" value={sort} onChange={e => setSort(e.target.value)}>
        <option value="newest">최근 등록순</option><option value="oldest">오래된 등록순</option><option value="name">이름순</option>
      </select></label>
      <button aria-label="문서 목록 새로고침" title="문서 목록 새로고침" disabled={loading} onClick={refresh}><WorkspaceIcon name="refresh" size={18}/></button>
      </div>
      {copyPending && <div className="notice">{copyPending.source.name}의 저장본 복제를 확인 중입니다.
        <button disabled={copying} onClick={() => void copySaved(copyPending.source)}>{copying ? "복제 중…" : "같은 사본 생성 결과 재확인"}</button>
      </div>}
      {copyNotice && <div className="notice">{copyNotice.name} 사본을 저장했습니다. 원본의 대화·권한·버전 이력은 복사하지 않습니다.
        <button onClick={() => setActive(copyNotice)}>사본 열기</button>
      </div>}
      {uploadPending && <div className="notice" role="status">{uploadPending}{uploading ? " 파일을 저장하는 중입니다." : " 파일의 저장 결과를 확인해야 합니다."}
        {!uploading && <button onClick={() => void incoming()}>업로드 결과 재확인</button>}
      </div>}
      {uploadNotice && <p className="notice" role="status">{uploadNotice}</p>}
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {loading ? (
        <p>문서 목록을 불러오는 중…</p>
      ) : docs.length === 0 ? (
        <div className="empty">
          {query ? "검색 결과가 없습니다. 다른 이름으로 검색해 보세요." : "아직 저장한 문서가 없습니다. 보고서를 작성하거나 HWPX 파일을 가져오세요."}
        </div>
      ) : (
        <div className="library-table-wrap"><table className="library-table">
          <thead>
            <tr>
              <th>문서명</th>
              <th>등록일</th>
              <th>작업</th>
            </tr>
          </thead>
          <tbody>
            {sortedDocs.map((d) => (
              <React.Fragment key={d.id}><tr>
                <td className="library-document-name">
                  <button className="link" onClick={() => setActive(d)}>
                    <WorkspaceIcon name="documents"/><span>{d.name}</span>
                  </button>
                </td>
                <td>{new Date(d.created_at * 1000).toLocaleString("ko-KR")}</td>
                <td className="library-document-actions">
                  <SavedDownload document={d} />
                  <button aria-label="버전 기록" title="버전 기록" onClick={() => setHistory(d)}><WorkspaceIcon name="history" size={17}/><span>버전 기록</span></button>
                  <button id={`document-more-${d.id}`} aria-label={`${d.name} 더보기`} title="더보기" aria-expanded={expandedDocument === d.id} aria-controls={`document-actions-${d.id}`}
                    onClick={() => setExpandedDocument(expandedDocument === d.id ? null : d.id)}><WorkspaceIcon name="more" size={17}/><span>더보기</span></button>
                </td>
              </tr>
              {expandedDocument === d.id && <tr className="library-expanded-actions"><td colSpan={3}>
                <div className="library-secondary-actions" id={`document-actions-${d.id}`} role="group" aria-label={`${d.name} 추가 작업`} onKeyDown={event => {
                  if (event.key === "Escape") { setExpandedDocument(null); document.getElementById(`document-more-${d.id}`)?.focus(); }
                }}>
                  <button aria-label="이름 변경" title="이름 변경"
                    disabled={d.role === "reader"}
                    onClick={async () => {
                      const name = prompt("새 문서명", d.name);
                      if (name)
                        try {
                          await api(`/documents/${d.id}`, {
                            method: "PATCH",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ name }),
                          });
                          refresh();
                        } catch (e) {
                          setError(String(e));
                        }
                    }}
                  >
                    <WorkspaceIcon name="rename" size={17}/><span>이름 변경</span>
                  </button>
                  <button aria-label="저장본 복제" title="저장본 복제" disabled={copying || !!copyPending} onClick={() => void copySaved(d)}><WorkspaceIcon name="copy" size={17}/><span>저장본 복제</span></button>
                  <label className="button" title="새 버전 업로드">
                    <WorkspaceIcon name="upload" size={17}/><span>새 버전 업로드</span>
                    <input
                      type="file"
                      disabled={d.role === "reader"}
                      accept=".hwpx"
                      aria-label={`${d.name} 새 버전 업로드`}
                      onChange={async (e) => {
                        const f = e.target.files?.[0];
                        e.target.value = "";
                        if (!f) return;
                        let token = "";
                        try {
                          if (!f.name.toLowerCase().endsWith(".hwpx"))
                            throw new Error("HWPX 파일만 허용됩니다.");
                          const lease = await api<{
                            token: string;
                            document: Document;
                          }>(`/documents/${d.id}/session`, { method: "POST" });
                          token = lease.token;
                          await api(
                            `/documents/${d.id}/versions?base_version=${d.current_version}&cause=reupload`,
                            {
                              method: "POST",
                              headers: {
                                "X-Edit-Session": token,
                                "Idempotency-Key": crypto.randomUUID(),
                              },
                              body: f,
                            },
                          );
                          refresh();
                        } catch (e) {
                          setError(String(e));
                        } finally {
                          if (token)
                            await api(`/documents/${d.id}/session`, {
                              method: "DELETE",
                              headers: { "X-Edit-Session": token },
                            }).catch(() => {});
                        }
                      }}
                    />
                  </label>
                  {account.authentication_required && d.role === "owner" && (
                    <button aria-label="공유 권한" title="공유 권한" onClick={() => setSharing(d)}><WorkspaceIcon name="share" size={17}/><span>공유 권한</span></button>
                  )}
                  {d.role !== "reader" && (
                    <button aria-label="양식 등록" title="양식 등록"
                      onClick={() => { setRegisterTemplate(d); setTemplates(true); }}
                    >
                      <WorkspaceIcon name="templates" size={17}/><span>양식으로 등록</span>
                    </button>
                  )}
                </div>
              </td></tr>}
              </React.Fragment>
            ))}
          </tbody>
        </table></div>
      )}
      {sharing && (
        <AccessPanel document={sharing} onClose={() => setSharing(null)} />
      )}
      {history && (
        <History
          document={history}
          onClose={() => setHistory(null)}
          onRestore={async (v) => {
            let token = "";
            try {
              const s = await api<{ token: string }>(
                `/documents/${history.id}/session`,
                { method: "POST" },
              );
              token = s.token;
              await api(
                `/documents/${history.id}/versions/${v.id}/restore?base_version=${history.current_version}`,
                {
                  method: "POST",
                  headers: {
                    "X-Edit-Session": token,
                    "Idempotency-Key": crypto.randomUUID(),
                  },
                },
              );
              setHistory(null);
              refresh();
            } catch (e) {
              setError(String(e));
            } finally {
              if (token)
                await api(`/documents/${history.id}/session`, {
                  method: "DELETE",
                  headers: { "X-Edit-Session": token },
                }).catch(() => {});
            }
          }}
        />
      )}
    </main></div>
  );
}


function Editor({
  initial,
  onClose,
  onHistory,
  history,
  closeHistory,
}: {
  initial: Document | null;
  onClose: () => void;
  onHistory: (d: Document) => void;
  history: Document | null;
  closeHistory: () => void;
}) {
  const mount = useRef<HTMLDivElement>(null),
    adapter = useRef<EditorAdapter | null>(null),
    doc = useRef<Document | null>(initial),
    token = useRef(""),
    savedRevision = useRef(-1),
    busy = useRef(false),
    epoch = useRef<number | null>(null),
    baseBytes = useRef<Uint8Array | null>(null),
    pending = useRef<{
      snapshot: ExportSnapshot;
      key: string;
      base: string | null;
      documentId: string;
      delta?: string;
      applicationId?: string;
    } | null>(null);
  const reviewHold = useRef(false);
  const assistantHold = useRef(false);
  const onAssistantActive = React.useCallback((value: boolean) => { assistantHold.current = value; }, []);
  const [discardTarget, setDiscardTarget] = useState<"panel" | "document" | null>(null);
  const rewriteRequest = useRef<{thread: string; instruction: string; id: string; snapshot: Snapshot;
    base: string; previousHold: boolean; contextRevision?: number} | null>(null);
  const contextDraftDirty = useRef(false);
  const onContextDirty = React.useCallback((value: boolean) => { contextDraftDirty.current = value; }, []);
  const applied = useRef<{id: string; snapshot: ExportSnapshot; receipt: unknown} | null>(null);
  const [holdingReview, setHoldingReview] = useState(false);
  const [name] = useState(initial?.name || "새 문서.hwpx"),
    [status, setStatus] = useState("편집기 로딩 중"),
    [error, setError] = useState(""),
    [ready, setReady] = useState(false),
    [dirty, setDirty] = useState(false),
    [panel, setPanel] = useState(false),
    [conversationPanel, setConversationPanel] = useState(false),
    [conversationMounted, setConversationMounted] = useState(false),
    [aiBusy, setAiBusy] = useState(false),
    [aiError, setAiError] = useState(""),
    [aiConnection, setAiConnection] = useState<{ configured_model: string; models: string[]; model_available: boolean } | null>(null),
    [aiConnectionChecking, setAiConnectionChecking] = useState(false),
    [proposal, setProposal] = useState<{
      snapshot: Snapshot;
      replacement: string;
      reason: string;
      documentId: string;
      base: string;
    } | null>(null),
    [warnings, setWarnings] = useState("");
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  const blockHosts = useRef<(HTMLDivElement | null)[]>([null, null, null]), blockHold = useRef<BlockCapture | TocCapture | null>(null),
    adapterOff = useRef<() => void>(() => {}), editorAlive = useRef(true),
    blockUndo = useRef<{ previous: EditorAdapter; current: EditorAdapter; snapshot: ExportSnapshot } | null>(null);
  const [blockPanel, setBlockPanel] = useState(false), [blockHolding, setBlockHolding] = useState(false),
    [blockUndoAvailable, setBlockUndoAvailable] = useState(false), [activeBlockHost, setActiveBlockHost] = useState(0);
  const [tocPanel, setTocPanel] = useState(false), [tocStatus, setTocStatus] = useState<"unknown" | "none" | "recorded" | "current" | "stale" | "damaged">("unknown");
  const tocKnown = useRef(false);
  const fileMenu = useRef<HTMLDetailsElement>(null), helpMenu = useRef<HTMLDetailsElement>(null), documentMenu = useRef<HTMLDetailsElement>(null), toolsMenu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const menus = [fileMenu, helpMenu, documentMenu, toolsMenu];
    let frameFocusTimer: ReturnType<typeof setTimeout> | undefined;
    const dismiss = (event: PointerEvent) => menus.forEach(menu => {
      if (menu.current && !menu.current.contains(event.target as Node)) menu.current.open = false;
    });
    // Pointer events inside the editor iframe do not reach the outer document.
    const frameFocus = () => {
      clearTimeout(frameFocusTimer);
      frameFocusTimer = setTimeout(() => {
        if (document.activeElement instanceof HTMLIFrameElement)
          menus.forEach(menu => { if (menu.current) menu.current.open = false; });
      }, 0);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      menus.forEach(menu => {
        if (!menu.current?.open) return;
        menu.current.open = false;
        menu.current.querySelector("summary")?.focus();
      });
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    window.addEventListener("blur", frameFocus);
    return () => {
      clearTimeout(frameFocusTimer);
      document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape);
      window.removeEventListener("blur", frameFocus);
    };
  }, []);
  async function checkReviewConnection() {
    setAiConnectionChecking(true); setAiConnection(null); setAiError("");
    try { setAiConnection(await api("/ai/models")); }
    catch { setAiError("별도 AI에 연결할 수 없습니다. 연결 설정을 확인하거나 문서 AI 대화를 이용하세요."); }
    finally { setAiConnectionChecking(false); }
  }
  function showPanel(next: "block" | "toc" | "review" | "conversation" | null) {
    // A prepared preview belongs to its panel until it is explicitly applied or cancelled.
    if (blockHold.current && next !== null && ((blockPanel && next !== "block") || (tocPanel && next !== "toc"))) {
      setError("준비한 블록·목차를 반영하거나 해당 패널에서 취소한 뒤 다른 도구를 여세요.");
      return;
    }
    if (conversationPanel && next === null && assistantHold.current) { setError("AI 응답을 확인하거나 수신을 중단한 뒤 대화를 닫으세요."); return; }
    if (conversationPanel && next === null && contextDraftDirty.current) { setDiscardTarget("panel"); return; }
    // Switching tools hides this panel without losing unsent messages or writing inputs.
    // Its explicit close action retains the existing discard/reopen behavior.
    if (next === "conversation") setConversationMounted(true);
    else if (next === null && conversationPanel) setConversationMounted(false);
    setBlockPanel(next === "block"); setTocPanel(next === "toc");
    setPanel(next === "review"); setConversationPanel(next === "conversation");
  }
  const tocLabel = {
    unknown: "목차 확인 필요", none: "목차 없음", recorded: "목차 기록 일치",
    current: "목차 쪽수 검증 완료", stale: "목차 갱신 필요", damaged: "목차 손상 · 자동 갱신 불가",
  }[tocStatus];
  const tocDescription = {
    unknown: "목차 관리에서 제목과 현재 쪽수를 확인하세요.", none: "목차 관리에서 본문 제목을 선택해 목차를 만들 수 있습니다.",
    recorded: "실제 쪽수는 목차 관리에서 다시 검증할 수 있습니다.", current: "현재 편집 내용에서 목차 제목과 실제 쪽수를 검증했습니다.",
    stale: "본문 수정 후 목차 관리에서 쪽수를 다시 확인하세요.", damaged: "목차 연결을 확인할 수 없습니다. 목차 관리에서 상태를 확인하세요.",
  }[tocStatus];
  function activateBlockHost(index: number) {
    blockHosts.current.forEach((host, candidate) => {
      if (!host) return;
      host.classList.toggle("active", candidate === index);
      host.inert = candidate !== index;
      host.setAttribute("aria-hidden", String(candidate !== index));
    });
    setActiveBlockHost(index);
  }
  function dropBlockUndo() {
    const previous = blockUndo.current; blockUndo.current = null;
    previous?.previous.destroy();
    if (editorAlive.current) setBlockUndoAvailable(false);
  }
  function watchAdapter(instance: EditorAdapter) {
    adapterOff.current();
    adapterOff.current = instance.bridge.subscribe(() => {
      if (!editorAlive.current || adapter.current !== instance) return;
      dropBlockUndo(); dirtyRef.current = true; setDirty(true);
      if (tocKnown.current) setTocStatus("stale");
      if (!busy.current) setStatus("저장되지 않은 변경 있음");
    });
  }
  function captureBlock(): BlockCapture {
    const instance = adapter.current, document = doc.current;
    if (!ready || !instance || !document) throw new BlockFenceError("먼저 문서를 서버에 저장한 뒤 블록을 준비하세요.");
    if (document.role === "reader" || !token.current) throw new BlockFenceError("현재 문서의 편집 세션이 없습니다.");
    if (busy.current || pending.current || applied.current || rewriteRequest.current || reviewHold.current || blockHold.current || aiBusy)
      throw new BlockFenceError("진행 중인 저장·승인·후보 비교를 먼저 마무리하세요.");
    let selection: Snapshot;
    try { selection = instance.selection(); } catch { throw new BlockFenceError("단일 서식의 본문 문단에 커서를 놓으세요. 표 안이나 혼합 서식 문단은 삽입 위치로 지원하지 않습니다."); }
    if (selection.selection.target?.kind !== "body_paragraph" || selection.selection.target.section !== 0)
      throw new BlockFenceError("첫 구역의 본문 문단 뒤에만 블록을 삽입할 수 있습니다.");
    const source = instance.exportHwpx();
    if (source.epoch !== epoch.current || selection.state.documentEpoch !== epoch.current || source.revision !== selection.revision)
      throw new BlockFenceError("문서 식별 또는 편집 리비전이 바뀌었습니다. 다시 확인하세요.");
    if (source.contentLoss.count) throw new BlockFenceError("엔진의 내용 손실 경고가 있어 블록을 준비할 수 없습니다. 복구용 다운로드를 확인하세요.");
    if (source.bytes.length > 10 * 1024 * 1024) throw new BlockFenceError("블록 삽입은 10MB 이하의 문서를 지원합니다.");
    dropBlockUndo();
    const captured = { adapter: instance, document: { ...document }, token: token.current, selection, source };
    blockHold.current = captured; setBlockHolding(true);
    return captured;
  }
  function captureToc(): TocCapture {
    const instance = adapter.current, document = doc.current;
    if (!ready || !instance || !document) throw new BlockFenceError("먼저 문서를 서버에 저장한 뒤 목차를 확인하세요.");
    if (document.role === "reader" || !token.current) throw new BlockFenceError("현재 문서의 편집 세션이 없습니다.");
    if (busy.current || pending.current || applied.current || rewriteRequest.current || reviewHold.current || blockHold.current || aiBusy)
      throw new BlockFenceError("진행 중인 저장·승인·후보 비교를 먼저 마무리하세요.");
    // TOC targets are selected from analyzed body coordinates. Unsupported
    // live cursor locations need no navigation and remain exactly untouched.
    let selection: Snapshot | null = null;
    try { selection = instance.selection(); } catch { /* Whole-document fences still apply. */ }
    const state = instance.bridge.documentState();
    const source = instance.exportHwpx();
    if (source.epoch !== epoch.current || state.documentEpoch !== source.epoch || blockJson(state) !== blockJson(instance.bridge.documentState()) || (selection && (selection.state.documentEpoch !== source.epoch || selection.revision !== source.revision)))
      throw new BlockFenceError("문서 식별 또는 편집 리비전이 바뀌었습니다. 다시 확인하세요.");
    if (source.contentLoss.count || source.bytes.length > 10 * 1024 * 1024)
      throw new BlockFenceError("내용 손실 경고가 없고 10MB 이하인 문서에서 목차를 확인할 수 있습니다.");
    dropBlockUndo();
    const captured = { adapter: instance, document: { ...document }, token: token.current, selection, state, source };
    blockHold.current = captured; setBlockHolding(true); return captured;
  }
  function ownsBlock(captured: BlockCapture | TocCapture) {
    return editorAlive.current && blockHold.current === captured && adapter.current === captured.adapter &&
      doc.current?.id === captured.document.id && doc.current.current_version === captured.document.current_version &&
      token.current === captured.token && epoch.current === captured.source.epoch;
  }
  function releaseBlock(captured: BlockCapture | TocCapture) {
    if (blockHold.current !== captured) return;
    blockHold.current = null;
    if (editorAlive.current) setBlockHolding(false);
  }
  const equalBytes = (left: Uint8Array, right: Uint8Array) => left.length === right.length && left.every((value, index) => value === right[index]);
  async function adoptBlock(prepared: PreparedBlock | PreparedToc, check = "block-check") {
    const captured = prepared.capture;
    if (!ownsBlock(captured) || busy.current || pending.current || applied.current || reviewHold.current || rewriteRequest.current)
      throw new BlockFenceError("문서 상태나 진행 중인 작업이 바뀌었습니다. 다시 준비하세요.");
    busy.current = true;
    try {
      await api(`/documents/${captured.document.id}/${check}`, {
        ...jsonRequest("POST", { base_version: captured.document.current_version }),
        headers: { "Content-Type": "application/json", "X-Edit-Session": captured.token },
      });
      // After the asynchronous permission check, every fence and the swap are
      // synchronous. User input cannot race between the check and adoption.
      if (!ownsBlock(captured) || pending.current || applied.current || reviewHold.current || rewriteRequest.current)
        throw new BlockFenceError("문서 세션이나 기준 버전이 바뀌었습니다. 다시 준비하세요.");
      const current = captured.selection ? captured.adapter.selection() : null, output = captured.adapter.exportHwpx();
      if (("state" in captured && blockJson(captured.adapter.bridge.documentState()) !== blockJson(captured.state)) || (captured.selection && blockJson(current) !== blockJson(captured.selection)) || output.epoch !== captured.source.epoch ||
          output.revision !== captured.source.revision || output.contentLoss.count || !equalBytes(output.bytes, captured.source.bytes))
        throw new BlockFenceError("미리보기 준비 후 문서나 커서가 바뀌었습니다. 현재 문단에서 다시 준비하세요.");
      const staged = prepared.adapter.exportHwpx();
      if (staged.epoch !== prepared.output.epoch || staged.revision !== prepared.output.revision || staged.contentLoss.count || !equalBytes(staged.bytes, prepared.output.bytes))
        throw new BlockFenceError("검증한 미리보기 내용이 바뀌었습니다. 다시 준비하세요.");
      const nextHost = blockHosts.current.findIndex(host => host === prepared.adapter.sdk.element.parentElement);
      if (!mount.current || nextHost < 0 || !prepared.adapter.sdk.element.isConnected)
        throw new BlockFenceError("편집기 컨테이너를 확인할 수 없습니다.");
      dropBlockUndo(); adapterOff.current();
      adapter.current = prepared.adapter; epoch.current = staged.epoch;
      activateBlockHost(nextHost);
      watchAdapter(prepared.adapter);
      blockUndo.current = { previous: captured.adapter, current: prepared.adapter, snapshot: staged };
      setBlockUndoAvailable(true); dirtyRef.current = true; setDirty(true); setStatus("저장되지 않은 변경 있음");
      if (check === "toc-check") { tocKnown.current = true; setTocStatus("current"); }
      else if (tocKnown.current) setTocStatus("stale");
      releaseBlock(captured);
    } finally { busy.current = false; }
  }
  function undoBlock() {
    const undo = blockUndo.current;
    if (!undo || !adapter.current || adapter.current !== undo.current || busy.current || pending.current || applied.current || blockHold.current || reviewHold.current || rewriteRequest.current) return;
    try {
      const current = undo.current.exportHwpx();
      if (current.epoch !== undo.snapshot.epoch || current.revision !== undo.snapshot.revision || current.contentLoss.count || !equalBytes(current.bytes, undo.snapshot.bytes))
        throw new BlockFenceError("삽입 뒤 다른 편집이 있어 취소할 수 없습니다. 편집기의 실행 취소를 사용하세요.");
      const previousHost = blockHosts.current.findIndex(host => host === undo.previous.sdk.element.parentElement);
      if (!mount.current || previousHost < 0) throw new BlockFenceError("편집기 컨테이너를 확인할 수 없습니다.");
      adapterOff.current(); activateBlockHost(previousHost);
      adapter.current = undo.previous; epoch.current = undo.previous.exportHwpx().epoch;
      watchAdapter(undo.previous); undo.current.destroy(); blockUndo.current = null;
      setBlockUndoAvailable(false); dirtyRef.current = true; setDirty(true); setStatus("저장되지 않은 변경 있음");
      if (tocKnown.current) setTocStatus("stale");
    } catch (e) { setError(String(e)); dropBlockUndo(); }
  }
  useEffect(() => {
    editorAlive.current = true;
    let disposed = false;
    let instance: EditorAdapter | null = null;
    (async () => {
      try {
        if (initial) {
          const lease = await api<{ token: string; document: Document }>(
            `/documents/${initial.id}/session`,
            { method: "POST" },
          );
          token.current = lease.token;
          doc.current = lease.document;
        }
        if (disposed) return;
        instance = await EditorAdapter.mount(blockHosts.current[0]!);
        if (disposed) {
          instance.destroy();
          return;
        }
        adapter.current = instance;
        if (doc.current) {
          const response = await fetch(fileUrl(doc.current));
          if (!response.ok) throw new Error("문서 다운로드 실패");
          const bytes = await response.arrayBuffer();
          baseBytes.current = new Uint8Array(bytes);
          await instance.load(bytes, doc.current.name);
        } else await instance.bridge.newDocument();
        // Studio creates its genuine blank HWP document on startup; export is engine serialization.
        const state = await instance.sdk.getDocumentState();
        if (disposed) return;
        epoch.current = state.documentEpoch;
        savedRevision.current = initial ? instance.bridge.revision() : -1;
        setDirty(!initial);
        setStatus(initial ? "저장 완료" : "저장되지 않은 변경 있음");
        setReady(true);
        const warning = instance.bridge.warnings();
        if (warning && (warning as { count?: number }).count)
          setWarnings(JSON.stringify(warning));
        watchAdapter(instance);
      } catch (e) {
        setError(`열기 실패: ${String(e)}`);
        setStatus("열기 실패");
      }
    })();
    const heartbeat = setInterval(() => {
      if (doc.current && token.current)
        api(`/documents/${doc.current.id}/session`, {
          method: "PUT",
          headers: { "X-Edit-Session": token.current },
        }).catch((e) =>
          setError(
            `세션 갱신 실패: ${e.message} 변경사항을 복구용 다운로드하세요.`,
          ),
        );
    }, 30000);
    const guard = (e: BeforeUnloadEvent) => {
      if (dirtyRef.current || busy.current || contextDraftDirty.current || blockHold.current || assistantHold.current) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => {
      disposed = true;
      editorAlive.current = false;
      adapterOff.current();
      clearInterval(heartbeat);
      window.removeEventListener("beforeunload", guard);
      adapter.current?.destroy();
      dropBlockUndo(); blockHold.current = null;
      if (doc.current && token.current)
        void api(`/documents/${doc.current.id}/session`, {
          method: "DELETE",
          headers: { "X-Edit-Session": token.current },
          keepalive: true,
        }).catch(() => {});
    };
  }, []);
  async function save(force = false) {
    const a = adapter.current;
    if (!a || busy.current || blockHold.current || (!dirtyRef.current && !force)) return false;
    dropBlockUndo();
    busy.current = true;
    setStatus("저장 중");
    setError("");
    try {
      if (applied.current && !pending.current?.applicationId)
        throw new Error("승인 적용의 저장 연결이 진행 중입니다. 같은 후보 버튼으로 재시도하세요.");
      if (!pending.current) {
        const snapshot = a.exportHwpx();
        if (snapshot.epoch !== epoch.current)
          throw new Error(
            "편집기 내부에서 다른 문서가 열렸습니다. 복구용 다운로드 후 목록에서 다시 여세요.",
          );
        if (snapshot.contentLoss.count) {
          downloadBytes(snapshot.bytes, "복구후보-" + name);
          throw new Error(
            "엔진에서 내용 손실을 보고했습니다. 정상 버전 저장을 차단하고 복구 후보를 다운로드했습니다: " +
              JSON.stringify(snapshot.contentLoss.losses),
          );
        }
        pending.current = {
          snapshot,
          key: crypto.randomUUID(),
          base: doc.current?.current_version || null,
          documentId: doc.current?.id || crypto.randomUUID(),
        };
      }
      const p = pending.current;
      if (p.snapshot.epoch !== epoch.current || p.snapshot.contentLoss.count)
        throw new Error("문서 식별 또는 내용 손실 검사에 실패했습니다. 복구용 다운로드 후 확인하세요.");
      if (!doc.current) {
        const created = await upload(
          p.snapshot.bytes,
          name,
          p.documentId,
          p.key,
        );
        const lease = await api<{ token: string }>(
          `/documents/${created.id}/session`,
          { method: "POST" },
        );
        token.current = lease.token;
        doc.current = created;
      } else {
        if (!baseBytes.current)
          throw new Error("기준 파일이 없습니다. 문서를 다시 여세요.");
        if (!p.delta)
          p.delta = await deltaBody(baseBytes.current, p.snapshot.bytes);
        const v = await api<Version>(
          `/documents/${doc.current.id}/versions?base_version=${p.base}&cause=web-edit`,
          {
            method: "POST",
            headers: {
              "X-Edit-Session": token.current,
              "Idempotency-Key": p.key,
              "Content-Type": "application/vnd.hwp.delta+json",
              ...(p.applicationId ? { "X-Application-Id": p.applicationId } : {}),
            },
            body: p.delta,
          },
        );
        doc.current = { ...doc.current, current_version: v.id };
      }
      savedRevision.current = p.snapshot.revision;
      baseBytes.current = new Uint8Array(p.snapshot.bytes);
      pending.current = null;
      if (p.applicationId && applied.current?.id === p.applicationId) {
        applied.current = null;
        reviewHold.current = false;
        setHoldingReview(false);
      }
      const clean = await a.bridge.acknowledge(
        p.snapshot.revision,
        p.snapshot.epoch,
        name,
      );
      setDirty(!clean);
      dirtyRef.current = !clean;
      setStatus(clean ? "저장 완료" : "저장되지 않은 변경 있음");
      return true;
    } catch (e) {
      setStatus(pending.current?.applicationId ? "저장 확인 실패" : "저장 실패");
      setError(
        String(e) + " · 서버 반영 여부를 확인하지 못했습니다. 같은 저장 재시도 또는 복구용 다운로드를 사용하세요.",
      );
      return false;
    } finally {
      busy.current = false;
    }
  }
  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => {
    const timer = setInterval(() => {
      if (dirtyRef.current && !pending.current && !reviewHold.current && !assistantHold.current && !blockHold.current) void saveRef.current();
    }, 20000);
    return () => clearInterval(timer);
  }, []);
  async function review() {
    if (blockHold.current) { setAiError("블록 삽입 준비를 먼저 마무리하세요."); return; }
    if (!adapter.current || !doc.current) {
      setAiError("먼저 문서를 서버에 저장하세요.");
      return;
    }
    setAiBusy(true);
    setAiError("");
    setProposal(null);
    try {
      const snapshot = adapter.current.selection();
      const d = doc.current;
      const result = await api<{
        replacement: string;
        reason: string;
        original_sha256: string;
      }>("/ai/review", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Edit-Session": token.current,
        },
        body: JSON.stringify({
          document_id: d.id,
          base_version: d.current_version,
          revision: snapshot.revision,
          original: snapshot.evidence.text,
          target: snapshot.selection.target,
        }),
      });
      if (result.original_sha256 !== snapshot.evidence.textSha256)
        throw new Error("원문 검증 실패");
      setProposal({
        snapshot,
        ...result,
        documentId: d.id,
        base: d.current_version,
      });
    } catch (e) {
      setAiError(String(e));
    } finally {
      setAiBusy(false);
    }
  }
  return (
    <div className="workspace editor-workspace">
      {discardTarget && <div className="editor-discard-dialog" role="alertdialog" aria-modal="true" aria-label="미저장 입력 확인">
        <section><h2>미저장 입력 확인</h2><p>{discardTarget === "panel" ? "아직 보내거나 저장하지 않은 대화 입력이 있습니다. 입력을 버리고 대화 패널을 닫을까요?" : "저장하지 않은 문서 변경이나 대화 입력이 있습니다. 저장하지 않고 문서 목록으로 돌아갈까요?"}</p>
          <button autoFocus onClick={() => setDiscardTarget(null)}>계속 편집</button>
          <button onClick={() => {
            const target = discardTarget; setDiscardTarget(null);
            if (target === "document") onClose();
            else { contextDraftDirty.current = false; setConversationPanel(false); setConversationMounted(false); }
          }}>{discardTarget === "panel" ? "입력 버리고 닫기" : "저장하지 않고 목록으로"}</button>
        </section>
      </div>}
      <WorkspaceNav active="documents" compact className="editor-rail" label="문서 작업">
        <button
          className="editor-rail-action editor-back"
          title="문서 목록"
          aria-label="← 문서 목록"
          onClick={() => {
            if (blockHold.current) { setError("블록 준비를 삽입하거나 취소한 뒤 문서 목록으로 돌아가세요."); return; }
            if (assistantHold.current) { setError("AI 응답을 확인하거나 수신을 중단한 뒤 문서 목록으로 돌아가세요."); return; }
            if (busy.current) return;
            if (dirtyRef.current || contextDraftDirty.current) { setDiscardTarget("document"); return; }
            onClose();
          }}
        >
          <WorkspaceIcon name="back" /><span>목록</span>
        </button>
        <details className="editor-menu editor-document-menu" name="editor-overflow" ref={documentMenu}>
          <summary className="editor-rail-action" aria-label="문서 정보" title={`${name} · ${status}`}>
            <WorkspaceIcon name="document" /><span>문서</span>
            <span role="status" className={`editor-save-state${status.includes("실패") ? " bad" : ""}${dirty ? " unsaved" : ""}`}>
              <span className="editor-visually-hidden">{status}</span>
            </span>
          </summary>
          <div className="editor-menu-content editor-document" role="group" aria-label="현재 문서 정보">
            <strong title={name}>{name}</strong>
            <p>{status}</p>
            {doc.current && <p>{tocLabel} · {tocDescription}</p>}
          </div>
        </details>
        <button
          className="primary editor-rail-action editor-save"
          title="서버 저장"
          aria-label="저장"
          disabled={!ready || status === "저장 중" || blockHolding}
          onClick={() => void save(true)}
        >
          <WorkspaceIcon name="save" /><span>저장</span>
        </button>
        {doc.current && (
          <SavedDownload document={doc.current} label="저장본 다운로드" rail />
        )}
        <details className="editor-menu" name="editor-overflow" ref={fileMenu}>
          <summary className="editor-rail-action" aria-label="파일 작업" title="파일 작업"><WorkspaceIcon name="file" /><span>파일</span></summary>
          <div className="editor-menu-content" role="group" aria-label="파일 작업 메뉴" onClick={event => {
            if ((event.target as Element).closest("button, a") && fileMenu.current) fileMenu.current.open = false;
          }}>
        <button
          disabled={!ready || blockHolding}
          onClick={async () => {
            if (blockHold.current || busy.current || pending.current || applied.current) { setError("진행 중인 저장·승인·블록 준비를 먼저 완료하세요."); return; }
            const n = prompt("다른 이름으로 저장", name);
            if (!n) return;
            try {
              const s = adapter.current!.exportHwpx();
              if (s.contentLoss.count)
                throw new Error("내용 손실 경고로 저장이 차단되었습니다.");
              await upload(s.bytes, n.endsWith(".hwpx") ? n : n + ".hwpx");
              setError("다른 문서로 저장했습니다. 문서 목록에서 확인하세요.");
            } catch (e) {
              setError(String(e));
            }
          }}
        >
          다른 이름으로 저장
        </button>
        <button
          disabled={!ready}
          onClick={() => {
            try {
              downloadBytes(
                adapter.current!.exportHwpx().bytes,
                "복구용-" + name,
              );
            } catch (e) {
              setError(String(e));
            }
          }}
        >
          복구용 다운로드
        </button>
        <button disabled={!doc.current} onClick={() => onHistory(doc.current!)}>
          버전 기록
        </button>
          <p>다운로드는 서버 저장과 별개입니다. 미저장 내용은 복구용 다운로드에 포함됩니다.</p>
          </div>
        </details>
        <div className="editor-rail-group" role="group" aria-label="문서 구성">
          <button className="editor-rail-action" disabled={!ready} title="블록 삽입" aria-label={`블록 삽입 ${blockPanel ? "닫기" : "열기"}`} aria-pressed={blockPanel}
            onClick={() => showPanel(blockPanel ? null : "block")}><WorkspaceIcon name="block" /><span>표·블록 삽입</span></button>
          {doc.current?.role !== "reader" && <button className="editor-rail-action editor-toc-state" disabled={!ready} data-state={tocStatus} title={`목차 관리 · ${tocLabel} · ${tocDescription}`} aria-label={`목차 ${tocPanel ? "닫기" : "관리"}`} aria-pressed={tocPanel}
            onClick={() => showPanel(tocPanel ? null : "toc")}><WorkspaceIcon name="toc" /><span>목차</span><i className="editor-toc-dot" aria-hidden="true" /><span className="editor-visually-hidden" aria-label="목차 상태">{tocLabel}</span></button>}
        </div>
        <div className="editor-rail-group" role="group" aria-label="AI 및 대화">
          <button className="editor-rail-action" disabled={!doc.current} title="현재 문서를 보며 AI와 대화" aria-label={`문서 AI 대화 ${conversationPanel ? "닫기" : "열기"}`} aria-pressed={conversationPanel}
            onClick={() => showPanel(conversationPanel ? null : "conversation")}><WorkspaceIcon name="conversation" /><span>문서 AI 대화</span></button>
          <details className="editor-menu" name="editor-overflow" ref={toolsMenu}>
            <summary className="editor-rail-action" aria-label="추가 도구" title="추가 도구"><WorkspaceIcon name="more"/><span>추가 도구</span></summary>
            <div className="editor-menu-content">
              <strong>별도 AI 연결 기능</strong><p>사내·로컬 모델을 따로 연결한 환경에서 사용합니다.</p>
              <button aria-label={`AI 검토 ${panel ? "닫기" : "열기"}`} onClick={() => { showPanel(panel ? null : "review"); if (toolsMenu.current) toolsMenu.current.open = false; }}>별도 AI 문단 검토</button>
            </div>
          </details>
        </div>
        {blockUndoAvailable && <button className="editor-rail-action editor-undo" title="방금 삽입 취소" aria-label="방금 삽입 취소" disabled={blockHolding || status === "저장 중"} onClick={undoBlock}><WorkspaceIcon name="undo" /><span>취소</span></button>}
          <details className="editor-menu editor-help" name="editor-overflow" ref={helpMenu}>
            <summary className="editor-rail-action" aria-label="편집 안내 및 진단" title={`편집 안내${warnings ? " · 엔진 진단 있음" : ""}`}><WorkspaceIcon name="help" /><span>안내</span></summary>
            <div className="editor-menu-content editor-help-content">
              <strong>편집 및 저장 안내</strong>
              <p>왼쪽 저장 버튼과 20초 간격 자동 저장이 서버에 새 버전을 만듭니다.</p>
              <p>AI 적용은 단일 서식의 본문 문단 전체만 지원합니다.</p>
              {doc.current && <p>{tocLabel} · {tocDescription}</p>}
              {warnings && <details className="editor-diagnostics"><summary>엔진 열기 진단 / 호환성 안내</summary><pre>{warnings}</pre></details>}
            </div>
          </details>
      </WorkspaceNav>
      <div className="editor-notifications" aria-label="편집 알림">
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {holdingReview && <div className="notice">후보 비교를 위해 자동 저장을 보류 중입니다. 수동 저장하면 기존 후보의 기준 버전이 오래될 수 있습니다.
        <button onClick={() => { reviewHold.current = false; setHoldingReview(false); }}>비교 종료 · 자동 저장 재개</button>
      </div>}
      {blockHolding && <div className="notice">블록·목차를 준비하는 동안 자동 저장을 보류합니다. 기존 문서에서 계속 편집할 수 있으며, 변경 후에는 다시 준비해야 합니다.</div>}
      </div>
      <div className="editor-row">
        <div className="editor block-editor-wrapper" ref={mount}>
          {[0, 1, 2].map(index => <div key={index} ref={node => { blockHosts.current[index] = node; }}
            className={`block-editor-slot${activeBlockHost === index ? " active" : ""}`}
            aria-hidden={activeBlockHost !== index} inert={activeBlockHost !== index} />)}
        </div>
        {blockPanel && <BlockInsert capture={captureBlock} isOwner={ownsBlock} release={releaseBlock} adopt={adoptBlock}
          getStagingHost={() => {
            const host = blockHosts.current.find(node => node && !node.querySelector("iframe"));
            if (!host) throw new BlockFenceError("비어 있는 미리보기 엔진 공간이 없습니다. 준비를 취소하고 다시 시도하세요.");
            return host;
          }} onClose={() => setBlockPanel(false)} />}
        {tocPanel && <TocPanel capture={captureToc} isOwner={ownsBlock} release={releaseBlock}
          adopt={prepared => adoptBlock(prepared, "toc-check")}
          getStagingHost={() => {
            const host = blockHosts.current.find(node => node && !node.querySelector("iframe"));
            if (!host) throw new BlockFenceError("비어 있는 미리보기 엔진 공간이 없습니다. 준비를 취소하고 다시 시도하세요.");
            return host;
          }} onAnalyzed={(captured, value) => {
            if (!ownsBlock(captured)) return;
            tocKnown.current = value !== "none";
            const current = captured.adapter.exportHwpx();
            setTocStatus(value === "current" ? ((current.epoch !== captured.source.epoch || current.revision !== captured.source.revision || !equalBytes(current.bytes, captured.source.bytes)) ? "stale" : "recorded") : value);
          }} onClose={() => setTocPanel(false)} />}
        {conversationMounted && doc.current && (
          <aside className="conversation-panel" aria-label="대화 및 AI 패널" hidden={!conversationPanel}>
            <button className="panel-close" aria-label="대화 패널 닫기" onClick={() => showPanel(null)}>닫기</button>
            <CollaborationPanel
              document={doc.current}
              capture={() => adapter.current!.selection()}
              onAssistantActive={onAssistantActive}
              captureDocument={async () => {
                const instance = adapter.current, document = doc.current;
                if (!ready || !instance || !document || !epoch.current) throw new Error("문서를 먼저 열고 저장한 뒤 대화를 시작하세요.");
                if (blockHold.current || busy.current || pending.current || applied.current || rewriteRequest.current) throw new Error("진행 중인 저장·수정 적용을 먼저 마무리하세요.");
                const source = instance.exportHwpx();
                let selection: Snapshot | null = null;
                try {
                  const candidate = instance.selection();
                  if (candidate.evidence.text && Array.from(candidate.evidence.text).length <= 4000 && candidate.selection.target?.kind === "body_paragraph") selection = candidate;
                } catch { /* Document questions also work in unsupported edit selections. */ }
                const document_context = await assistantDocumentContext(source);
                if (adapter.current !== instance || doc.current?.current_version !== document.current_version || instance.bridge.revision() !== source.revision || epoch.current !== source.epoch)
                  throw new Error("읽는 동안 문서가 바뀌었습니다. 현재 내용으로 다시 요청하세요.");
                return { base_version: document.current_version, document_context, selection };
              }}
              sendAssistant={async (thread, request) => {
                if (!ready || !adapter.current || !doc.current || busy.current || pending.current || applied.current || blockHold.current || rewriteRequest.current)
                  throw new Error("진행 중인 저장·수정 적용을 먼저 마무리하세요.");
                assistantHold.current = true;
                return api<AssistantTurn>(`/threads/${thread}/assistant-turns`, {
                  ...jsonRequest("POST", request), headers: { "Content-Type": "application/json", "X-Edit-Session": token.current },
                });
              }}
              onContextDirty={onContextDirty}
              getPendingRewrite={() => rewriteRequest.current}
              requestRewrite={async (thread, instruction, contextRevision, expectedSelection) => {
                if (blockHold.current) throw new Error("블록 삽입 준비를 먼저 마무리하세요.");
                if (applied.current) throw new Error("승인 적용의 저장 연결이 미해결입니다. 같은 후보로 저장 확인을 먼저 완료하세요. 새 지시는 보관됩니다.");
                if (busy.current || pending.current) throw new Error("진행 중인 저장을 먼저 완료하세요.");
                const previous = rewriteRequest.current;
                if (previous && (previous.thread !== thread || previous.instruction !== instruction))
                  throw new Error("응답이 확인되지 않은 후보 요청이 있습니다. 같은 대화와 개선 요청으로 먼저 결과를 확인하세요.");
                if (!previous) {
                  // Capture before pausing autosave: an unsupported selection can throw here.
                  const snapshot = adapter.current!.selection();
                  if (expectedSelection && blockJson(snapshot) !== blockJson(expectedSelection)) throw new Error("질문 이후 문서나 선택 문단이 바뀌었습니다. 현재 문단에서 AI에게 다시 요청하세요.");
                  rewriteRequest.current = {thread, instruction, id: crypto.randomUUID(), snapshot,
                    base: doc.current!.current_version, previousHold: reviewHold.current, contextRevision};
                }
                const frozen = rewriteRequest.current!;
                reviewHold.current = true; setHoldingReview(true);
                try {
                  await api(`/threads/${thread}/rewrite-tasks`, {
                    ...jsonRequest("POST", { id: frozen.id, base_version: frozen.base,
                      expected_revision: 0, selection: frozen.snapshot, instruction: frozen.instruction,
                      ...(frozen.contextRevision !== undefined ? { context_revision: frozen.contextRevision } : {}) }),
                    headers: { "Content-Type": "application/json", "X-Edit-Session": token.current },
                  });
                  if (rewriteRequest.current === frozen) rewriteRequest.current = null;
                } catch (e) {
                  // A remounted panel may have cancelled this request and started another.
                  if (rewriteRequest.current !== frozen) throw e;
                  if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
                    rewriteRequest.current = null;
                    reviewHold.current = frozen.previousHold;
                    setHoldingReview(frozen.previousHold);
                    throw e;
                  }
                  throw new Error(`${String(e)} · 요청 원문과 ID를 보관했습니다. 같은 개선 요청으로 다시 눌러 결과를 확인하세요.`);
                }
              }}
              cancelRewrite={async (task) => {
                const frozen = rewriteRequest.current;
                await api(`/rewrite-tasks/${task}/cancel`, { method: "POST" });
                if (frozen?.id === task && rewriteRequest.current === frozen) rewriteRequest.current = null;
              }}
              approveRewrite={async (task, candidate, applicationId) => {
                if (blockHold.current) throw new Error("블록 삽입 준비를 먼저 마무리하세요.");
                if (applied.current && applied.current.id !== applicationId)
                  throw new Error("승인 적용의 저장 연결이 미해결입니다. 먼저 기존 후보의 저장을 확인하세요.");
                if (busy.current || pending.current && pending.current.applicationId !== applicationId)
                  throw new Error("진행 중인 다른 저장을 먼저 완료하세요.");
                // A committed save response can be lost. Retry the exact frozen output/key
                // before re-reading approval, which may already point at the saved version.
                if (pending.current?.applicationId === applicationId) {
                  if (!await save(true)) throw new Error("승인 적용은 보존됐지만 서버 저장 확인에 실패했습니다. 같은 후보로 재시도하세요.");
                  return;
                }
                busy.current = true;
                try {
                  const approval = await api<{snapshot: Snapshot; replacement: string; status: string; saved_version: string | null}>(`/rewrite-tasks/${task}/approve`, {
                    ...jsonRequest("POST", { id: applicationId, candidate_id: candidate }),
                    headers: { "Content-Type": "application/json", "X-Edit-Session": token.current },
                  });
                  if (rewriteRequest.current?.id === task) rewriteRequest.current = null;
                  if (approval.saved_version) {
                    if (approval.saved_version !== doc.current?.current_version) throw new Error("에디터 기준과 저장 버전이 다릅니다. 다시 여세요.");
                    return;
                  }
                  if (!applied.current || applied.current.id !== applicationId) {
                    const receipt = await adapter.current!.apply(approval.snapshot, approval.replacement, applicationId);
                    const snapshot = adapter.current!.exportHwpx();
                    applied.current = { id: applicationId, snapshot, receipt };
                  }
                  const frozen = applied.current;
                  if (frozen.snapshot.epoch !== epoch.current || frozen.snapshot.contentLoss.count)
                    throw new Error("승인 적용 출력의 문서 식별 또는 내용 손실 검사에 실패했습니다.");
                  const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(frozen.snapshot.bytes)))).map(b => b.toString(16).padStart(2,"0")).join("");
                  await api(`/applications/${applicationId}/receipt`, {
                    ...jsonRequest("POST", { engine_receipt: frozen.receipt, export_sha256: hash }),
                    headers: { "Content-Type": "application/json", "X-Edit-Session": token.current },
                  });
                  pending.current = { snapshot: frozen.snapshot, key: applicationId, base: doc.current!.current_version,
                    documentId: doc.current!.id, applicationId };
                } finally { busy.current = false; }
                if (!await save(true)) throw new Error("승인 적용은 보존됐지만 서버 저장 확인에 실패했습니다. 같은 후보로 재시도하세요.");
                applied.current = null; reviewHold.current = false; setHoldingReview(false);
              }}
              approve={async (thread, proposal) => {
                if (blockHold.current) throw new Error("블록 삽입 준비를 먼저 마무리하세요.");
                if (busy.current || pending.current)
                  throw new Error("진행 중인 저장을 먼저 완료하세요.");
                const candidate = await api<{
                  snapshot: Snapshot;
                  replacement: string;
                }>(`/threads/${thread}/proposals/${proposal}/check`, {
                  method: "POST",
                  headers: { "X-Edit-Session": token.current },
                });
                await adapter.current!.apply(
                  candidate.snapshot,
                  candidate.replacement,
                );
              }}
            />
          </aside>
        )}
        {panel && (
          <aside className="review-panel" aria-label="문단 AI 검토 패널">
            <button className="panel-close" aria-label="검토 패널 닫기" onClick={() => showPanel(null)}>닫기</button>
            <h2>별도 AI 문단 검토</h2>
            <p className="hint">사내·로컬 모델의 연결 상태를 확인한 뒤 사용할 수 있습니다.</p>
            <button disabled={!doc.current} onClick={() => showPanel("conversation")}>문서 AI 대화로 이동</button>
            <p>
              검토할 본문 문단에 커서를 놓으세요. 승인 전에는 문서를 변경하지
              않습니다.
            </p>
            <button disabled={!ready || aiBusy || !aiConnection?.model_available || aiConnectionChecking} onClick={() => void review()}>
              {aiBusy ? "검토 중…" : "현재 문단 검토"}
            </button>
            <button
              disabled={aiConnectionChecking}
              onClick={() => void checkReviewConnection()}
            >
              {aiConnectionChecking ? "연결 확인 중…" : "AI 연결 확인"}
            </button>
            {aiConnection && <p role="status">{aiConnection.model_available ? `${aiConnection.configured_model} 연결됨` : "설정한 모델을 사용할 수 없습니다. 서버의 모델 설정을 확인하세요."}</p>}
            {aiError && <p role="alert">{aiError}</p>}
            {proposal && (
              <>
                <h3>원문</h3>
                <p className="text">{proposal.snapshot.evidence.text}</p>
                <h3>수정안</h3>
                <p className="text">{proposal.replacement}</p>
                <h3>이유</h3>
                <p>{proposal.reason}</p>
                <button
                  onClick={async () => {
                    try {
                      if (
                        blockHold.current ||
                        doc.current?.id !== proposal.documentId ||
                        doc.current.current_version !== proposal.base
                      )
                        throw new Error(
                          "문서 버전이 바뀌었습니다. 다시 검토하세요.",
                        );
                      await adapter.current!.apply(
                        proposal.snapshot,
                        proposal.replacement,
                      );
                      setProposal(null);
                    } catch (e) {
                      setAiError(String(e));
                    }
                  }}
                >
                  승인하고 적용
                </button>
                <button onClick={() => setProposal(null)}>제안 버리기</button>
              </>
            )}
            <small>사실·회사 규정 적합성 검증 기능이 아닙니다.</small>
          </aside>
        )}
      </div>
      {history && (
        <History
          document={history}
          onClose={closeHistory}
          onRestore={async (v) => {
            if (blockHold.current || pending.current || applied.current) { setError("블록 준비와 진행 중인 저장·승인을 먼저 마무리하세요."); return; }
            if (dirtyRef.current) {
              setError(
                "먼저 변경사항을 저장하거나 복구용 다운로드 후 목록에서 복원하세요.",
              );
              return;
            }
            if (busy.current) return;
            busy.current = true;
            try {
              const d = doc.current!;
              const restored = await api<Version>(
                `/documents/${d.id}/versions/${v.id}/restore?base_version=${d.current_version}`,
                {
                  method: "POST",
                  headers: {
                    "X-Edit-Session": token.current,
                    "Idempotency-Key": crypto.randomUUID(),
                  },
                },
              );
              doc.current = { ...d, current_version: restored.id };
              const bytes = await (
                await fetch(fileUrl(doc.current))
              ).arrayBuffer();
              const state = await adapter.current!.load(bytes, name);
              baseBytes.current = new Uint8Array(bytes);
              epoch.current = state.documentEpoch;
              savedRevision.current = adapter.current!.bridge.revision();
              setDirty(false);
              dirtyRef.current = false;
              setStatus("저장 완료");
              closeHistory();
            } catch (e) {
              setError(String(e));
            } finally {
              busy.current = false;
            }
          }}
        />
      )}
    </div>
  );
}
function AuthGate() {
  const [account, setAccount] = useState<{
      user_id: string;
      authentication_required: boolean;
    } | null>(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [token, setToken] = useState("");
  useEffect(() => {
    api<NonNullable<typeof account>>("/auth/me")
      .then(setAccount)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);
  if (loading) return <main className="drive">문서함 연결 중…</main>;
  if (account) return <App account={account} />;
  return (
    <main className="drive">
      <h1>HWPX 문서함 로그인</h1>
      <p>관리자가 발급한 사용자별 토큰을 입력하세요.</p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setError("");
          try {
            await api("/auth/login", jsonRequest("POST", { token }));
            setToken("");
            setAccount(await api("/auth/me"));
          } catch (err) {
            setError(String(err));
          }
        }}
      >
        <input
          aria-label="인증 토큰"
          type="password"
          autoComplete="off"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <button type="submit">로그인</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<AuthGate />);
