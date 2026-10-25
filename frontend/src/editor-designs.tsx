import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, jsonRequest } from "./api";
import { EditorAdapter, type StyleTarget } from "./editor";
import { BlockFenceError, blockHash, blockJson, safeSvg } from "./block-insert";
import type { TocCapture, PreparedToc } from "./toc";
import "./editor-designs.css";

export type DesignChoice = {
  kind: "design" | "standard";
  id: string;
  sha256: string;
};
export type DesignItem = {
  id: string;
  sha256: string;
  name: string;
  kind?: "table" | "title";
  settings: Record<string, string | number | boolean>;
};
export type DesignCatalog = { designs: DesignItem[]; standards: DesignItem[] };
export type DesignCapture = TocCapture & { styleTarget: StyleTarget | null };
export type PreparedDesign = PreparedToc & {
  capture: DesignCapture;
  target: StyleTarget | { kind: "document"; section: 0 };
};
export type DesignIntent = {
  choice: DesignChoice;
  expected?: {
    source_sha256: string;
    revision: number;
    epoch: number;
    base_version: string;
    target: PreparedDesign["target"];
  };
  nonce: string;
};
type Preview = {
  request_id: string;
  input_sha256: string;
  source_sha256: string;
  output_sha256: string;
  output_base64: string;
  warnings: string[];
  summary: Record<string, unknown>;
};
type PagePair = { before: string; after: string; page: number };
type DesignTask = {
  capture: DesignCapture;
  body: Record<string, unknown>;
  preview?: Preview;
  prepared?: PreparedDesign;
  verified?: boolean;
  counts?: { before: number; after: number };
};
function PreviewPair({ pair }: { pair: PagePair }) {
  return (
    <div className="editor-design-pair">
      <figure>
        <figcaption>적용 전 · {pair.page}쪽</figcaption>
        {pair.before ? (
          <img src={pair.before} alt="적용 전 실제 문서" />
        ) : (
          <p>해당 쪽 없음</p>
        )}
      </figure>
      <figure>
        <figcaption>적용 후 · {pair.page}쪽</figcaption>
        {pair.after ? (
          <img src={pair.after} alt="적용 후 실제 문서" />
        ) : (
          <p>해당 쪽 없음</p>
        )}
      </figure>
    </div>
  );
}
function LargeDesignPreview({
  pair,
  onClose,
}: {
  pair: PagePair;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="editor-design-large"
      aria-label="서식 적용 전후 크게 비교"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2>서식 적용 전후 · {pair.page}쪽</h2>
        <button autoFocus onClick={onClose}>
          비교 닫기
        </button>
      </header>
      <PreviewPair pair={pair} />
    </dialog>
  );
}
const bytes = (value: string) =>
  Uint8Array.from(atob(value), (c) => c.charCodeAt(0));
function encoded(value: Uint8Array) {
  const parts: string[] = [];
  for (let i = 0; i < value.length; i += 8192)
    parts.push(String.fromCharCode(...value.subarray(i, i + 8192)));
  return btoa(parts.join(""));
}

