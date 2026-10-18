import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
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

function App({
  account,
}: {
  account: { user_id: string; authentication_required: boolean };
}) {
  const [docs, setDocs] = useState<Document[]>([]),
    [query, setQuery] = useState(""),
    [sort, setSort] = useState("newest"),
    [copying, setCopying] = useState(false),
    [copyNotice, setCopyNotice] = useState<Document | null>(null),
    [copyPending, setCopyPending] = useState<{ source: Document; body: { document_id: string; source_version: string; request_id: string; name: string } } | null>(null),
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
  async function incoming(file: File) {
    try {
      setError("");
      if (!file.name.toLowerCase().endsWith(".hwpx"))
        throw new Error("HWPX 파일만 업로드하세요.");
      if (file.size > 30 * 1024 * 1024)
        throw new Error("30MB 이하 파일만 업로드하세요.");
      await upload(new Uint8Array(await file.arrayBuffer()), file.name);
      refresh();
    } catch (e) {
      setError(String(e));
    }
  }
  if (standardDesigner) return <StandardDesigner onClose={() => { setStandardDesigner(false); refresh(); }}
    onReport={standard => { setReportStandard(standard); setStandardDesigner(false); setReports(true); }} />;
  if (reports) return <Reports initialVisualStandard={reportStandard} onClose={() => { setReports(false); setReportStandard(undefined); refresh(); }} onOpen={doc => { setReports(false); setReportStandard(undefined); setActive(doc); }} />;
  if (templates) return <TemplateMarket registerDocument={registerTemplate}
    onClose={() => { setTemplates(false); setRegisterTemplate(undefined); refresh(); }}
    onApply={doc => { setTemplates(false); setRegisterTemplate(undefined); setActive(doc); }}
    onDesignStandard={() => { setTemplates(false); setRegisterTemplate(undefined); setStandardDesigner(true); }}
    onReport={() => { setTemplates(false); setRegisterTemplate(undefined); setReports(true); }} />;
  if (active?.role === "reader")
    return (
      <main className="drive">
        <button
          onClick={() => {
            setActive(null);
            refresh();
          }}
        >
          ← 문서 목록
        </button>
        <h1>{active.name}</h1>
        <p>열람 권한 · 저장본과 문서 공유 대화를 확인할 수 있습니다.</p>
        <a className="button" href={fileUrl(active)}>
          저장본 다운로드
        </a>
        <CollaborationPanel document={active} />
      </main>
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
    <main className="drive">
      <header>
        <div>
          <small>
            {account.user_id} ·{" "}
            {account.authentication_required
              ? "문서 권한 적용"
              : "단일 사용자 개발 모드"}
          </small>
          <h1>HWPX 문서함</h1>
          <p>원본을 보존하며 문서를 편집하고, 새 버전으로 저장합니다.</p>
        </div>
        <button onClick={() => setCreating(true)}>+ 새 문서</button>
        <button className="primary" onClick={() => setReports(true)}>보고서 작성</button>
        <button onClick={() => { setRegisterTemplate(undefined); setTemplates(true); }}>템플릿 마켓</button>
        <button onClick={() => setStandardDesigner(true)}>문서 표준 편집</button>
        {account.authentication_required && (
          <button
            onClick={async () => {
              await api("/auth/logout", { method: "POST" });
              location.reload();
            }}
          >
            로그아웃
          </button>
        )}
        <label className="button primary">
          HWPX 업로드
          <input
            aria-label="HWPX 업로드"
            type="file"
            accept=".hwpx"
            onChange={(e) => {
              if (e.target.files?.[0]) void incoming(e.target.files[0]);
              e.target.value = "";
            }}
          />
        </label>
      </header>
      <div className="notice">
        {account.authentication_required
          ? "문서별 열람·편집·소유자 권한과 대화 공개 범위가 적용됩니다."
          : "인증 미설정: 한 명의 local 사용자로 동작합니다. 사용자별 공유는 서버 인증을 설정해야 사용할 수 있습니다."}{" "}
        한글 왕복 호환성은 별도 검증이 필요합니다.
      </div>
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
      <button disabled={loading} onClick={refresh}>문서 목록 새로고침</button>
      {copyPending && <div className="notice">{copyPending.source.name}의 저장본 복제를 확인 중입니다.
        <button disabled={copying} onClick={() => void copySaved(copyPending.source)}>{copying ? "복제 중…" : "같은 사본 생성 결과 재확인"}</button>
      </div>}
      {copyNotice && <div className="notice">{copyNotice.name} 사본을 저장했습니다. 원본의 대화·권한·버전 이력은 복사하지 않습니다.
        <button onClick={() => setActive(copyNotice)}>사본 열기</button>
      </div>}
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      {loading ? (
        <p>문서 목록을 불러오는 중…</p>
      ) : docs.length === 0 ? (
        <div className="empty">
          문서가 없습니다. HWPX를 업로드하거나 새 문서를 만드세요.
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>문서명</th>
              <th>등록일</th>
              <th>작업</th>
            </tr>
          </thead>
          <tbody>
            {sortedDocs.map((d) => (
              <tr key={d.id}>
                <td>
                  <button className="link" onClick={() => setActive(d)}>
                    {d.name}
                  </button>
                </td>
                <td>{new Date(d.created_at * 1000).toLocaleString("ko-KR")}</td>
                <td>
                  <button
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
                    이름 변경
                  </button>
                  <a className="button" href={fileUrl(d)}>
                    다운로드
                  </a>
                  <button disabled={copying || !!copyPending} onClick={() => void copySaved(d)}>저장본 복제</button>
                  <button onClick={() => setHistory(d)}>버전 기록</button>
                  <label className="button">
                    새 버전 업로드
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
                  {d.role === "owner" && (
                    <button onClick={() => setSharing(d)}>공유 권한</button>
                  )}
                  {d.role !== "reader" && (
                    <button
                      onClick={() => { setRegisterTemplate(d); setTemplates(true); }}
                    >
                      양식 등록
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
    </main>
  );
}

function EditorRailIcon({ name }: { name: "back" | "document" | "save" | "download" | "file" | "block" | "toc" | "conversation" | "review" | "help" | "undo" }) {
  const paths = {
    back: "M15 5 8 12l7 7M8 12h12",
    document: "M7 3h7l4 4v14H7zM14 3v5h4M10 12h5M10 16h5",
    save: "M5 3h13l3 3v15H3V3zM7 3v6h10V3M7 21v-8h10v8",
    download: "M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4",
    file: "M3 6h7l2 2h9v12H3zM3 6V4h7l2 2h7v2",
    block: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM17.5 14v7M14 17.5h7",
    toc: "M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01",
    conversation: "M4 4h16v12H9l-5 4zM8 8h8M8 12h5",
    review: "M4 3h12v8M4 3v18h8M8 7h4M8 11h3M15 15l2 2 4-5",
    help: "M9.1 8a3 3 0 0 1 5.8 1c0 2-3 2-3 4M12 17h.01M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20",
    undo: "M9 4 4 9l5 5M4 9h10a6 6 0 0 1 0 12h-3",
  };
  return <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
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
  const fileMenu = useRef<HTMLDetailsElement>(null), helpMenu = useRef<HTMLDetailsElement>(null), documentMenu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const menus = [fileMenu, helpMenu, documentMenu];
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
  function showPanel(next: "block" | "toc" | "review" | "conversation" | null) {
    // A prepared preview belongs to its panel until it is explicitly applied or cancelled.
    if (blockHold.current && next !== null && ((blockPanel && next !== "block") || (tocPanel && next !== "toc"))) {
      setError("준비한 블록·목차를 반영하거나 해당 패널에서 취소한 뒤 다른 도구를 여세요.");
      return;
    }
    if (conversationPanel && next === null && contextDraftDirty.current) {
      if (!confirm("저장하지 않은 작성 정보가 있습니다. 패널을 닫으면 입력이 사라집니다. 계속할까요?")) return;
      contextDraftDirty.current = false;
    }
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
      if (dirtyRef.current || busy.current || contextDraftDirty.current || blockHold.current) {
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
      if (dirtyRef.current && !pending.current && !reviewHold.current && !blockHold.current) void saveRef.current();
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
      <nav className="editor-rail" aria-label="문서 작업">
        <button
          className="editor-rail-action editor-back"
          title="문서 목록"
          aria-label="← 문서 목록"
          onClick={() => {
            if (blockHold.current) { setError("블록 준비를 삽입하거나 취소한 뒤 문서 목록으로 돌아가세요."); return; }
            if (
              !busy.current &&
              (!(dirtyRef.current || contextDraftDirty.current) ||
                confirm(
                  "저장하지 않은 변경사항이 있습니다. 문서 목록으로 돌아갈까요?",
                ))
            )
              onClose();
          }}
        >
          <EditorRailIcon name="back" /><span>목록</span>
        </button>
        <details className="editor-menu editor-document-menu" name="editor-overflow" ref={documentMenu}>
          <summary className="editor-rail-action" aria-label="문서 정보" title={`${name} · ${status}`}>
            <EditorRailIcon name="document" /><span>문서</span>
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
          <EditorRailIcon name="save" /><span>저장</span>
        </button>
        {doc.current && (
          <a className="button editor-rail-action editor-download" aria-label="저장본 다운로드" title="서버에 저장된 버전 다운로드" href={fileUrl(doc.current)}>
            <EditorRailIcon name="download" /><span>받기</span>
          </a>
        )}
        <details className="editor-menu" name="editor-overflow" ref={fileMenu}>
          <summary className="editor-rail-action" aria-label="파일 작업" title="파일 작업"><EditorRailIcon name="file" /><span>파일</span></summary>
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
            onClick={() => showPanel(blockPanel ? null : "block")}><EditorRailIcon name="block" /><span>블록</span></button>
          {doc.current?.role !== "reader" && <button className="editor-rail-action editor-toc-state" disabled={!ready} data-state={tocStatus} title={`목차 관리 · ${tocLabel} · ${tocDescription}`} aria-label={`목차 ${tocPanel ? "닫기" : "관리"}`} aria-pressed={tocPanel}
            onClick={() => showPanel(tocPanel ? null : "toc")}><EditorRailIcon name="toc" /><span>목차</span><i className="editor-toc-dot" aria-hidden="true" /><span className="editor-visually-hidden" aria-label="목차 상태">{tocLabel}</span></button>}
        </div>
        <div className="editor-rail-group" role="group" aria-label="AI 및 대화">
          <button className="editor-rail-action" disabled={!doc.current} title="대화 · AI" aria-label={`대화 · AI ${conversationPanel ? "닫기" : "열기"}`} aria-pressed={conversationPanel}
            onClick={() => showPanel(conversationPanel ? null : "conversation")}><EditorRailIcon name="conversation" /><span>대화</span></button>
          <button className="editor-rail-action" title="사내 AI 검토" aria-label={`AI 검토 ${panel ? "닫기" : "열기"}`} aria-pressed={panel}
            onClick={() => showPanel(panel ? null : "review")}><EditorRailIcon name="review" /><span>검토</span></button>
        </div>
        {blockUndoAvailable && <button className="editor-rail-action editor-undo" title="방금 삽입 취소" aria-label="방금 삽입 취소" disabled={blockHolding || status === "저장 중"} onClick={undoBlock}><EditorRailIcon name="undo" /><span>취소</span></button>}
          <details className="editor-menu editor-help" name="editor-overflow" ref={helpMenu}>
            <summary className="editor-rail-action" aria-label="편집 안내 및 진단" title={`편집 안내${warnings ? " · 엔진 진단 있음" : ""}`}><EditorRailIcon name="help" /><span>안내</span></summary>
            <div className="editor-menu-content editor-help-content">
              <strong>편집 및 저장 안내</strong>
              <p>왼쪽 저장 버튼과 20초 간격 자동 저장이 서버에 새 버전을 만듭니다.</p>
              <p>AI 적용은 단일 서식의 본문 문단 전체만 지원합니다.</p>
              {doc.current && <p>{tocLabel} · {tocDescription}</p>}
              {warnings && <details className="editor-diagnostics"><summary>엔진 열기 진단 / 호환성 안내</summary><pre>{warnings}</pre></details>}
            </div>
          </details>
      </nav>
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
              onContextDirty={onContextDirty}
              getPendingRewrite={() => rewriteRequest.current}
              requestRewrite={async (thread, instruction, contextRevision) => {
                if (blockHold.current) throw new Error("블록 삽입 준비를 먼저 마무리하세요.");
                if (applied.current) throw new Error("승인 적용의 저장 연결이 미해결입니다. 같은 후보로 저장 확인을 먼저 완료하세요. 새 지시는 보관됩니다.");
                if (busy.current || pending.current) throw new Error("진행 중인 저장을 먼저 완료하세요.");
                const previous = rewriteRequest.current;
                if (previous && (previous.thread !== thread || previous.instruction !== instruction))
                  throw new Error("응답이 확인되지 않은 후보 요청이 있습니다. 같은 대화와 개선 요청으로 먼저 결과를 확인하세요.");
                if (!previous) {
                  // Capture before pausing autosave: an unsupported selection can throw here.
                  const snapshot = adapter.current!.selection();
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
            <h2>문단 AI 검토</h2>
            <p className="hint">이 패널은 별도 로컬·사내 모델 연결용입니다. 기본 Codex 후보 생성은 대화 · AI에서 사용할 수 있습니다.</p>
            <button disabled={!doc.current} onClick={() => showPanel("conversation")}>대화의 후보 생성으로 이동</button>
            <p>
              검토할 본문 문단에 커서를 놓으세요. 승인 전에는 문서를 변경하지
              않습니다.
            </p>
            <button disabled={!ready || aiBusy} onClick={() => void review()}>
              {aiBusy ? "검토 중…" : "현재 문단 검토"}
            </button>
            <button
              onClick={async () => {
                try {
                  const r = await api<{
                    models: string[];
                    configured_model: string;
                    model_available: boolean;
                  }>("/ai/models");
                  setAiError(
                    `설정 모델: ${r.configured_model} / 서버 제공: ${r.models.join(", ")} / 일치: ${r.model_available ? "예" : "아니오"}`,
                  );
                } catch (e) {
                  setAiError(String(e));
                }
              }}
            >
              AI 연결·모델 확인
            </button>
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
function History({
  document: d,
  onClose,
  onRestore,
}: {
  document: Document;
  onClose: () => void;
  onRestore: (v: Version) => Promise<void>;
}) {
  const [rows, setRows] = useState<Version[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [changes, setChanges] = useState<{
      recorded: boolean;
      version: Version;
      entries: {
        path: string;
        before_sha256: string | null;
        after_sha256: string | null;
      }[];
    } | null>(null);
  useEffect(() => {
    api<Version[]>(`/documents/${d.id}/versions`)
      .then(setRows)
      .catch((e) => setError(e.message));
  }, [d]);
  return (
    <div className="modal">
      <section>
        <button className="close" onClick={onClose}>
          닫기
        </button>
        <h2>{d.name} · 버전 기록</h2>
        <p>복원은 선택한 파일로 새 버전을 만듭니다. 과거 이력은 유지됩니다.</p>
        {error && <p role="alert">{error}</p>}
        {rows.map((v) => (
          <article key={v.id}>
            <strong>
              {
                (
                  {
                    upload: "최초 업로드",
                    "web-edit": "웹 편집",
                    reupload: "재업로드",
                    restore: "복원",
                  } as Record<string, string>
                )[v.cause]
              }
            </strong>{" "}
            · {new Date(v.created_at * 1000).toLocaleString("ko-KR")} ·{" "}
            {v.size.toLocaleString()} bytes{" "}
            <small>
              {v.id} {v.id === d.current_version ? "(현재)" : ""}
            </small>
            <a className="button" href={fileUrl(d, v.id)}>
              다운로드
            </a>
            <button
              onClick={async () => {
                try {
                  setChanges(
                    await api(`/documents/${d.id}/versions/${v.id}/changes`),
                  );
                } catch (e) {
                  setError(String(e));
                }
              }}
            >
              변경분 보기
            </button>
            <button
              disabled={busy || d.role === "reader"}
              onClick={async () => {
                if (!confirm("이 버전으로 복원하여 새 버전을 만들까요?"))
                  return;
                setBusy(true);
                try {
                  await onRestore(v);
                } finally {
                  setBusy(false);
                }
              }}
            >
              이 버전 복원
            </button>
          </article>
        ))}
        {changes && (
          <div className="notice">
            <strong>
              기준 버전: {changes.version.base_version || "최초 저장"}
            </strong>
            {!changes.recorded ? (
              <p>최초 저장 또는 변경분 기록 도입 전 버전입니다.</p>
            ) : changes.entries.length === 0 ? (
              <p>HWPX 내부 항목의 내용 변경이 없습니다.</p>
            ) : (
              <ul>
                {changes.entries.map((e) => (
                  <li key={e.path}>
                    {e.path} ·{" "}
                    {!e.before_sha256
                      ? "추가"
                      : !e.after_sha256
                        ? "삭제"
                        : "변경"}
                  </li>
                ))}
              </ul>
            )}
            <p>
              파일 내부 항목의 해시 비교입니다. 한글 서식·배치 호환성을 판정하는
              검사는 아닙니다.
            </p>
          </div>
        )}
      </section>
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
