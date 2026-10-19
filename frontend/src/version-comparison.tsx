import React, { useEffect, useRef, useState } from "react";
import { api, fileUrl, type Document, type Version } from "./api";
import type {
  Author,
  CompareBlock,
  CompareChange,
  CompareResult,
  ComparisonRenderer,
  PageLocation,
  SaveEvent,
} from "./comparison-types";
import { textDifference } from "./text-diff";
import "./version-history.css";

export const causeLabel = (cause: string) =>
  ({
    upload: "최초 업로드",
    "web-edit": "웹 편집",
    reupload: "재업로드",
    restore: "복원",
    report: "보고서 저장",
  })[cause] || cause;
export const authorLabel = (author?: Author) =>
  !author
    ? "수정자 기록 없음"
    : author.identity_kind === "local"
      ? "로컬 사용자"
      : author.user_id;
export const dateLabel = (time: number) =>
  new Date(time * 1000).toLocaleString("ko-KR");
const kindLabel = (kind: string) =>
  ({
    added: "추가",
    removed: "삭제",
    modified: "내용 수정",
    format: "서식 수정",
    object: "개체·설정 수정",
    moved: "순서 변경",
  })[kind] || kind;

export function useComparisonDialog(
  ref: React.RefObject<HTMLElement | null>,
  onClose: () => void,
) {
  const callback = useRef(onClose);
  callback.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        callback.current();
      }
      if (event.key !== "Tab") return;
      const items = Array.from(
        ref.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]',
        ) || [],
      ).filter((e) => e.getClientRects().length);
      if (!items.length) return;
      const index = items.indexOf(document.activeElement as HTMLElement);
      if (event.shiftKey && index <= 0) {
        event.preventDefault();
        items.at(-1)!.focus();
      } else if (!event.shiftKey && (index < 0 || index === items.length - 1)) {
        event.preventDefault();
        items[0].focus();
      }
    };
    const message = (event: MessageEvent) => {
      if (
        event.origin === location.origin &&
        event.data?.type === "hwp-comparison-escape" &&
        Array.from(ref.current?.querySelectorAll("iframe") || []).some(
          (frame) => frame.contentWindow === event.source,
        )
      )
        callback.current();
    };
    document.addEventListener("keydown", key, true);
    window.addEventListener("message", message);
    return () => {
      document.removeEventListener("keydown", key, true);
      window.removeEventListener("message", message);
      if (previous?.isConnected) previous.focus();
    };
  }, [ref]);
}

function SavedBy({
  event,
  uncertain = false,
}: {
  event: SaveEvent | null;
  uncertain?: boolean;
}) {
  if (!event)
    return <span className="compare-muted">수정 이력을 확인할 수 없음</span>;
  if (uncertain)
    return (
      <span className="compare-muted">
        문단 대응 불확실 · 수정자 단정 안 함
      </span>
    );
  return (
    <span className="compare-author">
      <b>{authorLabel(event.author)}</b>
      <span>
        {dateLabel(event.created_at)} · {causeLabel(event.cause)}
      </span>
    </span>
  );
}

