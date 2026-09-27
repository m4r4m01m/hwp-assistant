import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { EditorAdapter, type Snapshot, type ExportSnapshot } from "./editor";
import {
  api,
  upload,
  fileUrl,
  downloadBytes,
  deltaBody,
  jsonRequest,
  type Document,
  type Version,
} from "./api";
import "./style.css";
import { AccessPanel, CollaborationPanel, Templates } from "./collaboration";

function App({
  account,
}: {
  account: { user_id: string; authentication_required: boolean };
}) {
  const [docs, setDocs] = useState<Document[]>([]),
    [query, setQuery] = useState(""),
    [active, setActive] = useState<Document | null>(null),
    [creating, setCreating] = useState(false),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true),
    [history, setHistory] = useState<Document | null>(null),
    [sharing, setSharing] = useState<Document | null>(null),
    [templates, setTemplates] = useState(false);
  const refresh = () => {
    setLoading(true);
    api<Document[]>(`/documents?q=${encodeURIComponent(query)}`)
      .then(setDocs)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(refresh, [query]);
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
        <button onClick={() => setTemplates(true)}>템플릿 추천 · 적용</button>
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
        placeholder="파일명 검색"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
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
            {docs.map((d) => (
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
                      onClick={async () => {
                        const name = prompt(
                          "양식 이름",
                          d.name.replace(/\.hwpx$/i, ""),
                        );
                        if (!name) return;
                        const description =
                          prompt("양식 설명·추천 검색어", "") || "";
                        try {
                          await api(
                            "/templates",
                            jsonRequest("POST", {
                              document_id: d.id,
                              version_id: d.current_version,
                              name,
                              description,
                              tags: description
                                .split(/[,\s]+/)
                                .filter(Boolean)
                                .slice(0, 20),
                            }),
                          );
                          setTemplates(true);
                        } catch (e) {
                          setError(String(e));
                        }
                      }}
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
      {templates && (
        <Templates
          onClose={() => setTemplates(false)}
          onApply={(d) => {
            setTemplates(false);
            setActive(d);
          }}
        />
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
  const applied = useRef<{id: string; snapshot: ExportSnapshot; receipt: unknown} | null>(null);
  const [holdingReview, setHoldingReview] = useState(false);
  const [name] = useState(initial?.name || "새 문서.hwpx"),
    [status, setStatus] = useState("편집기 로딩 중"),
    [error, setError] = useState(""),
    [ready, setReady] = useState(false),
    [dirty, setDirty] = useState(false),
    [panel, setPanel] = useState(false),
    [conversationPanel, setConversationPanel] = useState(false),
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
  useEffect(() => {
    let disposed = false,
      off = () => {};
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
        instance = await EditorAdapter.mount(mount.current!);
        adapter.current = instance;
        if (disposed) {
          instance.destroy();
          return;
        }
        if (doc.current) {
          const response = await fetch(fileUrl(doc.current));
          if (!response.ok) throw new Error("문서 다운로드 실패");
          const bytes = await response.arrayBuffer();
          baseBytes.current = new Uint8Array(bytes);
          await instance.load(bytes, doc.current.name);
        } else await instance.bridge.newDocument();
        // Studio creates its genuine blank HWP document on startup; export is engine serialization.
        const state = await instance.sdk.getDocumentState();
        epoch.current = state.documentEpoch;
        savedRevision.current = initial ? instance.bridge.revision() : -1;
        setDirty(!initial);
        setStatus(initial ? "저장 완료" : "저장되지 않은 변경 있음");
        setReady(true);
        const warning = instance.bridge.warnings();
        if (warning && (warning as { count?: number }).count)
          setWarnings(JSON.stringify(warning));
        off = instance.bridge.subscribe(() => {
          dirtyRef.current = true;
          setDirty(true);
          if (!busy.current) setStatus("저장되지 않은 변경 있음");
        });
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
      if (dirtyRef.current || busy.current) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => {
      disposed = true;
      off();
      clearInterval(heartbeat);
      window.removeEventListener("beforeunload", guard);
      instance?.destroy();
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
    if (!a || busy.current || (!dirtyRef.current && !force)) return false;
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
      setStatus("저장 실패");
      setError(
        String(e) + " · 같은 저장 재시도 또는 복구용 다운로드를 사용하세요.",
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
      if (dirtyRef.current && !pending.current && !reviewHold.current) void saveRef.current();
    }, 20000);
    return () => clearInterval(timer);
  }, []);
  async function review() {
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
    <div className="workspace">
      <header>
        <button
          onClick={() => {
            if (
              !busy.current &&
              (!dirtyRef.current ||
                confirm(
                  "저장하지 않은 변경사항이 있습니다. 문서 목록으로 돌아갈까요?",
                ))
            )
              onClose();
          }}
        >
          ← 문서 목록
        </button>
        <strong>{name}</strong>
        <span role="status" className={status === "저장 실패" ? "bad" : ""}>
          {status}
        </span>
        <button
          disabled={!ready || status === "저장 중"}
          onClick={() => void save(true)}
        >
          저장
        </button>
        <button
          disabled={!ready}
          onClick={async () => {
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
        {doc.current && (
          <a className="button" href={fileUrl(doc.current)}>
            저장본 다운로드
          </a>
        )}
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
        <button onClick={() => setPanel(!panel)}>
          AI 검토 {panel ? "닫기" : "열기"}
        </button>
        <button
          disabled={!doc.current}
          onClick={() => setConversationPanel(!conversationPanel)}
        >
          대화 · Codex {conversationPanel ? "닫기" : "열기"}
        </button>
      </header>
      {error && (
        <div role="alert" className="error">
          {error}
        </div>
      )}
      <div className="notice">
        상단의 저장 버튼과 20초 간격 자동 저장이 서버에 새 버전을 만듭니다. AI
        적용은 단일 서식의 본문 문단 전체만 지원합니다.
      </div>
      {holdingReview && <div className="notice">후보 비교를 위해 자동 저장을 보류 중입니다. 수동 저장하면 기존 후보의 기준 버전이 오래될 수 있습니다.
        <button onClick={() => { reviewHold.current = false; setHoldingReview(false); }}>비교 종료 · 자동 저장 재개</button>
      </div>}
      {warnings && (
        <details className="warnings">
          <summary>엔진 열기 진단 / 호환성 안내</summary>
          <pre>{warnings}</pre>
        </details>
      )}
      <div className="editor-row">
        <div className="editor" ref={mount} />
        {conversationPanel && doc.current && (
          <aside className="conversation-panel">
            <CollaborationPanel
              document={doc.current}
              capture={() => adapter.current!.selection()}
              requestRewrite={async (thread, instruction) => {
                if (busy.current || pending.current) throw new Error("진행 중인 저장을 먼저 완료하세요.");
                reviewHold.current = true; setHoldingReview(true);
                const snapshot = adapter.current!.selection();
                await api(`/threads/${thread}/rewrite-tasks`, {
                  ...jsonRequest("POST", { id: crypto.randomUUID(), base_version: doc.current!.current_version,
                    expected_revision: 0, selection: snapshot, instruction }),
                  headers: { "Content-Type": "application/json", "X-Edit-Session": token.current },
                });
              }}
              approveRewrite={async (task, candidate, applicationId) => {
                if (busy.current || pending.current && pending.current.applicationId !== applicationId)
                  throw new Error("진행 중인 다른 저장을 먼저 완료하세요.");
                busy.current = true;
                try {
                  const approval = await api<{snapshot: Snapshot; replacement: string; status: string; saved_version: string | null}>(`/rewrite-tasks/${task}/approve`, {
                    ...jsonRequest("POST", { id: applicationId, candidate_id: candidate }),
                    headers: { "Content-Type": "application/json", "X-Edit-Session": token.current },
                  });
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
                if (!await save(true)) throw new Error("승인 적용은 보존됐지만 서버 저장이 실패했습니다. 같은 후보로 재시도하세요.");
                applied.current = null; reviewHold.current = false; setHoldingReview(false);
              }}
              approve={async (thread, proposal) => {
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
          <aside>
            <h2>문단 AI 검토</h2>
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
