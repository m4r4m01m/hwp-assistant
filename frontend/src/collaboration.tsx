import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, jsonRequest, type Document } from "./api";
import type { Snapshot } from "./editor";
import { RewritePanel, type PendingRewrite } from "./rewrite";
import { WritingContextEditor, type WritingContext } from "./context";
import { DocumentAssistantPanel, type AssistantActions } from "./document-assistant";
import { WorkspaceIcon } from "./workspace-nav";
import { CapabilityNotice, useFeature } from "./capabilities";
import { useComparisonDialog } from "./version-comparison";

type Thread = { id: string; title: string; visibility: "document" | "private" };
type Message = {
  id: string;
  author_id: string;
  role: string;
  content: string;
  source: string;
};
type Proposal = {
  id: string;
  replacement: string;
  reason: string;
  stale: boolean;
  snapshot: Snapshot;
};
type Detail = Thread & { context: WritingContext; messages: Message[] };

export function CollaborationPanel({
  document: doc,
  capture,
  approve,
  requestRewrite,
  approveRewrite,
  cancelRewrite,
  onContextDirty,
  getPendingRewrite,
  captureDocument, sendAssistant, onAssistantActive, active = true,
}: AssistantActions & {
  document: Document;
  capture?: () => Snapshot;
  approve?: (thread: string, proposal: string) => Promise<void>;
  requestRewrite?: (thread: string, instruction: string, contextRevision?: number, expectedSelection?: Snapshot) => Promise<void>;
  approveRewrite?: (task: string, candidate: string, application: string) => Promise<void>;
  cancelRewrite?: (task: string) => Promise<void>;
  onContextDirty?: (dirty: boolean) => void;
  getPendingRewrite?: () => PendingRewrite | null;
  active?: boolean;
}) {
  const chatFeature = useFeature("chat");
  const [threads, setThreads] = useState<Thread[]>([]),
    [selected, setSelected] = useState(""),
    [detail, setDetail] = useState<Detail | null>(null),
    [proposals, setProposals] = useState<Proposal[]>([]),
    [contextDirty, setContextDirty] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [creating, setCreating] = useState(false),
    [newTitle, setNewTitle] = useState("문서 검토"),
    [titleDirty, setTitleDirty] = useState(false),
    [createPending, setCreatePending] = useState(false),
    [switchRequest, setSwitchRequest] = useState<{ id: string; label: string } | null>(null),
    [discardNew, setDiscardNew] = useState(false),
    [checkingConnection, setCheckingConnection] = useState(false),
    [separateAi, setSeparateAi] = useState<{ ready: boolean; model: string; error: string } | null>(null);
  const generation = useRef(0);
  const [assistantDirty, setAssistantDirty] = useState(false), [assistantActive, setAssistantActive] = useState(false), [showRewrite, setShowRewrite] = useState(false);
  const assistantDirtyChanged = useCallback((dirty: boolean) => setAssistantDirty(dirty), []);
  const assistantActiveChanged = useCallback((active: boolean) => { setAssistantActive(active); onAssistantActive?.(active); }, [onAssistantActive]);
  const pendingCreate = useRef<{ id: string; title: string; visibility: "document" } | null>(null);
  const dirtyChanged = useCallback((dirty: boolean) => { setContextDirty(dirty); }, []);
  useEffect(() => { onContextDirty?.(contextDirty || message.length > 0 || titleDirty || createPending || assistantDirty); }, [contextDirty, message, titleDirty, createPending, assistantDirty, onContextDirty]);
  const writable = doc.role !== "reader";
  async function list() {
    const rows = await api<Thread[]>(`/documents/${doc.id}/threads`);
    setThreads(rows);
    return rows;
  }
  async function load(id = selected) {
    if (!id) return;
    const epoch = ++generation.current;
    const [d, p] = await Promise.all([
      api<Detail>(`/threads/${id}`),
      api<Proposal[]>(`/threads/${id}/proposals`),
    ]);
    if (generation.current !== epoch) return;
    setDetail(d);
    setProposals(p);

  }
  useEffect(() => {
    let live = true;
    void api<Thread[]>(`/documents/${doc.id}/threads`).then(rows => {
      if (!live) return;
      setThreads(rows);
      // The API returns conversations in creation order. Reopen the newest one.
      if (rows.length) setSelected(rows[rows.length - 1].id);
      else if (writable) setCreating(true);
    }).catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [doc.id]);
  useEffect(() => {
    setDetail(null);
    setProposals([]);
    void load(selected).catch((e) => setError(e.message));
    return () => {
      generation.current++;
    };
  }, [selected]);
  async function run(task: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function checkSeparateAi() {
    setCheckingConnection(true);
    setSeparateAi(null);
    try {
      const value = await api<{ configured_model: string; model_available: boolean }>("/ai/models");
      setSeparateAi({ ready: value.model_available, model: value.configured_model,
        error: value.model_available ? "" : "설정한 모델을 연결된 서버에서 찾지 못했습니다." });
    } catch (cause) {
      setSeparateAi({ ready: false, model: "", error: String(cause) });
    } finally { setCheckingConnection(false); }
  }
  function selectThread(id: string) {
    if (id === selected) return;
    if (assistantActive) return;
    if (contextDirty || message.length > 0 || titleDirty || assistantDirty) {
      setSwitchRequest({ id, label: threads.find(row => row.id === id)?.title || "선택한 대화" });
      return;
    }
    changeThread(id);
  }
  function changeThread(id: string) {
    dirtyChanged(false); setMessage(""); setSelected(id); setCreating(false); setShowRewrite(false);
    setNewTitle("문서 검토"); setTitleDirty(false); setSwitchRequest(null); setDiscardNew(false);
  }
  async function createThread() {
    if (!pendingCreate.current) {
      const title = newTitle.trim();
      if (!title) return;
      pendingCreate.current = { id: crypto.randomUUID(), title, visibility: "document" };
    }
    const frozen = pendingCreate.current;
    setCreatePending(true);
    let row: Thread;
    try {
      row = await api<Thread>(`/documents/${doc.id}/threads`, jsonRequest("POST", frozen));
    } catch (cause) {
      if (cause instanceof ApiError && cause.status >= 400 && cause.status < 500) {
        pendingCreate.current = null; setCreatePending(false);
        throw cause;
      }
      throw new Error(`${String(cause)} · 대화 이름과 요청을 보관했습니다. ‘대화 생성 결과 재확인’으로 같은 요청을 확인하세요.`);
    }
    // The committed conversation can be opened even if refreshing the list fails.
    pendingCreate.current = null; setCreatePending(false);
    setThreads(rows => rows.some(item => item.id === row.id) ? rows : [...rows, row]);
    changeThread(row.id);
    try { await list(); }
    catch { setError("대화를 만들었습니다. 목록을 새로 불러오지 못했지만 이 대화는 계속 사용할 수 있습니다."); }
  }
  function beginCreate() {
    if (assistantActive) return;
    if (!createPending && (contextDirty || message.length > 0 || assistantDirty)) {
      setSwitchRequest({ id: "__new__", label: "새 대화" });
      return;
    }
    void run(createThread);
  }
  return (
    <section className="collaboration">
      <h2>문서 AI 대화</h2>
      <p className="hint">이 문서의 수정 후보와 검토 내용을 함께 보관합니다. 대화는 문서 접근 권한이 있는 사용자와 공유됩니다.</p>
      <div className="conversation-switcher"><select
        aria-label="대화 선택"
        value={selected}
        onChange={(e) => selectThread(e.target.value)}
        disabled={busy || createPending || assistantActive}
      >
        <option value="">대화를 선택하세요</option>
        {threads.map((t) => (
          <option key={t.id} value={t.id}>
            문서 대화 · {t.title}
          </option>
        ))}
      </select>
      {writable && !creating && <button disabled={busy || assistantActive} onClick={() => { setCreating(true); setSwitchRequest(null); }}>새 대화</button>}
      <button aria-label="대화 새로고침" title="대화 새로고침" disabled={busy} onClick={() => void run(async () => { await list(); await load(); })}><WorkspaceIcon name="refresh" size={17}/></button>
      </div>
      {writable && creating && <form aria-label="새 대화 만들기" onSubmit={event => { event.preventDefault(); beginCreate(); }}>
        <label>대화 이름<input aria-label="대화 이름" value={newTitle} maxLength={120} disabled={busy || createPending}
          onChange={event => { setNewTitle(event.target.value); setTitleDirty(true); }} autoFocus /></label>
        <button type="submit" disabled={busy || assistantActive || !newTitle.trim()}>{busy ? "대화 여는 중…" : createPending ? "대화 생성 결과 재확인" : "AI와 대화 시작"}</button>
        {selected && <button type="button" disabled={busy || createPending} onClick={() => {
          if (titleDirty) setDiscardNew(true);
          else setCreating(false);
        }}>새 대화 취소</button>}
        {createPending && <p role="status">같은 이름과 요청으로 결과를 확인합니다. 중복 대화를 만들지 않습니다.</p>}
      </form>}
      {discardNew && <div role="group" aria-label="새 대화 입력 취소 확인">
        <p>입력한 대화 이름을 버릴까요?</p>
        <button disabled={busy} onClick={() => { setCreating(false); setTitleDirty(false); setNewTitle("문서 검토"); setDiscardNew(false); }}>대화 이름 버리기</button>
        <button onClick={() => setDiscardNew(false)}>계속 작성</button>
      </div>}
      {switchRequest && <div role="group" aria-label="대화 전환 확인">
        <p>저장하지 않은 입력이 있습니다. 입력을 버리고 ‘{switchRequest.label}’로 이동할까요?</p>
        <button disabled={busy || assistantActive || createPending} onClick={() => {
          if (assistantActive || createPending) return;
          const next = switchRequest.id; setSwitchRequest(null);
          if (next === "__new__") void run(createThread);
          else changeThread(next);
        }}>입력 버리고 이동</button>
        <button disabled={busy} onClick={() => setSwitchRequest(null)}>현재 대화 유지</button>
      </div>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {detail && (
        <div className="conversation-content" inert={createPending}>
          <DocumentAssistantPanel active={active} key={`assistant-${detail.id}`} thread={detail.id} document={doc} captureDocument={captureDocument} sendAssistant={sendAssistant}
            onDirty={assistantDirtyChanged} onActive={assistantActiveChanged} requestRewrite={requestRewrite} onShowRewrite={() => setShowRewrite(true)} />
          <details className="assistant-secondary-tools" open={showRewrite} onToggle={event => setShowRewrite(event.currentTarget.open)}><summary>문단 수정 후보 · 직접 요청</summary>
          <RewritePanel key={detail.id} thread={detail.id} document={doc} context={detail.context} contextDirty={contextDirty}
            active={active && showRewrite} requestRewrite={requestRewrite} approveRewrite={approveRewrite} cancelRewrite={cancelRewrite} getPendingRewrite={getPendingRewrite} />
          </details>
          <details className="assistant-secondary-tools"><summary>작성 정보·검토 메모</summary>
          <details><summary>추가 작성 정보 · 선택 사항</summary>
          <WritingContextEditor key={`context-${detail.id}`} thread={detail.id} context={detail.context}
            baseVersion={doc.current_version} writable={writable} capture={capture} onSaved={() => load(detail.id)} onDirty={dirtyChanged} />
          </details>
          <h3>검토 메모</h3>
          <p className="hint">메모는 이 앱에 보관됩니다. 수정 후보에 필요한 내용은 추가 작성 정보에 입력하세요.</p>
          <div className="messages" aria-label="저장된 대화">
            {detail.messages.map((m) => (
              <article key={m.id}>
                <small>
                  {m.author_id} ·{" "}
                  {m.role === "assistant" && m.source === "document_ai" ? "문서 AI" : m.source === "model"
                    ? "설정된 AI"
                    : m.source === "codex"
                      ? "Codex"
                      : "사용자"}
                </small>
                <p className="text">{m.content}</p>
              </article>
            ))}
          </div>
          {writable && (
            <>
              <textarea
                aria-label="대화 메시지"
                placeholder="문서에 대한 의견이나 검토 내용을 남기세요."
                value={message}
                disabled={busy}
                onChange={(e) => setMessage(e.target.value)}
                maxLength={12000}
              />
              <button
                disabled={busy || !message.trim()}
                onClick={() =>
                  void run(async () => {
                    await api(
                      `/threads/${selected}/messages`,
                      jsonRequest("POST", {
                        request_id: crypto.randomUUID(),
                        content: message,
                      }),
                    );
                    setMessage("");
                    await load();
                  })
                }
              >
                메모 저장
              </button>
              <details>
                <summary>별도 AI 연결</summary>
                <CapabilityNotice feature="chat" label="별도 모델 AI 답변" />
                <p className="hint">관리자가 연결한 로컬·사내 모델에 질문하는 기능입니다. Codex 연결만으로는 사용할 수 없습니다. 질문할 때 저장된 작성 정보와 최근 대화가 전달됩니다.</p>
                <button disabled={busy || checkingConnection} onClick={() => void checkSeparateAi()}>{checkingConnection ? "연결 확인 중…" : "별도 AI 연결 확인"}</button>
                {separateAi && <p role={separateAi.ready ? "status" : "alert"}>{separateAi.ready ? `연결됨 · ${separateAi.model}` : separateAi.error}</p>}
              <button
                disabled={busy || checkingConnection || !separateAi?.ready || !message.trim() || !chatFeature.available}
                onClick={() =>
                  void run(async () => {
                    const row = await api<{ id: string }>(
                      `/threads/${selected}/messages`,
                      jsonRequest("POST", {
                        request_id: crypto.randomUUID(),
                        content: message,
                      }),
                    );
                    setMessage("");
                    await load();
                    try {
                      await api(
                        `/threads/${selected}/chat`,
                        jsonRequest("POST", { message_id: row.id }),
                      );
                    } finally {
                      await load();
                    }
                  })
                }
              >
                메모 내용으로 별도 AI에 질문
              </button>
              </details>
            </>
          )}
          {proposals.map((p) => (
            <article key={p.id}>
              <h3>Codex 문단 제안 {p.stale ? "· 오래됨" : ""}</h3>
              <p className="text">원문: {p.snapshot.evidence.text}</p>
              <p className="text">수정안: {p.replacement}</p>
              <p>{p.reason}</p>
              {approve && (
                <button
                  disabled={busy || p.stale}
                  onClick={() =>
                    void run(async () => {
                      await approve(selected, p.id);
                      setProposals((rows) =>
                        rows.filter((row) => row.id !== p.id),
                      );
                    })
                  }
                >
                  이 제안 승인하고 적용
                </button>
              )}
            </article>
          ))}
          <details><summary>연결 정보</summary><p>대화 ID: <code>{detail.id}</code></p></details>
          </details>
        </div>
      )}
    </section>
  );
}

export function AccessPanel({
  document: doc,
  onClose,
}: {
  document: Document;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  useComparisonDialog(dialog, onClose);
  const [rows, setRows] = useState<{ user_id: string; role: string }[]>([]),
    [user, setUser] = useState(""),
    [role, setRole] = useState("reader"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const load = async () => setRows(await api(`/documents/${doc.id}/access`));
  useEffect(() => {
    void load().catch((e) => setError(e.message));
  }, [doc.id]);
  async function change(task: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await task();
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="modal">
      <section ref={dialog} role="dialog" aria-modal="true" aria-labelledby="access-panel-title">
        <button className="close" onClick={onClose}>
          닫기
        </button>
        <h2 id="access-panel-title">{doc.name} · 공유 권한</h2>
        <p>
          열람자는 문서 공유 대화·컨텍스트·버전을 볼 수 있습니다. 편집자는
          저장·대화·제안을 작성합니다. 권한 변경 시 열린 편집 세션은 만료됩니다.
        </p>
        {rows.map((r) => (
          <p key={r.user_id}>
            {r.user_id} · {r.role}{" "}
            {r.role !== "owner" && (
              <button
                disabled={busy}
                onClick={() =>
                  void change(() =>
                    api(
                      `/documents/${doc.id}/access/${encodeURIComponent(r.user_id)}`,
                      { method: "DELETE" },
                    ),
                  )
                }
              >
                권한 회수
              </button>
            )}
          </p>
        ))}
        <input
          aria-label="공유 대상 사용자"
          placeholder="서버에 등록된 사용자 ID"
          value={user}
          onChange={(e) => setUser(e.target.value)}
        />
        <select
          aria-label="공유 권한"
          value={role}
          onChange={(e) => setRole(e.target.value)}
        >
          <option value="reader">열람</option>
          <option value="editor">편집</option>
        </select>
        <button
          disabled={busy || !user}
          onClick={() =>
            void change(() =>
              api(
                `/documents/${doc.id}/access`,
                jsonRequest("PUT", { user_id: user, role }),
              ),
            )
          }
        >
          권한 저장
        </button>
        {error && <p role="alert">{error}</p>}
      </section>
    </div>
  );
}