// A bounded illustrative card; the selected document's actual engine output is
// available separately. No saved document or standard is changed by this card.
export function DesignExample({ item }: { item: DesignItem }) {
  const s = item.settings;
  const color = (key: string, fallback: string) =>
    /^#[0-9a-f]{6}$/i.test(String(s[key])) ? String(s[key]) : fallback;
  if (item.kind === "table")
    return (
      <svg viewBox="0 0 300 140" role="img" aria-label={`${item.name} 표 예시`}>
        <rect
          x="12"
          y="22"
          width="276"
          height="96"
          fill="white"
          stroke={color("table_border_color", "#67788a")}
        />
        <rect
          x="12"
          y="22"
          width="276"
          height="30"
          fill={color("table_header_fill", "#e6eff7")}
        />
        {[104, 196].map((x) => (
          <path
            key={x}
            d={`M${x} 22V118`}
            stroke={color("table_border_color", "#67788a")}
          />
        ))}
        {[52, 85].map((y) => (
          <path
            key={y}
            d={`M12 ${y}H288`}
            stroke={color("table_border_color", "#67788a")}
          />
        ))}
        {[
          ["항목", "담당", "일정"],
          ["현황 확인", "운영팀", "이번 주"],
          ["조치 완료", "담당자", "다음 주"],
        ].map((row, y) =>
          row.map((value, x) => (
            <text
              key={`${x}:${y}`}
              x={58 + x * 92}
              y={42 + y * 32}
              textAnchor="middle"
              fontSize="12"
              fontWeight={y === 0 ? 600 : 400}
              fill={color("table_color", "#233548")}
            >
              {value}
            </text>
          )),
        )}
      </svg>
    );
  if (item.kind === "title")
    return (
      <svg
        viewBox="0 0 300 140"
        role="img"
        aria-label={`${item.name} 제목 예시`}
      >
        <path
          d="M16 38H284M16 98H284"
          stroke={color("title_rule_color", "#2e75b6")}
          strokeWidth="2"
        />
        <text
          x="150"
          y="77"
          textAnchor="middle"
          fontSize="21"
          fontWeight="700"
          fill={color("title_color", "#172d42")}
        >
          업무 추진 계획
        </text>
      </svg>
    );
  return (
    <svg
      viewBox="0 0 300 140"
      role="img"
      aria-label={`${item.name} 문서 서식 예시`}
    >
      <rect
        x="94"
        y="8"
        width="112"
        height="124"
        rx="2"
        fill="white"
        stroke="#c4d2df"
      />
      <path
        d="M109 28H191M109 44H191"
        stroke={color("title_rule_color", "#2e75b6")}
      />
      <text x="150" y="39" textAnchor="middle" fontSize="8" fill="#233548">
        업무 추진 계획
      </text>
      {[62, 82, 102].map((y, i) => (
        <g key={y}>
          <text x="108" y={y} fontSize="8" fill="#334c62">
            {["□", "ㅇ", "-"][i]}
          </text>
          <path d={`M120 ${y - 3}H190M120 ${y + 4}H182`} stroke="#94a6b7" />
        </g>
      ))}
    </svg>
  );
}

export function EditorDesignShelf({
  catalog,
  loading = false,
  onUse,
}: {
  catalog: DesignCatalog | null;
  loading?: boolean;
  onUse?: (item: DesignItem) => void;
}) {
  return (
    <section className="editor-design-shelf" aria-label="표·제목 예시" aria-busy={loading}>
      <p>
        표나 제목의 모양을 고른 뒤 문서 편집에서 적용할 위치를 선택하세요. 본문
        내용은 유지합니다.
      </p>
      {loading && <p role="status">디자인 불러오는 중…</p>}
      <div className="editor-design-grid">
        {catalog?.designs.map((item) => (
          <button
            className="editor-design-card"
            key={item.id}
            onClick={() => onUse?.(item)}
            disabled={!onUse || loading}
          >
            <DesignExample item={item} />
            <strong>{item.name}</strong>
            <small>
              {item.kind === "table" ? "표" : "제목"} 디자인 · 문서에서 사용
            </small>
          </button>
        ))}
      </div>
      <p className="editor-design-help">
        카드는 모양 예시입니다. 글꼴·폭·줄바꿈을 포함한 실제 결과는 문서에서
        확인합니다.
      </p>
    </section>
  );
}

