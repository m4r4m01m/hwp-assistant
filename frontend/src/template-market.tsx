import { useEffect, useRef, useState } from "react";
import { api, ApiError, jsonRequest, type Document, type Version } from "./api";
import { EditorAdapter } from "./editor";
import { previewSvg } from "./preview-svg";
import { WorkspaceNav, WorkspaceIcon, type WorkspaceSection } from "./workspace-nav";
import { publicPath } from "./public-path";

type Template = {
  id: string;
  name: string;
  description: string;
  tags: string[];
  document_id: string;
  version_id: string;
  category: string;
  revision: number;
  favorite: boolean;
  can_manage: boolean;
  created_at: number;
  sha256: string;
  size: number;
  source_name: string;
};
type Catalog = {
  items: Template[];
  total: number;
  categories: { name: string; count: number }[];
};
type Fields = {
  name: string;
  description: string;
  tags: string;
  category: string;
};
type Pending = {
  kind: "register" | "apply" | "edit";
  template?: string;
  body: Record<string, unknown>;
};
const emptyFields = (): Fields => ({
  name: "",
  description: "",
  tags: "",
  category: "기타",
});
const fieldsOf = (t: Template): Fields => ({
  name: t.name,
  description: t.description,
  tags: t.tags.join(", "),
  category: t.category,
});
const payload = (fields: Fields) => ({
  name: fields.name.trim(),
  description: fields.description.trim(),
  tags: [
    ...new Set(
      fields.tags
        .split(/[,\n]+/)
        .map((t) => t.trim())
        .filter(Boolean),
    ),
  ],
  category: fields.category.trim() || "기타",
});
const categories = ["보고서", "회의록", "계획서", "점검표", "기타"];
function PinnedPreview({ item }: { item: Template }) {
  const host = useRef<HTMLDivElement>(null);
  const [pages, setPages] = useState<string[]>([]),
    [count, setCount] = useState(0),
    [busy, setBusy] = useState(true),
    [error, setError] = useState("");
  const identity = `${item.id}:${item.version_id}:${item.sha256}`;
  useEffect(() => {
    const controller = new AbortController();
    let live = true,
      adapter: EditorAdapter | null = null;
    const urls: string[] = [];
    setPages([]);
    setCount(0);
    setBusy(true);
    setError("");
    async function load() {
      if (!host.current) return;
      const url = publicPath(`api/template-market/${item.id}/file`);
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok)
        throw new Error(
          response.status === 403 || response.status === 404
            ? "현재 이 양식을 열람할 수 없습니다. 접근 권한을 확인하세요."
            : "양식 파일을 불러오지 못했습니다.",
        );
      const bytes = await response.arrayBuffer();
      const digest = Array.from(
        new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
      )
        .map((n) => n.toString(16).padStart(2, "0"))
        .join("");
      if (digest !== item.sha256)
        throw new Error("등록된 고정 버전과 파일 해시가 다릅니다.");
      if (!live) return;
      const mounted = await EditorAdapter.mount(host.current!);
      if (!live) {
        mounted.sdk.destroy();
        return;
      }
      adapter = mounted;
      await adapter.load(bytes, item.source_name);
      const total = await adapter.sdk.pageCount();
      for (let page = 0; page < Math.min(total, 6); page++) {
        const svg = previewSvg(await adapter.sdk.getPageSvg(page));
        if (!live) return;
        urls.push(
          URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" })),
        );
      }
      if (live) {
        setPages([...urls]);
        setCount(total);
      }
    }
    void load()
      .catch((e) => {
        if (live && !controller.signal.aborted) {
          setPages([]);
          setError(String(e));
        }
      })
      .finally(() => {
        adapter?.sdk.destroy();
        adapter = null;
        if (live) setBusy(false);
      });
    return () => {
      live = false;
      controller.abort();
      adapter?.sdk.destroy();
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [identity]);
  return (
    <section className="market-preview" aria-label="양식 HWPX 미리보기">
      <div className="market-renderer" ref={host} aria-hidden="true" />
      {busy && <p role="status">한글 엔진으로 양식을 불러오는 중…</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {pages.map((src, i) => (
        <figure key={src}>
          <img src={src} alt={`양식 미리보기 ${i + 1}쪽`} />
          <figcaption>
            {i + 1} / {count}쪽
          </figcaption>
        </figure>
      ))}
      {!busy && count > 6 && <p>전체 {count}쪽 중 앞 6쪽을 표시했습니다.</p>}
    </section>
  );
}

export function TemplateMarket({
  onClose,
  onApply,
  onReport,
  onDesignStandard,
  registerDocument,
  onNavigate,
}: {
  onClose: () => void;
  onApply: (d: Document) => void;
  onReport: () => void;
  onDesignStandard?: () => void;
  registerDocument?: Document;
  onNavigate?: (section: WorkspaceSection) => void;
}) {
  const [catalog, setCatalog] = useState<Catalog>({
      items: [],
      total: 0,
      categories: [],
    }),
    [query, setQuery] = useState(""),
    [category, setCategory] = useState(""),
    [favorite, setFavorite] = useState(false),
    [sort, setSort] = useState("newest"),
    [offset, setOffset] = useState(0),
    [refresh, setRefresh] = useState(0),
    [selected, setSelected] = useState<Template | null>(null),
    [registering, setRegistering] = useState(!!registerDocument),
    [docs, setDocs] = useState<Document[]>(
      registerDocument ? [registerDocument] : [],
    ),
    [source, setSource] = useState(registerDocument?.id || ""),
    [versions, setVersions] = useState<Version[]>([]),
    [version, setVersion] = useState(registerDocument?.current_version || ""),
    [fields, setFields] = useState<Fields>(() => ({
      ...emptyFields(),
      name: registerDocument?.name.replace(/\.hwpx$/i, "") || "",
    })),
    [editing, setEditing] = useState(false),
    [applyName, setApplyName] = useState(""),
    [pending, setPending] = useState<Pending | null>(null),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const frozen = useRef<Pending | null>(null),
    operationBusy = useRef(false),
    detailGeneration = useRef(0);
  const locked = busy || !!pending;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const params = new URLSearchParams({
      q: query,
      favorite: String(favorite),
      sort,
      offset: String(offset),
      limit: "24",
    });
    if (category) params.set("category", category);
    void api<Catalog>(`/template-market?${params}`, {
      signal: controller.signal,
    })
      .then((rows) => {
        if (!controller.signal.aborted) setCatalog(rows);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(String(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [query, category, favorite, sort, offset, refresh]);
  useEffect(() => {
    if (!registering) return;
    const controller = new AbortController();
    void api<Document[]>("/documents", { signal: controller.signal })
      .then((rows) => {
        if (!controller.signal.aborted)
          setDocs(rows.filter((d) => d.role !== "reader"));
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(String(e));
      });
    return () => controller.abort();
  }, [registering]);
  useEffect(() => {
    if (!registering || !source) {
      setVersions([]);
      return;
    }
    const controller = new AbortController();
    setVersions([]);
    void api<Version[]>(`/documents/${source}/versions`, {
      signal: controller.signal,
    })
      .then((rows) => {
        if (controller.signal.aborted) return;
        setVersions(rows);
        setVersion((current) =>
          rows.some((v) => v.id === current)
            ? current
            : docs.find((d) => d.id === source)?.current_version ||
              rows[0]?.id ||
              "",
        );
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setVersion("");
          setError(String(e));
        }
      });
    return () => controller.abort();
  }, [registering, source]);
  async function select(t: Template) {
    if (locked || operationBusy.current) return;
    const generation = ++detailGeneration.current;
    setSelected(null);
    setEditing(false);
    setError("");
    setNotice("");
    try {
      const detail = await api<Template>(`/template-market/${t.id}`);
      if (generation === detailGeneration.current) {
        setSelected(detail);
        setApplyName(detail.name.replace(/\.hwpx$/i, "") + ".hwpx");
      }
    } catch (e) {
      if (generation === detailGeneration.current) setError(String(e));
    }
  }
  function clearPending() {
    frozen.current = null;
    setPending(null);
  }
  async function execute(candidate: Pending) {
    if (operationBusy.current) return;
    const retrying = !!frozen.current;
    const task = frozen.current || candidate;
    frozen.current = task;
    setPending(task);
    operationBusy.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (task.kind === "apply") {
        const doc = await api<Document>(
          `/templates/${task.template}/apply`,
          jsonRequest("POST", task.body),
        );
        clearPending();
        onApply(doc);
      } else if (task.kind === "register") {
        const result = await api<{ id: string; version_id: string }>(
          "/templates",
          jsonRequest("POST", task.body),
        );
        clearPending();
        setRegistering(false);
        setRefresh((n) => n + 1);
        setNotice("저장된 고정 버전을 양식으로 등록했습니다.");
        const detail = await api<Template>(`/template-market/${result.id}`);
        setSelected(detail);
        setApplyName(detail.name.replace(/\.hwpx$/i, "") + ".hwpx");
      } else {
        if (retrying) {
          const current = await api<Template>(
            `/template-market/${task.template}`,
          );
          const desired = task.body;
          if (
            current.name === desired.name &&
            current.description === desired.description &&
            current.category === desired.category &&
            JSON.stringify(current.tags) === JSON.stringify(desired.tags)
          ) {
            clearPending();
            setSelected(current);
            setEditing(false);
            setRefresh((n) => n + 1);
            setNotice("최신 양식 정보가 저장 요청과 일치함을 확인했습니다.");
            return;
          }
        }
        const item = await api<Template>(
          `/template-market/${task.template}`,
          jsonRequest("PUT", task.body),
        );
        clearPending();
        setSelected(item);
        setEditing(false);
        setRefresh((n) => n + 1);
        setNotice("양식 정보를 저장했습니다.");
      }
    } catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        clearPending();
        if (e.status === 403 || e.status === 404) {
          detailGeneration.current++;
          setSelected(null);
          setEditing(false);
        }
      }
      setError(
        `${String(e)}${frozen.current ? " · 결과가 아직 확인되지 않았습니다. 같은 입력으로 재확인해 주세요." : " · 입력이나 현재 접근 권한을 확인해 주세요."}`,
      );
    } finally {
      operationBusy.current = false;
      setBusy(false);
    }
  }
  async function toggleFavorite(t: Template) {
    if (locked || operationBusy.current) return;
    operationBusy.current = true;
    setBusy(true);
    setError("");
    try {
      const updated = await api<Template>(
        `/template-market/${t.id}/favorite`,
        jsonRequest("PUT", { favorite: !t.favorite }),
      );
      setCatalog((current) => ({
        ...current,
        items: current.items.map((item) =>
          item.id === updated.id ? updated : item,
        ),
      }));
      if (selected?.id === updated.id) setSelected(updated);
      setRefresh((n) => n + 1);
    } catch (e) {
      setError(String(e));
    } finally {
      operationBusy.current = false;
      setBusy(false);
    }
  }
  function registration() {
    if (locked || operationBusy.current) return;
    detailGeneration.current++;
    setSelected(null);
    setRegistering(true);
    setEditing(false);
    setFields(emptyFields());
    setSource("");
    setVersion("");
    setError("");
    setNotice("");
  }
  const fieldInputs = (
    <>
      <label>
        양식 이름
        <input
          aria-label="양식 이름"
          maxLength={120}
          value={fields.name}
          disabled={locked}
          onChange={(e) => setFields({ ...fields, name: e.target.value })}
          required
        />
      </label>
      <label>
        양식 설명
        <textarea
          aria-label="양식 설명"
          maxLength={1000}
          value={fields.description}
          disabled={locked}
          onChange={(e) =>
            setFields({ ...fields, description: e.target.value })
          }
        />
      </label>
      <label>
        분류
        <select
          aria-label="양식 분류"
          value={fields.category}
          disabled={locked}
          onChange={(e) => setFields({ ...fields, category: e.target.value })}
        >
          {categories.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <label>
        태그
        <input
          aria-label="양식 태그"
          placeholder="쉼표로 구분: 회의, 결정, 기록"
          maxLength={500}
          value={fields.tags}
          disabled={locked}
          onChange={(e) => setFields({ ...fields, tags: e.target.value })}
        />
      </label>
    </>
  );
  return (
    <div className="workspace-shell market-shell">
      <WorkspaceNav active="templates" onNavigate={onNavigate} onBack={onClose} backLabel="← 문서함" disabled={locked || registering || editing}>
        <button className="primary workspace-nav-action" disabled={locked || registering || editing} onClick={registration}><WorkspaceIcon name="plus"/><span>양식 등록</span></button>
      </WorkspaceNav>
    <main className="template-market market-workspace" data-detail-open={!!selected || registering}>
      <header>
        <div>
          <h1>양식 보관함</h1>
          <p>
            저장된 문서 양식으로 새 문서를 시작합니다.
          </p>
        </div>
        <button className="market-mobile-back" disabled={locked || editing} onClick={() => { detailGeneration.current++; setSelected(null); setRegistering(false); setEditing(false); }}>양식 목록으로</button>
      </header>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {pending && (
        <div className="notice market-pending">
          <strong>
            {pending.kind === "apply"
              ? "새 문서 생성"
              : pending.kind === "register"
                ? "양식 등록"
                : "정보 저장"}{" "}
            결과 확인 대기
          </strong>
          <p>
            입력과 요청 ID를 유지하고 있습니다. 결과를 확인한 뒤 다른 작업을
            진행할 수 있습니다.
          </p>
          <button disabled={busy} onClick={() => void execute(pending)}>
            같은 요청 재확인
          </button>
        </div>
      )}
      <div className="market-layout">
        <section className="market-catalog" aria-label="양식 목록">
          <div className="market-filters">
            <input
              aria-label="양식 검색"
              placeholder="이름, 설명, 태그 검색"
              value={query}
              disabled={locked}
              onChange={(e) => {
                setQuery(e.target.value);
                setOffset(0);
              }}
            />
            <select
              aria-label="양식 분류 필터"
              value={category}
              disabled={locked}
              onChange={(e) => {
                setCategory(e.target.value);
                setOffset(0);
              }}
            >
              <option value="">모든 분류</option>
              {catalog.categories.map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name} ({c.count})
                </option>
              ))}
            </select>
            <select
              aria-label="양식 정렬"
              value={sort}
              disabled={locked}
              onChange={(e) => {
                setSort(e.target.value);
                setOffset(0);
              }}
            >
              <option value="newest">최신 등록</option>
              <option value="name">이름순</option>
              <option value="relevance">검색 관련도</option>
            </select>
            <label className="market-check">
              <input
                type="checkbox"
                checked={favorite}
                disabled={locked}
                onChange={(e) => {
                  setFavorite(e.target.checked);
                  setOffset(0);
                }}
              />
              즐겨찾기만
            </label>
          </div>
          <p className="market-count">
            양식 {catalog.total}개 {loading && "· 불러오는 중…"}
          </p>
          {!loading && !catalog.items.length && (
            <div className="market-empty">
              <h2>{query || category || favorite ? "조건에 맞는 양식이 없습니다" : "등록된 양식이 없습니다"}</h2>
              <p>{query || category || favorite ? "검색어나 분류, 즐겨찾기 조건을 바꿔 보세요." : "문서함에 저장한 문서를 양식으로 등록하면 여기서 다시 사용할 수 있습니다."}</p>
              {query || category || favorite ? <button disabled={locked} onClick={() => { setQuery(""); setCategory(""); setFavorite(false); setOffset(0); }}>검색 조건 지우기</button> : <button disabled={locked} onClick={registration}>양식 등록하기</button>}
            </div>
          )}
          <div className="market-cards">
            {catalog.items.map((t) => (
              <article
                className={`market-card${selected?.id === t.id ? " selected" : ""}`}
                key={t.id}
              >
                <div className="market-card-top">
                  <span className="market-badge">{t.category}</span>
                  <button
                    className="market-star"
                    aria-label={`${t.name} 즐겨찾기`}
                    aria-pressed={t.favorite}
                    disabled={locked}
                    onClick={() => void toggleFavorite(t)}
                  >
                    {t.favorite ? "★" : "☆"}
                  </button>
                </div>
                <h2>{t.name}</h2>
                <p>{t.description || "설명이 없는 양식"}</p>
                <div className="market-tags">
                  {t.tags.slice(0, 6).map((tag) => (
                    <span key={tag}>#{tag}</span>
                  ))}
                </div>
                <small>
                  {new Date(t.created_at * 1000).toLocaleDateString("ko-KR")} ·{" "}
                  {Math.ceil(t.size / 1024)} KB
                </small>
                <button disabled={locked || registering || editing} onClick={() => void select(t)}>
                  양식 보기
                </button>
              </article>
            ))}
          </div>
          {catalog.total > 24 && <nav className="market-pages" aria-label="양식 목록 페이지">
            <button
              disabled={locked || loading || offset === 0}
              onClick={() => setOffset(Math.max(0, offset - 24))}
            >
              이전
            </button>
            <span>
              {Math.floor(offset / 24) + 1} /{" "}
              {Math.max(1, Math.ceil(catalog.total / 24))}
            </span>
            <button
              disabled={locked || loading || offset + 24 >= catalog.total}
              onClick={() => setOffset(offset + 24)}
            >
              다음
            </button>
          </nav>}
        </section>
        <section className="market-detail" aria-label="양식 상세">
          {registering ? (
            <form
              className="market-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (
                  !source ||
                  !version ||
                  !fields.name.trim() ||
                  !versions.some((v) => v.id === version)
                )
                  return;
                void execute({
                  kind: "register",
                  body: {
                    id: crypto.randomUUID(),
                    document_id: source,
                    version_id: version,
                    ...payload(fields),
                  },
                });
              }}
            >
              <h2>양식 등록</h2>
              <p>
                선택한 저장본의 내용과 서식을 함께 보관합니다. 미저장 편집을 포함하려면 먼저 문서를 저장하세요.
              </p>
              <label>
                원본 문서
                <select
                  aria-label="양식 원본 문서"
                  value={source}
                  disabled={locked}
                  onChange={(e) => {
                    const doc = docs.find((d) => d.id === e.target.value);
                    setSource(e.target.value);
                    setVersion(doc?.current_version || "");
                    setFields({
                      ...fields,
                      name: doc?.name.replace(/\.hwpx$/i, "") || fields.name,
                    });
                  }}
                  required
                >
                  <option value="">문서를 선택하세요</option>
                  {docs.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                사용할 저장본
                <select
                  aria-label="양식 고정 버전"
                  value={version}
                  disabled={locked || !versions.length}
                  onChange={(e) => setVersion(e.target.value)}
                  required
                >
                  {versions.map((v) => (
                    <option key={v.id} value={v.id}>
                      {new Date(v.created_at * 1000).toLocaleString("ko-KR")} ·{" "}
                      {v.id.slice(0, 8)}
                      {docs.find((d) => d.id === source)?.current_version ===
                      v.id
                        ? " (현재 저장본)"
                        : ""}
                    </option>
                  ))}
                </select>
              </label>
              {fieldInputs}
              <div className="market-actions">
                <button
                  className="primary"
                  type="submit"
                  disabled={
                    locked ||
                    !source ||
                    !version ||
                    !fields.name.trim() ||
                    !versions.some((v) => v.id === version)
                  }
                >
                  이 저장본을 양식으로 등록
                </button>
                <button
                  type="button"
                  disabled={locked}
                  onClick={() => setRegistering(false)}
                >
                  취소
                </button>
              </div>
            </form>
          ) : selected ? (
            <>
              <div className="market-detail-heading">
                <span className="market-badge">{selected.category}</span>
                <h2>{selected.name}</h2>
                <p>{selected.description}</p>
                <small>
                  원본{" "}
                  {selected.source_name}
                </small>
              </div>
              {editing ? (
                <form
                  className="market-form"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void execute({
                      kind: "edit",
                      template: selected.id,
                      body: {
                        ...payload(fields),
                        expected_revision: selected.revision,
                      },
                    });
                  }}
                >
                  <h3>양식 정보 편집</h3>
                  {fieldInputs}
                  <div className="market-actions">
                    <button
                      className="primary"
                      type="submit"
                      disabled={locked || !fields.name.trim()}
                    >
                      양식 정보 저장
                    </button>
                    <button
                      type="button"
                      disabled={locked}
                      onClick={() => setEditing(false)}
                    >
                      편집 취소
                    </button>
                    <button
                      type="button"
                      disabled={locked}
                      onClick={async () => {
                        try {
                          const current = await api<Template>(
                            `/template-market/${selected.id}`,
                          );
                          setSelected(current);
                          setFields(fieldsOf(current));
                          setError("");
                          setNotice(
                            "최신 양식 정보를 불러왔습니다. 내용을 확인하고 저장하세요.",
                          );
                        } catch (e) {
                          setError(String(e));
                        }
                      }}
                    >
                      최신 정보 불러오기
                    </button>
                  </div>
                </form>
              ) : (
                <div className="market-use">
                  <label>
                    새 문서 이름
                    <input
                      aria-label="양식 새 문서 이름"
                      value={applyName}
                      maxLength={180}
                      disabled={locked}
                      onChange={(e) => setApplyName(e.target.value)}
                    />
                  </label>
                  <div className="market-actions">
                    <button
                      className="primary"
                      disabled={locked || !applyName.trim()}
                      onClick={() =>
                        void execute({
                          kind: "apply",
                          template: selected.id,
                          body: {
                            document_id: crypto.randomUUID(),
                            request_id: crypto.randomUUID(),
                            name: applyName.trim(),
                          },
                        })
                      }
                    >
                      이 양식으로 새 문서
                    </button>
                    {selected.can_manage && (
                      <button
                        disabled={locked}
                        onClick={() => {
                          setFields(fieldsOf(selected));
                          setEditing(true);
                        }}
                      >
                        양식 정보 편집
                      </button>
                    )}
                  </div>
                  <p className="market-hint">
                    저장된 내용과 서식을 복사해 새 문서를 만듭니다. 새 문서의 내용은 직접 편집할 수 있습니다.
                  </p>
                </div>
              )}
              <PinnedPreview
                key={`${selected.id}:${selected.version_id}`}
                item={selected}
              />
            </>
          ) : (
            <div className="market-detail-empty">
              <h2>양식을 선택하세요</h2>
              <p>내용과 서식을 미리 보고 새 문서로 복사할 수 있습니다.</p>
              <details className="market-help"><summary>AI 보고서의 양식을 바꾸려면</summary><p>여백·글자·표 설정은 ‘문서 표준 편집’에서 저장하고, ‘보고서 작성’에서 선택합니다.</p><div className="market-actions">{onDesignStandard && <button disabled={locked || registering || editing} onClick={onDesignStandard}>표준 설정 편집</button>}<button disabled={locked || registering || editing} onClick={onReport}>보고서 작성으로</button></div></details>
            </div>
          )}
        </section>
      </div>
    </main></div>
  );
}