function DocumentPane({
  side,
  version,
  bytes,
  block,
  kind,
  zoom,
}: {
  side: "left" | "right";
  version: Version;
  bytes: Uint8Array;
  block: CompareBlock | null;
  kind: string;
  zoom: number;
}) {
  const frame = useRef<HTMLIFrameElement>(null),
    renderer = useRef<ComparisonRenderer | null>(null);
  const [loaded, setLoaded] = useState(false),
    [pages, setPages] = useState(0),
    [page, setPage] = useState(0);
  const [error, setError] = useState(""),
    [location, setLocation] = useState<PageLocation | undefined>();
  useEffect(() => {
    if (!loaded) return;
    let alive = true;
    const instance = (
      frame.current?.contentWindow as unknown as {
        hwpComparisonRenderer?: ComparisonRenderer;
      }
    )?.hwpComparisonRenderer;
    if (!instance) {
      setError("비교 렌더러를 시작하지 못했습니다.");
      return;
    }
    let timeout: ReturnType<typeof setTimeout>;
    const deadline = new Promise<never>((_, reject) => {
      timeout = setTimeout(
        () =>
          reject(
            Error(
              "문서 렌더러 준비 시간이 초과되었습니다. 비교를 닫고 다시 시도하세요.",
            ),
          ),
        45000,
      );
    });
    void Promise.race([instance.open(bytes, version.sha256), deadline])
      .then((result) => {
        if (!alive) {
          instance.dispose();
          return;
        }
        renderer.current = instance;
        setPages(result.pages);
        setPage(0);
      })
      .catch((e) => {
        if (alive) {
          instance.dispose();
          setError(String(e));
        }
      })
      .finally(() => clearTimeout(timeout));
    return () => {
      alive = false;
      clearTimeout(timeout);
      renderer.current = null;
      instance.dispose();
    };
  }, [loaded, bytes, version.sha256]);
  useEffect(() => {
    if (!pages || !renderer.current) return;
    try {
      const next = block ? renderer.current.locate(block) : undefined;
      setLocation(next);
      setPage(next?.page ?? 0);
    } catch (e) {
      setError(String(e));
    }
  }, [pages, block]);
  useEffect(() => {
    if (!renderer.current || !pages) return;
    try {
      renderer.current.render(page, location, kind, zoom);
    } catch (e) {
      setError(String(e));
    }
  }, [pages, page, location, kind, zoom]);
  return (
    <div
      className="comparison-pane"
      data-side={side}
      data-render-ready={pages > 0 && !error}
    >
      <div className="comparison-pane-head">
        <strong>{side === "left" ? "기준 문서" : "비교 문서"}</strong>
        <span>
          {dateLabel(version.created_at)} · {authorLabel(version.author)}
        </span>
      </div>
      <div className="comparison-pages">
        <button
          aria-label={`${side === "left" ? "기준" : "비교"} 문서 이전 쪽`}
          disabled={page === 0 || !pages}
          onClick={() => setPage((p) => p - 1)}
        >
          ‹
        </button>
        <span>
          {pages ? `${page + 1} / ${pages}쪽` : "실제 문서 렌더링 준비 중…"}
        </span>
        <button
          aria-label={`${side === "left" ? "기준" : "비교"} 문서 다음 쪽`}
          disabled={!pages || page + 1 >= pages}
          onClick={() => setPage((p) => p + 1)}
        >
          ›
        </button>
        {location?.page != null && page !== location.page && (
          <button onClick={() => setPage(location.page!)}>변경 위치</button>
        )}
      </div>
      {error ? (
        <p role="alert">{error}</p>
      ) : (
        <iframe
          ref={frame}
          title={`${side === "left" ? "기준" : "비교"} 버전 읽기 전용 페이지`}
          src="/compare-renderer.html"
          tabIndex={-1}
          onLoad={() => setLoaded(true)}
        />
      )}
      <p className="comparison-location">
        {block
          ? location?.note || "변경 위치 확인 중…"
          : "이쪽에는 대응하는 변경 내용이 없습니다. 문서를 자유롭게 확인할 수 있습니다."}
      </p>
    </div>
  );
}