export function EditorDesignPanel({
  capture,
  isOwner,
  release,
  adopt,
  getStagingHost,
  onClose,
  intent,
  onAskAi,
}: {
  capture: () => DesignCapture;
  isOwner: (value: DesignCapture) => boolean;
  release: (value: DesignCapture) => void;
  adopt: (value: PreparedDesign) => Promise<void>;
  getStagingHost: () => HTMLDivElement;
  onClose: () => void;
  intent?: DesignIntent | null;
  onAskAi?: () => void;
}) {
  const [catalog, setCatalog] = useState<DesignCatalog | null>(null),
    [mode, setMode] = useState<"design" | "standard">("design"),
    [filter, setFilter] = useState<"all" | "title" | "table">("all");
  const [phase, setPhase] = useState("idle"),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [warnings, setWarnings] = useState<string[]>([]);
  const [selected, setSelected] = useState<DesignChoice | null>(
      intent?.choice || null,
    ),
    [images, setImages] = useState<PagePair[]>([]);
  const [page, setPage] = useState(0),
    [pageCount, setPageCount] = useState(0),
    [paging, setPaging] = useState(false),
    [zoom, setZoom] = useState(false);
  const active = useRef(true),
    running = useRef(false),
    controller = useRef<AbortController | null>(null),
    staging = useRef<EditorAdapter | null>(null);
  const frozen = useRef<DesignTask | null>(null);
  const urls = useRef<string[]>([]);
  const previewedIntent = useRef<string | null>(null);
  const reset = () => {
    controller.current?.abort();
    controller.current = null;
    staging.current?.destroy();
    staging.current = null;
    if (frozen.current) release(frozen.current.capture);
    frozen.current = null;
    urls.current.forEach(URL.revokeObjectURL);
    urls.current = [];
  };
  useEffect(() => {
    active.current = true;
    const abort = new AbortController();
    void api<DesignCatalog>("/editor-designs", { signal: abort.signal })
      .then(setCatalog)
      .catch((e) => {
        if (!abort.signal.aborted) setError(errorMessage(e));
      });
    return () => {
      active.current = false;
      abort.abort();
      reset();
    };
  }, []);
  useEffect(() => {
    if (intent) {
      setSelected(intent.choice);
      setMode(intent.choice.kind);
    }
  }, [intent?.nonce]);
  useEffect(() => {
    if (
      intent?.expected &&
      catalog &&
      previewedIntent.current !== intent.nonce &&
      !running.current &&
      !frozen.current
    ) {
      previewedIntent.current = intent.nonce;
      void prepare(intent.choice);
    }
  }, [intent?.nonce, catalog]);
  const owner = (value: NonNullable<typeof frozen.current>) => {
    if (!active.current || frozen.current !== value || !isOwner(value.capture))
      throw new BlockFenceError(
        "문서 세션이 바뀌었습니다. 현재 문서에서 다시 선택하세요.",
      );
  };
  const cancel = () => {
    reset();
    setImages([]);
    setWarnings([]);
    setZoom(false);
    setPhase("idle");
    setError("");
  };
  async function renderPage(task: DesignTask, index: number) {
    owner(task);
    if (
      !task.prepared ||
      !task.counts ||
      !Number.isSafeInteger(index) ||
      index < 0 ||
      index >= Math.max(task.counts.before, task.counts.after)
    )
      throw new BlockFenceError("미리보기 쪽을 확인할 수 없습니다.");
    let before = "",
      after = "";
    if (index < task.counts.before) {
      const svg = await task.capture.adapter.sdk.getPageSvg(index);
      owner(task);
      before = URL.createObjectURL(
        new Blob([safeSvg(svg)], {
          type: "image/svg+xml",
        }),
      );
      urls.current.push(before);
    }
    owner(task);
    if (index < task.counts.after) {
      const svg = await task.prepared.adapter.sdk.getPageSvg(index);
      owner(task);
      after = URL.createObjectURL(
        new Blob([safeSvg(svg)], {
          type: "image/svg+xml",
        }),
      );
      urls.current.push(after);
    }
    owner(task);
    setImages((previous) => [
      ...previous.filter((item) => item.page !== index + 1),
      { before, after, page: index + 1 },
    ]);
    setPage(index);
  }
  async function changePage(index: number) {
    if (running.current || !frozen.current) return;
    const task = frozen.current;
    if (images.some((item) => item.page === index + 1)) {
      setPage(index);
      return;
    }
    running.current = true;
    setPaging(true);
    try {
      await renderPage(task, index);
    } catch (cause) {
      if (active.current && frozen.current === task) {
        reset();
        setImages([]);
        setPhase("idle");
        setError(errorMessage(cause));
      }
    } finally {
      running.current = false;
      if (active.current) setPaging(false);
    }
  }
  async function applyPrepared() {
    const task = frozen.current;
    if (!task?.prepared || !task.verified) return;
    owner(task);
    setPhase("applying");
    await adopt(task.prepared);
    staging.current = null;
    frozen.current = null;
    urls.current.forEach(URL.revokeObjectURL);
    urls.current = [];
    if (active.current) {
      setImages([]);
      setPhase("idle");
      setNotice(
        "서식을 적용했습니다. 내용은 유지되며, 왼쪽 ‘방금 적용 취소’로 되돌릴 수 있습니다.",
      );
    }
  }
  async function prepare(choice: DesignChoice, direct = false) {
    if (running.current || !catalog) return;
    running.current = true;
    setError("");
    setNotice("");
    setPhase("preparing");
    setSelected(choice);
    const abort = new AbortController();
    controller.current = abort;
    try {
      let task = frozen.current;
      if (!task) {
        const item = (
          choice.kind === "standard" ? catalog.standards : catalog.designs
        ).find(
          (item) => item.id === choice.id && item.sha256 === choice.sha256,
        );
        if (!item)
          throw new BlockFenceError(
            "디자인이 변경됐습니다. 보관함을 다시 열어 확인하세요.",
          );
        const snap = capture();
        task = { capture: snap, body: {} };
        frozen.current = task;
        const target: PreparedDesign["target"] =
          choice.kind === "standard"
            ? { kind: "document", section: 0 }
            : snap.styleTarget!;
        if (
          !target ||
          (choice.kind === "design" &&
            item.kind === "table" &&
            target.kind !== "table")
        )
          throw new BlockFenceError(
            item.kind === "table"
              ? "바꾸려는 표 안에 커서를 놓고 다시 선택하세요."
              : "바꾸려는 제목 문단에 커서를 놓고 다시 선택하세요.",
          );
        const sourceSha = await blockHash(snap.source.bytes);
        owner(task);
        if (intent?.expected && intent.choice.id === choice.id) {
          const expected = intent.expected;
          if (
            expected.source_sha256 !== sourceSha ||
            expected.revision !== snap.source.revision ||
            expected.epoch !== snap.source.epoch ||
            expected.base_version !== snap.document.current_version ||
            blockJson(expected.target) !== blockJson(target)
          )
            throw new BlockFenceError(
              "AI가 확인한 문서나 선택 위치가 바뀌었습니다. 현재 문서로 다시 제안을 요청하세요.",
            );
        }
        task.body = {
          request_id: crypto.randomUUID(),
          base_version: snap.document.current_version,
          source_base64: encoded(snap.source.bytes),
          source_sha256: sourceSha,
          target,
          choice,
        };
      }
      owner(task);
      const headers = {
        "Content-Type": "application/json",
        "X-Edit-Session": task.capture.token,
      };
      if (!task.preview) {
        const result = await api<Preview>(
          `/documents/${task.capture.document.id}/design-preview`,
          { ...jsonRequest("POST", task.body), headers, signal: abort.signal },
        );
        owner(task);
        if (
          result.request_id !== task.body.request_id ||
          result.source_sha256 !== task.body.source_sha256 ||
          (await blockHash(bytes(result.output_base64))) !==
            result.output_sha256
        )
          throw new BlockFenceError("서식 결과의 원문·요청 확인값이 다릅니다.");
        task.preview = result;
      }
      if (!task.prepared) {
        const adapter = await EditorAdapter.mount(getStagingHost(), {
          signal: abort.signal,
        });
        staging.current = adapter;
        owner(task);
        await adapter.sdk.loadFile(
          bytes(task.preview.output_base64),
          "서식-적용-예시.hwpx",
          { suppressDialogs: true },
        );
        owner(task);
        const output = adapter.exportHwpx();
        if (output.contentLoss.count)
          throw new BlockFenceError(
            "내용 손실 경고가 있어 서식 적용을 중단했습니다.",
          );
        const outputSha256 = await blockHash(output.bytes);
        owner(task);
        task.prepared = {
          capture: task.capture,
          adapter,
          output,
          outputSha256,
          requestId: String(task.body.request_id),
          target: task.body.target as PreparedDesign["target"],
        };
      }
      if (!task.verified) {
        const proof = await api<{
          verified: boolean;
          request_id: string;
          input_sha256: string;
          output_sha256: string;
        }>(`/documents/${task.capture.document.id}/design-verify`, {
          ...jsonRequest("POST", {
            ...task.body,
            engine_output_base64: encoded(task.prepared.output.bytes),
          }),
          headers,
          signal: abort.signal,
        });
        owner(task);
        if (
          !proof.verified ||
          proof.request_id !== task.body.request_id ||
          proof.input_sha256 !== task.preview.input_sha256 ||
          proof.output_sha256 !== task.prepared.outputSha256
        )
          throw new BlockFenceError("내용 보존 검증을 완료하지 못했습니다.");
        task.verified = true;
      }
      setWarnings(task.preview.warnings || []);
      if (direct && !(task.preview.warnings || []).length) {
        await applyPrepared();
        return;
      }
      if (!task.counts) {
        const a = await task.capture.adapter.sdk.pageCount(),
          b = await task.prepared.adapter.sdk.pageCount();
        owner(task);
        task.counts = { before: a, after: b };
        setPageCount(Math.max(a, b));
      }
      // A failed/cancelled image request must never count as a ready preview.
      // Retry rendering even if an earlier request allocated a partial URL.
      const start = task.prepared.target.kind === "document" ? 0 :
        task.capture.adapter.bridge.paragraphPage(0, task.prepared.target.paragraph!) - 1;
      await renderPage(task, Math.max(0, Math.min(start,
        Math.max(task.counts.before, task.counts.after) - 1)));
      setPhase("ready");
    } catch (e) {
      if (!active.current || abort.signal.aborted) return;
      setError(errorMessage(e));
      if (
        e instanceof BlockFenceError ||
        (e instanceof ApiError && e.status >= 400 && e.status < 500)
      ) {
        reset();
        setImages([]);
        setPhase("idle");
      } else setPhase("retry");
    } finally {
      running.current = false;
      if (controller.current === abort) controller.current = null;
    }
  }
  const locked = phase !== "idle";
  const shown = images.find((item) => item.page === page + 1);
  const choices =
    (mode === "standard"
      ? catalog?.standards
      : catalog?.designs.filter(
          (item) => filter === "all" || item.kind === filter,
        )) || [];
  return (
    <aside className="editor-design-panel" aria-label="문서 서식·디자인">
      <header>
        <div>
          <small>문서 편집</small>
          <h2>서식·디자인</h2>
        </div>
        <button
          disabled={running.current}
          onClick={() => {
            reset();
            onClose();
          }}
        >
          닫기
        </button>
      </header>
      <div
        className="editor-design-tabs"
        role="group"
        aria-label="서식 적용 범위"
      >
        <button
          aria-pressed={mode === "design"}
          disabled={locked}
          onClick={() => setMode("design")}
        >
          선택한 부분
        </button>
        <button
          aria-pressed={mode === "standard"}
          disabled={locked}
          onClick={() => setMode("standard")}
        >
          문서 전체
        </button>
      </div>
      <p>
        {mode === "design"
          ? "표 안 또는 제목 문단에 커서를 놓고 예시를 누르면 해당 부분에 적용합니다."
          : "등록한 표준의 글꼴·문단·용지 서식을 적용합니다. 표지·목차를 새로 만들거나 본문 내용을 재작성하지 않습니다."}
      </p>
      {onAskAi && (
        <button
          className="editor-design-ask"
          disabled={locked}
          onClick={onAskAi}
        >
          현재 문서를 보고 AI에게 추천받기
        </button>
      )}
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
      {intent?.expected && (
        <p className="notice">
          AI가 제안한 디자인입니다. 같은 문서·위치인지 확인한 뒤 적용합니다.
        </p>
      )}
      {locked ? (
        <div className="editor-design-prepared">
          <p role="status">
            {phase === "preparing"
              ? "실제 문서에 서식을 배치하고 내용 보존을 확인하는 중…"
              : phase === "applying"
                ? "문서에 적용 중…"
                : phase === "retry"
                  ? "같은 요청으로 다시 확인할 수 있습니다."
                  : "실제 문서의 적용 전후"}
          </p>
          {warnings.length > 0 && (
            <ul>
              {warnings.map((text, i) => (
                <li key={i}>{text}</li>
              ))}
            </ul>
          )}
          {shown && (
            <>
              <PreviewPair pair={shown} />
              <button onClick={() => setZoom(true)}>크게 비교</button>
              {zoom && (
                <LargeDesignPreview
                  pair={shown}
                  onClose={() => setZoom(false)}
                />
              )}
              {pageCount > 1 && (
                <div className="editor-design-pagination">
                  <button
                    disabled={!page || paging}
                    onClick={() => void changePage(page - 1)}
                  >
                    이전
                  </button>
                  <span>
                    {page + 1} / {pageCount}
                  </span>
                  <button
                    disabled={page === pageCount - 1 || paging}
                    onClick={() => void changePage(page + 1)}
                  >
                    다음
                  </button>
                </div>
              )}
              <p className="editor-design-help">
                같은 쪽 번호를 나란히 표시합니다. 서식에 따라 내용의 쪽 배치가
                달라질 수 있습니다.
              </p>
            </>
          )}
          <div className="editor-design-actions">
            {phase === "ready" && (
              <button
                className="primary"
                disabled={paging}
                onClick={() => {
                  if (running.current) return;
                  running.current = true;
                  void applyPrepared()
                    .catch((e) => {
                      setError(errorMessage(e));
                      setPhase("ready");
                    })
                    .finally(() => {
                      running.current = false;
                    });
                }}
              >
                문서에 적용
              </button>
            )}
            {phase === "retry" && selected && (
              <button onClick={() => void prepare(selected)}>
                같은 요청 재확인
              </button>
            )}
            <button disabled={phase === "applying"} onClick={cancel}>
              취소
            </button>
          </div>
        </div>
      ) : (
        <>
          {mode === "design" && (
            <div
              className="editor-design-filters"
              role="group"
              aria-label="디자인 종류"
            >
              {(
                [
                  ["all", "전체"],
                  ["table", "표"],
                  ["title", "제목"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  aria-pressed={filter === key}
                  onClick={() => setFilter(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          {!catalog && !error && <p role="status">보관함 불러오는 중…</p>}
          <div className="editor-design-grid">
            {choices.map((item) => {
              const choice: DesignChoice = {
                kind: mode,
                id: item.id,
                sha256: item.sha256,
              };
              return (
                <article
                  key={item.id}
                  className={`editor-design-option${selected?.id === item.id ? " selected" : ""}`}
                >
                  <button
                    className="editor-design-card"
                    aria-label={`${item.name} ${mode === "design" ? "적용" : "미리보기"}`}
                    onClick={() => void prepare(choice, mode === "design")}
                  >
                    <DesignExample item={item} />
                    <strong>{item.name}</strong>
                    <small>
                      {mode === "design"
                        ? "클릭하여 적용"
                        : "표준 적용 전후 보기"}
                    </small>
                  </button>
                  {mode === "design" && (
                    <button
                      className="editor-design-preview"
                      onClick={() => void prepare(choice)}
                    >
                      적용 전 보기
                    </button>
                  )}
                </article>
              );
            })}
          </div>
          <p className="editor-design-help">
            카드는 디자인 예시입니다. ‘적용 전 보기’에서 현재 문서의 실제 배치를
            확인할 수 있습니다.
          </p>
        </>
      )}
    </aside>
  );
}
