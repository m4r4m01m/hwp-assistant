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
    [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const dirtyChanged = useCallback((dirty: boolean) => { setContextDirty(dirty); onContextDirty?.(dirty); }, [onContextDirty]);
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
  return (
    <section className="collaboration">
      <h2>문서 대화 · 컨텍스트</h2>
      <p>
        문서 접근 권한자는 작성자와 관계없이 이 문서의 모든 대화·후보·적용 이력을 볼 수 있습니다. 편집 권한은 별도로 적용됩니다.
      </p>
      <select
        aria-label="대화 선택"
        value={selected}
        onChange={(e) => {
          if (contextDirty && !confirm("저장하지 않은 작성 정보가 있습니다. 대화를 바꾸면 입력이 사라집니다. 계속할까요?")) return;
          dirtyChanged(false); setSelected(e.target.value);
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
                if (contextDirty && !confirm("저장하지 않은 작성 정보가 있습니다. 새 대화를 열까요?")) return;
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
                dirtyChanged(false); setSelected(row.id);
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
          <small>
            대화 ID: <code>{detail.id}</code>
          </small>
          <RewritePanel key={detail.id} thread={detail.id} document={doc} context={detail.context} contextDirty={contextDirty}
            requestRewrite={requestRewrite} approveRewrite={approveRewrite} cancelRewrite={cancelRewrite} getPendingRewrite={getPendingRewrite} />
          <WritingContextEditor key={`context-${detail.id}`} thread={detail.id} context={detail.context}
            baseVersion={doc.current_version} writable={writable} capture={capture} onSaved={() => load(detail.id)} onDirty={dirtyChanged} />
          <p className="hint">작성 정보와 대화는 이 앱에 저장됩니다. 후보 생성에는 선택한 작성 정보만 사용하며 전체 대화를 자동 전달하지 않습니다.</p>
          <div className="messages" aria-label="저장된 대화">
            {detail.messages.map((m) => (
              <article key={m.id}>
                <small>
                  {m.author_id} ·{" "}
                  {m.source === "model"
                    ? "설정된 AI"
                    : m.source === "codex"
                      ? "Codex/MCP"
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
                메시지 저장
              </button>
              <button
                disabled={busy || !message.trim()}
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
                설정된 AI에 질문
              </button>
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

export function Templates({
  onClose,
  onApply,
}: {
  onClose: () => void;
  onApply: (d: Document) => void;
}) {
  const [query, setQuery] = useState(""),
    [rows, setRows] = useState<
      {
        id: string;
        name: string;
        description: string;
        reason: string;
        version_id: string;
      }[]
    >([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const pending = useRef<{ id: string; body: object } | null>(null);
  useEffect(() => {
    let live = true;
    api<typeof rows>(`/templates?q=${encodeURIComponent(query)}`)
      .then((r) => {
        if (live) setRows(r);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [query]);
  return (
    <div className="modal">
      <section>
        <button className="close" onClick={onClose}>
          닫기
        </button>
        <h2>템플릿 추천 · 적용</h2>
        <p>
          접근 가능한 양식을 이름·설명·태그로 추천합니다. 적용하면 표·서식을
          포함한 해당 HWPX 버전으로 새 문서를 만듭니다.
        </p>
        <input
          aria-label="템플릿 검색"
          placeholder="회의록, 보고서, 계획 등"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {!rows.length && (
          <p>
            추천할 양식이 없습니다. 문서 목록에서 저장된 문서를 양식으로
            등록하세요.
          </p>
        )}
        {rows.map((t) => (
          <article key={t.id}>
            <h3>{t.name}</h3>
            <p>{t.description}</p>
            <small>
              {t.reason} · 기준 {t.version_id}
            </small>
            <button
              disabled={
                busy || (!!pending.current && pending.current.id !== t.id)
              }
              onClick={async () => {
                if (!pending.current) {
                  const name = prompt(
                    "양식으로 만들 문서 이름",
                    t.name + ".hwpx",
                  );
                  if (!name) return;
                  pending.current = {
                    id: t.id,
                    body: {
                      document_id: crypto.randomUUID(),
                      request_id: crypto.randomUUID(),
                      name,
                    },
                  };
                }
                setBusy(true);
                setError("");
                try {
                  const d = await api<Document>(
                    `/templates/${t.id}/apply`,
                    jsonRequest("POST", pending.current.body),
                  );
                  pending.current = null;
                  onApply(d);
                } catch (e) {
                  setError(
                    String(e) + " 같은 양식 버튼으로 재시도할 수 있습니다.",
                  );
                } finally {
                  setBusy(false);
                }
              }}
            >
              이 양식으로 새 문서
            </button>
          </article>
        ))}
        {error && <p role="alert">{error}</p>}
      </section>
    </div>
  );
}