function ChangeDetail({ change }: { change: CompareChange }) {
  const parts = textDifference(
    change.left?.text || "",
    change.right?.text || "",
  );
  return (
    <div className="comparison-detail">
      <div className="compare-text-before" aria-label="변경 전 내용">
        {parts
          .filter((p) => p.kind !== "added")
          .map((p, i) =>
            p.kind === "removed" ? (
              <del key={i}>{p.text}</del>
            ) : (
              <span key={i}>{p.text}</span>
            ),
          ) || ""}
      </div>
      <div className="compare-text-after" aria-label="변경 후 내용">
        {parts
          .filter((p) => p.kind !== "removed")
          .map((p, i) =>
            p.kind === "added" ? (
              <ins key={i}>{p.text}</ins>
            ) : (
              <span key={i}>{p.text}</span>
            ),
          ) || ""}
      </div>
      {change.kind === "format" && (
        <p>
          문구는 같고 글자 또는 문단 서식이 달라졌습니다. 좌우 실제 문서를
          확인하세요.
        </p>
      )}
      <div className="comparison-provenance">
        <span>기준 내용의 마지막 변경</span>
        <SavedBy
          event={change.left?.last_change || null}
          uncertain={change.uncertain}
        />
        <span>비교 내용의 마지막 변경</span>
        <SavedBy
          event={change.right?.last_change || null}
          uncertain={change.uncertain}
        />
      </div>
      {change.history.length > 0 && (
        <details open>
          <summary>
            선택한 두 버전 사이의 저장 기록 ({change.history.length})
          </summary>
          <ol>
            {change.history.map((e, i) => (
              <li key={`${e.version_id}:${e.kind}:${i}`}>
                <strong>{authorLabel(e.author)}</strong> · {kindLabel(e.kind)}
                <br />
                {dateLabel(e.created_at)} · {causeLabel(e.cause)}
                <small>버전 {e.version_id.slice(0, 8)}</small>
              </li>
            ))}
          </ol>
        </details>
      )}
      {change.uncertain && (
        <p className="compare-caution">
          반복 문단 등으로 대응 관계가 불명확합니다. 저장 기록을 함께
          검토하세요.
        </p>
      )}
    </div>
  );
}

