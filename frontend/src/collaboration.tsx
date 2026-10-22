import { useCallback, useEffect, useRef, useState } from "react";
import { api, jsonRequest, type Document } from "./api";
import type { Snapshot } from "./editor";
import { RewritePanel, type PendingRewrite } from "./rewrite";
import { WritingContextEditor, type WritingContext } from "./context";

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
}: {
  document: Document;
  capture?: () => Snapshot;
  approve?: (thread: string, proposal: string) => Promise<void>;
  requestRewrite?: (thread: string, instruction: string, contextRevision?: number) => Promise<void>;
  approveRewrite?: (task: string, candidate: string, application: string) => Promise<void>;
  cancelRewrite?: (task: string) => Promise<void>;
  onContextDirty?: (dirty: boolean) => void;
  getPendingRewrite?: () => PendingRewrite | null;
}) {
  const [threads, setThreads] = useState<Thread[]>([]),
    [selected, setSelected] = useState(""),
    [detail, setDetail] = useState<Detail | null>(null),
    [proposals, setProposals] = useState<Proposal[]>([]),
    [contextDirty, setContextDirty] = useState(false),
    [message, setMessage] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [checkingConnection, setCheckingConnection] = useState(false),
    [separateAi, setSeparateAi] = useState<{ ready: boolean; model: string; error: string } | null>(null);
  const generation = useRef(0);
  const dirtyChanged = useCallback((dirty: boolean) => { setContextDirty(dirty); }, []);
  useEffect(() => { onContextDirty?.(contextDirty || message.length > 0); }, [contextDirty, message, onContextDirty]);
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
    void list().catch((e) => setError(e.message));
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
  return (
    <section className="collaboration">
      <h2>문장 수정과 검토 기록</h2>
      <p className="hint">검토별로 수정 후보와 메모를 보관합니다. 이 문서에 접근할 수 있는 사용자에게 공유됩니다.</p>
      <select
        aria-label="대화 선택"
        value={selected}
        onChange={(e) => {
          if ((contextDirty || message.length > 0) && !confirm("저장하지 않은 작성 정보나 메모가 있습니다. 대화를 바꾸면 입력이 사라집니다. 계속할까요?")) return;
          dirtyChanged(false); setMessage(""); setSelected(e.target.value);
        }}
        disabled={busy}
      >
        <option value="">대화를 선택하세요</option>
        {threads.map((t) => (
          <option key={t.id} value={t.id}>
            문서 대화 · {t.title}
          </option>
        ))}
      </select>
      {writable &&
        (["document"] as const).map((visibility) => (
          <button
            key={visibility}
            disabled={busy}
            onClick={() =>
              void run(async () => {
                if ((contextDirty || message.length > 0) && !confirm("저장하지 않은 작성 정보나 메모가 있습니다. 새 대화를 열면 입력이 사라집니다. 계속할까요?")) return;
                const title = prompt(
                  "대화 제목",
                  "문서 검토",
                );
                if (!title) return;
                const row = await api<Thread>(
                  `/documents/${doc.id}/threads`,
                  jsonRequest("POST", {
                    id: crypto.randomUUID(),
                    title,
                    visibility,
                  }),
                );
                await list();
                dirtyChanged(false); setMessage(""); setSelected(row.id);
              })
            }
          >
            공유 대화 만들기
          </button>
        ))}
      <button
        disabled={busy}
        onClick={() =>
          void run(async () => {
            await list();
            await load();
          })
        }
      >
        대화 새로고침
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {detail && (
        <>
          <RewritePanel key={detail.id} thread={detail.id} document={doc} context={detail.context} contextDirty={contextDirty}
            requestRewrite={requestRewrite} approveRewrite={approveRewrite} cancelRewrite={cancelRewrite} getPendingRewrite={getPendingRewrite} />
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
                  {m.source === "model"
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
                <p className="hint">관리자가 연결한 로컬·사내 모델에 질문하는 기능입니다. Codex 연결만으로는 사용할 수 없습니다. 질문할 때 저장된 작성 정보와 최근 대화가 전달됩니다.</p>
                <button disabled={busy || checkingConnection} onClick={() => void checkSeparateAi()}>{checkingConnection ? "연결 확인 중…" : "별도 AI 연결 확인"}</button>
                {separateAi && <p role={separateAi.ready ? "status" : "alert"}>{separateAi.ready ? `연결됨 · ${separateAi.model}` : separateAi.error}</p>}
              <button
                disabled={busy || checkingConnection || !separateAi?.ready || !message.trim()}
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
        </>
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
      <section>
        <button className="close" onClick={onClose}>
          닫기
        </button>
        <h2>{doc.name} · 공유 권한</h2>
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