export function VersionComparison({
  document: d,
  left,
  right,
  onClose,
}: {
  document: Document;
  left: Version;
  right: Version;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLElement>(null);
  const [result, setResult] = useState<CompareResult | null>(null),
    [files, setFiles] = useState<[Uint8Array, Uint8Array] | null>(null);
  const [error, setError] = useState(""),
    [phase, setPhase] = useState("저장 이력과 변경 내용을 비교하는 중…"),
    [selected, setSelected] = useState(0);
  const [zoom, setZoom] = useState(1);
  useComparisonDialog(dialog, onClose);
  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    void (async () => {
      try {
        const response = await api<CompareResult>(
          `/documents/${d.id}/compare?left=${left.id}&right=${right.id}`,
          { signal: controller.signal },
        );
        if (!alive) return;
        if (
          response.left.id !== left.id ||
          response.right.id !== right.id ||
          response.left.sha256 !== left.sha256 ||
          response.right.sha256 !== right.sha256
        )
          throw Error("비교 대상 버전이 일치하지 않습니다.");
        setResult(response);
        setPhase("두 저장본을 불러오는 중…");
        const bytes = await Promise.all(
          [left, right].map(async (v) => {
            const res = await fetch(fileUrl(d, v.id), {
              signal: controller.signal,
            });
            if (!res.ok)
              throw Error(
                "저장본을 읽을 권한이 없거나 다운로드에 실패했습니다.",
              );
            if (Number(res.headers.get("Content-Length")) > 30 * 1024 * 1024)
              throw Error("비교 저장본의 크기 한도를 초과했습니다.");
            const value = new Uint8Array(await res.arrayBuffer());
            if (value.length !== v.size || value.length > 30 * 1024 * 1024)
              throw Error("저장본의 크기가 기록과 다릅니다.");
            return value;
          }),
        );
        if (alive) {
          setFiles(bytes as [Uint8Array, Uint8Array]);
          setPhase("");
        }
      } catch (e) {
        if (alive) {
          setResult(null);
          setFiles(null);
          setError(String(e));
          setPhase("");
        }
      }
    })();
    return () => {
      alive = false;
      controller.abort();
    };
  }, [d.id, left.id, right.id]);
  const change = result?.changes[selected];
  return (
    <div className="modal comparison-modal">
      <section
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="comparison-title"
      >
        <header className="comparison-header">
          <div>
            <h2 id="comparison-title">{d.name} · 문서 비교</h2>
            <p>저장된 두 버전을 읽기 전용으로 비교합니다.</p>
          </div>
          <button onClick={onClose}>비교 닫기</button>
        </header>
        <p className="comparison-policy">
          수정자는 해당 변경을 서버에 저장한 사용자입니다. 외부 파일의 실제
          작성자는 확인할 수 없으며, 로컬 모드는 개인을 구분하지 않습니다.
        </p>
        {error && <p role="alert">{error}</p>}
        {phase && <p role="status">{phase}</p>}
        {result && (
          <>
            <div className="comparison-toolbar">
              <strong>변경 {result.changes.length}건</strong>
              <span>
                {result.reverse
                  ? "역순 비교 · 수정 기록은 실제 저장 순서"
                  : `저장 이력 ${result.history_versions}개 확인`}
              </span>
              <button
                disabled={!change || selected === 0}
                onClick={() => setSelected((i) => i - 1)}
              >
                이전 변경
              </button>
              <button
                disabled={!change || selected + 1 >= result.changes.length}
                onClick={() => setSelected((i) => i + 1)}
              >
                다음 변경
              </button>
              {change && (
                <span>
                  {selected + 1} / {result.changes.length}
                </span>
              )}
              <label>
                배율{" "}
                <select
                  aria-label="문서 비교 배율"
                  value={zoom}
                  onChange={(e) => setZoom(Number(e.target.value))}
                >
                  <option value={0}>폭 맞춤</option>
                  <option value={0.75}>75%</option>
                  <option value={1}>100%</option>
                  <option value={1.25}>125%</option>
                  <option value={1.5}>150%</option>
                </select>
              </label>
            </div>
            {result.warnings.length > 0 && (
              <details className="comparison-warnings">
                <summary>비교 범위 안내 ({result.warnings.length})</summary>
                {result.warnings.map((w) => (
                  <p key={w}>{w}</p>
                ))}
              </details>
            )}
            {!result.changes.length && (
              <p className="notice">
                지원하는 본문·서식·개체 범위에서 변경이 없습니다.
              </p>
            )}
            <div className="comparison-workspace">
              <div className="comparison-documents">
                {files && (
                  <>
                    <DocumentPane
                      side="left"
                      version={result.left}
                      bytes={files[0]}
                      block={change?.left || null}
                      kind={change?.kind === "removed" ? "removed" : "modified"}
                      zoom={zoom}
                    />
                    <DocumentPane
                      side="right"
                      version={result.right}
                      bytes={files[1]}
                      block={change?.right || null}
                      kind={change?.kind === "added" ? "added" : "modified"}
                      zoom={zoom}
                    />
                  </>
                )}
              </div>
              <aside
                className="comparison-sidebar"
                aria-label="변경 내용과 수정자"
              >
                <h3>변경 내용 · 수정자</h3>
                <div className="comparison-change-list">
                  {result.changes.map((item, i) => (
                    <button
                      key={item.id}
                      className={i === selected ? "selected" : ""}
                      aria-pressed={i === selected}
                      onClick={() => setSelected(i)}
                    >
                      <span className={`compare-kind ${item.kind}`}>
                        {kindLabel(item.kind)}
                      </span>
                      <span>{(item.right || item.left)!.label}</span>
                      <span className="compare-snippet">
                        {(item.right || item.left)!.text || "빈 문단"}
                      </span>
                      <span className="compare-byline">
                        {item.uncertain
                          ? "문단 대응 확인 필요"
                          : authorLabel(
                              item.history.length
                                ? item.history.at(-1)!.author
                                : (item.right || item.left)?.last_change
                                    ?.author,
                            )}
                      </span>
                    </button>
                  ))}
                </div>
                {change && <ChangeDetail change={change} />}
              </aside>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
