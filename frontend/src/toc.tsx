import { useEffect, useRef, useState } from "react";
import { api, ApiError, jsonRequest, type Document } from "./api";
import {
  EditorAdapter,
  type DocumentState,
  type ExportSnapshot,
  type LayoutEvidence,
  type LayoutHeading,
  type Snapshot,
} from "./editor";
import { BlockFenceError, blockHash } from "./block-insert";

export type TocCapture = {
  adapter: EditorAdapter;
  document: Document;
  token: string;
  selection: Snapshot | null;
  state: DocumentState;
  source: ExportSnapshot;
};
export type PreparedToc = {
  capture: TocCapture;
  adapter: EditorAdapter;
  output: ExportSnapshot;
  outputSha256: string;
  requestId: string;
};
type Candidate = {
  section: number;
  paragraph: number;
  text: string;
  text_sha256: string;
  length: number;
  heading_id?: string;
  level?: 1 | 2;
};
type Anchor = {
  section: number;
  paragraph: number;
  expected_text_sha256: string;
};
type Analysis = {
  request_id: string;
  input_sha256: string;
  source_sha256: string;
  candidates: Candidate[];
  toc: null | {
    id: string;
    status: "current" | "stale" | "damaged";
    reason?: string;
    anchor: Anchor;
    headings: (Candidate & { id: string; heading_id: string; level: 1 | 2 })[];
  };
};
type Preview = {
  request_id: string;
  input_sha256: string;
  source_sha256: string;
  output_base64: string;
  output_sha256: string;
  pass_index: number;
  headings: (LayoutHeading & { text: string })[];
  pages: Record<string, number>;
  plan: { toc_paragraph: number };
};
type Verification = {
  request_id: string;
  input_sha256: string;
  output_sha256: string;
  verified: boolean;
  converged: boolean;
  measured_pages: Record<string, number>;
};
type Frozen = {
  capture: TocCapture;
  requestId: string;
  analyzeBody?: Record<string, unknown>;
  analysis?: Analysis;
  body?: Record<string, unknown>;
  preview?: Preview;
  prepared?: PreparedToc;
  evidence?: LayoutEvidence;
  verified?: Verification;
};
const sameBytes = (a: Uint8Array, b: Uint8Array) =>
  a.length === b.length && a.every((n, i) => n === b[i]);
function base64(bytes: Uint8Array) {
  const chunks: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 8192)
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 8192)));
  return btoa(chunks.join(""));
}
const bytes = (value: string) =>
  Uint8Array.from(atob(value), (char) => char.charCodeAt(0));
function safeSvg(value: string) {
  if (value.length > 8_000_000 || /<!DOCTYPE|<!ENTITY/i.test(value))
    throw new BlockFenceError("표시할 수 없는 목차 미리보기입니다.");
  const document = new DOMParser().parseFromString(value, "image/svg+xml");
  if (
    document.querySelector("parsererror") ||
    document.documentElement.localName !== "svg"
  )
    throw new BlockFenceError("목차 미리보기를 읽을 수 없습니다.");
  const resource = (text: string) =>
    /^(#|data:(image\/(png|jpeg|gif|webp)|font\/(woff|woff2)|application\/(font-woff|x-font-woff));base64,)/i.test(
      text,
    );
  const externalCss = (text: string) =>
    /@import/i.test(text) ||
    Array.from(text.matchAll(/url\s*\(([^)]*)\)/gi)).some(
      (match) => !resource(match[1].trim().replace(/^['"]|['"]$/g, "")),
    );
  for (const element of Array.from(document.querySelectorAll("*"))) {
    if (
      [
        "script",
        "foreignObject",
        "iframe",
        "object",
        "embed",
        "animate",
        "set",
        "animateMotion",
        "animateTransform",
      ].includes(element.localName)
    )
      throw new BlockFenceError(
        "활성 요소가 있는 목차 미리보기는 표시할 수 없습니다.",
      );
    for (const attribute of Array.from(element.attributes))
      if (
        /^on/i.test(attribute.name) ||
        (attribute.localName === "href" && !resource(attribute.value)) ||
        externalCss(attribute.value)
      )
        throw new BlockFenceError(
          "외부 리소스가 있는 목차 미리보기는 표시할 수 없습니다.",
        );
    if (element.localName === "style" && externalCss(element.textContent || ""))
      throw new BlockFenceError(
        "외부 서식이 있는 목차 미리보기는 표시할 수 없습니다.",
      );
  }
  return new XMLSerializer().serializeToString(document);
}

export function TocPanel({
  capture,
  isOwner,
  release,
  adopt,
  getStagingHost,
  onClose,
  onAnalyzed,
}: {
  capture: () => TocCapture;
  isOwner: (value: TocCapture) => boolean;
  release: (value: TocCapture) => void;
  adopt: (value: PreparedToc) => Promise<void>;
  getStagingHost: () => HTMLDivElement;
  onClose: () => void;
  onAnalyzed: (
    value: TocCapture,
    status: "current" | "stale" | "damaged" | "none",
  ) => void;
}) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null),
    [selected, setSelected] = useState<
      Record<number, { id: string; level: 1 | 2 }>
    >({}),
    [anchor, setAnchor] = useState(0),
    [phase, setPhase] = useState("idle"),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [pages, setPages] = useState<{ url: string; page: number }[]>([]),
    [measured, setMeasured] = useState<
      (LayoutHeading & { text: string; page: number })[]
    >([]);
  const active = useRef(true),
    running = useRef(false),
    frozen = useRef<Frozen | null>(null),
    staging = useRef<EditorAdapter | null>(null),
    urls = useRef<string[]>([]),
    abort = useRef<AbortController | null>(null);
  function destroyStage(task?: Frozen) {
    staging.current?.destroy();
    staging.current = null;
    if (task) {
      task.prepared = undefined;
      task.evidence = undefined;
    }
  }
  function clear() {
    abort.current?.abort();
    abort.current = null;
    const old = frozen.current;
    frozen.current = null;
    destroyStage();
    urls.current.forEach(URL.revokeObjectURL);
    urls.current = [];
    if (old) release(old.capture);
  }
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      clear();
    };
  }, []);
  function owner(task: Frozen) {
    if (!active.current || frozen.current !== task || !isOwner(task.capture))
      throw new BlockFenceError(
        "문서 세션이 바뀌었습니다. 현재 문서에서 목차를 다시 확인하세요.",
      );
  }
  function headers(task: Frozen) {
    return {
      "Content-Type": "application/json",
      "X-Edit-Session": task.capture.token,
    };
  }
  function stageFence(task: Frozen) {
    const prepared = task.prepared!;
    const current = prepared.adapter.exportHwpx();
    if (
      current.contentLoss.count ||
      current.epoch !== prepared.output.epoch ||
      current.revision !== prepared.output.revision ||
      !sameBytes(current.bytes, prepared.output.bytes)
    )
      throw new BlockFenceError(
        "검증한 목차 미리보기 내용이 바뀌었습니다. 다시 준비하세요.",
      );
  }
  function fail(e: unknown, controller: AbortController, task: Frozen | null) {
    if (
      !active.current ||
      controller.signal.aborted ||
      (task && frozen.current !== task)
    )
      return;
    const recoverable =
      !(e instanceof BlockFenceError) &&
      !(e instanceof ApiError && e.status >= 400 && e.status < 500);
    setError(
      `${String(e)} · 기존 문서의 편집 내용은 보존됐습니다.${recoverable && task ? " 입력과 요청 ID를 보관했습니다. 같은 목차 요청을 재확인하세요." : " 현재 문서에서 다시 확인하세요."}`,
    );
    if (recoverable && task)
      setPhase(task.analysis ? "retry-prepare" : "retry-analyze");
    else {
      clear();
      setAnalysis(null);
      setPages([]);
      setMeasured([]);
      setPhase("idle");
    }
  }
  async function analyze() {
    if (running.current) return;
    running.current = true;
    setPhase("analyzing");
    setError("");
    setNotice("");
    const controller = new AbortController();
    abort.current = controller;
    let task = frozen.current;
    try {
      if (!task) {
        task = { capture: capture(), requestId: crypto.randomUUID() };
        frozen.current = task;
        const hash = await blockHash(task.capture.source.bytes);
        owner(task);
        task.analyzeBody = {
          request_id: task.requestId,
          base_version: task.capture.document.current_version,
          source_base64: base64(task.capture.source.bytes),
          source_sha256: hash,
        };
      }
      owner(task);
      const result = await api<Analysis>(
        `/documents/${task.capture.document.id}/toc-analyze`,
        {
          ...jsonRequest("POST", task.analyzeBody),
          headers: headers(task),
          signal: controller.signal,
        },
      );
      owner(task);
      if (
        result.request_id !== task.requestId ||
        result.source_sha256 !== task.analyzeBody!.source_sha256
      )
        throw new BlockFenceError(
          "목차 분석의 문서와 요청이 일치하지 않습니다.",
        );
      task.analysis = result;
      setAnalysis(result);
      const headings: Record<number, { id: string; level: 1 | 2 }> = {};
      for (const heading of result.toc?.headings || [])
        headings[heading.paragraph] = {
          id: heading.heading_id || heading.id,
          level: heading.level,
        };
      setSelected(headings);
      setAnchor(
        result.toc?.anchor.paragraph ?? result.candidates[0]?.paragraph ?? 0,
      );
      onAnalyzed(task.capture, result.toc?.status || "none");
      setPhase("select");
    } catch (e) {
      fail(e, controller, task);
    } finally {
      running.current = false;
      if (abort.current === controller) abort.current = null;
    }
  }
  async function prepare() {
    const task = frozen.current;
    if (running.current || !task?.analysis) return;
    running.current = true;
    setPhase("preparing");
    setError("");
    setNotice("");
    const controller = new AbortController();
    abort.current = controller;
    try {
      owner(task);
      if (!task.body) {
        const candidates = task.analysis.candidates.filter(
          (item) => selected[item.paragraph],
        );
        if (!candidates.length || candidates.length > 30)
          throw new BlockFenceError("목차 제목을 1~30개 선택하세요.");
        const target =
          task.analysis.toc?.anchor ||
          (() => {
            const candidate = task.analysis!.candidates.find(
              (item) => item.paragraph === anchor,
            );
            if (!candidate)
              throw new BlockFenceError("목차를 넣을 본문 위치를 선택하세요.");
            return {
              section: 0,
              paragraph: anchor,
              expected_text_sha256: candidate.text_sha256,
            };
          })();
        task.body = {
          ...task.analyzeBody,
          toc_id: task.analysis.toc?.id || crypto.randomUUID(),
          target,
          headings: candidates.map((item) => ({
            heading_id: selected[item.paragraph].id,
            paragraph: item.paragraph,
            level: selected[item.paragraph].level,
            expected_text_sha256: item.text_sha256,
          })),
          pass_index: 1,
          pages: {},
        };
      }
      while (true) {
        owner(task);
        if (!task.preview) {
          const result: Preview = await api<Preview>(
            `/documents/${task.capture.document.id}/toc-preview`,
            {
              ...jsonRequest("POST", task.body),
              headers: headers(task),
              signal: controller.signal,
            },
          );
          owner(task);
          if (
            result.request_id !== task.requestId ||
            result.source_sha256 !== task.body.source_sha256 ||
            result.pass_index !== task.body.pass_index ||
            (await blockHash(bytes(result.output_base64))) !==
              result.output_sha256
          )
            throw new BlockFenceError(
              "목차 준비 결과 또는 파일 해시가 일치하지 않습니다.",
            );
          owner(task);
          task.preview = result;
        }
        if (!task.prepared) {
          destroyStage(task);
          const instance = await EditorAdapter.mount(getStagingHost());
          try {
            owner(task);
          } catch (e) {
            instance.destroy();
            throw e;
          }
          staging.current = instance;
          try {
            await instance.sdk.loadFile(
              bytes(task.preview!.output_base64),
              "목차-미리보기.hwpx",
              { suppressDialogs: true },
            );
            owner(task);
            const output = instance.exportHwpx();
            if (output.contentLoss.count)
              throw new BlockFenceError(
                "한글 엔진의 내용 손실 경고가 있어 목차를 반영할 수 없습니다.",
              );
            const outputSha256 = await blockHash(output.bytes);
            owner(task);
            task.prepared = {
              capture: task.capture,
              adapter: instance,
              output,
              outputSha256,
              requestId: task.requestId,
            };
            const descriptors = task.preview!.headings.map(
              ({ id, section, paragraph, length, text_sha256 }) => ({
                id,
                section,
                paragraph,
                length,
                text_sha256,
              }),
            );
            const evidence = instance.bridge.layoutEvidence(descriptors);
            stageFence(task);
            if (
              evidence.epoch !== output.epoch ||
              evidence.revision !== output.revision ||
              evidence.export_sha256 !== outputSha256 ||
              !Number.isInteger(evidence.page_count) ||
              evidence.page_count < 1
            )
              throw new BlockFenceError(
                "렌더링 쪽수와 문서 상태의 검증에 실패했습니다.",
              );
            task.evidence = evidence;
          } catch (e) {
            if (staging.current === instance) destroyStage(task);
            throw e;
          }
        }
        if (!task.verified) {
          const verified: Verification = await api<Verification>(
            `/documents/${task.capture.document.id}/toc-verify`,
            {
              ...jsonRequest("POST", {
                ...task.body,
                engine_output_base64: base64(task.prepared.output.bytes),
                layout_evidence: task.evidence,
              }),
              headers: headers(task),
              signal: controller.signal,
            },
          );
          owner(task);
          stageFence(task);
          if (
            !verified.verified ||
            verified.request_id !== task.requestId ||
            verified.input_sha256 !== task.preview!.input_sha256 ||
            verified.output_sha256 !== task.prepared.outputSha256
          )
            throw new BlockFenceError(
              "목차와 원문 보존 검증을 완료하지 못했습니다.",
            );
          task.verified = verified;
        }
        if (task.verified!.converged) break;
        if (Number(task.body.pass_index) >= 3)
          throw new BlockFenceError(
            "세 번의 렌더링으로 목차 쪽수가 수렴하지 않았습니다. 원문은 그대로 유지합니다.",
          );
        task.body = {
          ...task.body,
          pass_index: Number(task.body.pass_index) + 1,
          pages: { ...task.verified!.measured_pages },
        };
        destroyStage(task);
        task.preview = undefined;
        task.verified = undefined;
      }
      const images: { url: string; page: number }[] = [];
      let committed = false;
      try {
        // Render actual pages as inert images. Export bytes must remain exactly
        // those verified above, including after SVG layout reads.
        const tocPage = task.prepared!.adapter.bridge.paragraphPage(
          0,
          task.preview!.plan.toc_paragraph,
        );
        stageFence(task);
        if (
          !Number.isInteger(tocPage) ||
          tocPage < 1 ||
          tocPage > task.evidence!.page_count
        )
          throw new BlockFenceError("목차의 실제 쪽수를 확인하지 못했습니다.");
        for (
          let index = tocPage - 1;
          index < Math.min(task.evidence!.page_count, tocPage + 2);
          index++
        ) {
          const svg = safeSvg(
            await task.prepared!.adapter.sdk.getPageSvg(index),
          );
          owner(task);
          stageFence(task);
          images.push({
            url: URL.createObjectURL(
              new Blob([svg], { type: "image/svg+xml" }),
            ),
            page: index + 1,
          });
        }
        owner(task);
        urls.current.forEach(URL.revokeObjectURL);
        urls.current = images.map((item) => item.url);
        setPages(images);
        committed = true;
      } finally {
        if (!committed) images.forEach((item) => URL.revokeObjectURL(item.url));
      }
      owner(task);
      stageFence(task);
      setMeasured(
        task.preview!.headings.map((item) => ({
          ...item,
          page: task.verified!.measured_pages[item.id],
        })),
      );
      setPhase("ready");
    } catch (e) {
      fail(e, controller, task);
    } finally {
      running.current = false;
      if (abort.current === controller) abort.current = null;
    }
  }
  async function insert() {
    const task = frozen.current;
    if (running.current || !task?.prepared || !task.verified?.converged) return;
    running.current = true;
    setPhase("inserting");
    setError("");
    try {
      owner(task);
      stageFence(task);
      await adopt(task.prepared);
      staging.current = null;
      frozen.current = null;
      urls.current.forEach(URL.revokeObjectURL);
      urls.current = [];
      setPages([]);
      setMeasured([]);
      setAnalysis(null);
      setPhase("idle");
      setNotice(
        "검증한 목차를 반영했습니다. 본문을 수정하면 쪽수를 다시 확인하고 갱신하세요. 서버 저장은 별도입니다.",
      );
    } catch (e) {
      if (!active.current || frozen.current !== task) return;
      setError(`${String(e)} · 기존 문서의 편집 내용은 보존됐습니다.`);
      if (
        e instanceof BlockFenceError ||
        (e instanceof ApiError && e.status >= 400 && e.status < 500)
      ) {
        clear();
        setPages([]);
        setAnalysis(null);
        setMeasured([]);
        setPhase("idle");
      } else setPhase("ready");
    } finally {
      running.current = false;
    }
  }
  const editable = phase === "select",
    existing = Boolean(analysis?.toc),
    count = Object.keys(selected).length;
  return (
    <aside className="toc-panel" aria-label="목차 관리">
      <div className="block-panel-heading">
        <h2>목차 관리</h2>
        <button
          disabled={phase === "inserting"}
          onClick={() => {
            clear();
            onClose();
          }}
        >
          닫기
        </button>
      </div>
      <p>
        제목과 1·2계층을 선택하면 한글 엔진에서 실제 쪽수를 확인합니다. 미리보기
        후 직접 반영하세요.
      </p>
      <p className="hint">
        첫 구역의 본문 제목을 최대 30개 지원합니다. 표·필드·그림 문단은
        제외하며, 제목 ID가 중복되거나 목차를 직접 고친 경우 갱신을 중단합니다.
      </p>
      {phase === "idle" && (
        <button className="primary" onClick={() => void analyze()}>
          현재 문서의 목차·제목 확인
        </button>
      )}
      {(phase === "analyzing" || phase === "preparing") && (
        <p role="status">
          {phase === "analyzing"
            ? "현재 문서의 제목과 목차를 확인 중…"
            : "실제 렌더링 쪽수와 원문 보존을 검증 중…"}
        </p>
      )}
      {phase === "retry-analyze" && (
        <button onClick={() => void analyze()}>같은 목차 요청 재확인</button>
      )}
      {phase === "retry-prepare" && (
        <button onClick={() => void prepare()}>같은 목차 요청 재확인</button>
      )}
      {analysis && (
        <>
          <p className="notice">
            {analysis.toc
              ? {
                  current: "현재 목차 · 실제 쪽수 재검증 가능",
                  stale: "목차 갱신 필요 · 본문이 변경됐습니다",
                  damaged: "목차가 손상되어 자동 갱신할 수 없습니다",
                }[analysis.toc.status]
              : "아직 관리되는 목차가 없습니다"}
            {analysis.toc?.reason && ` · ${analysis.toc.reason}`}
          </p>
          <label>
            목차 위치
            <select
              aria-label="목차를 넣을 문단 뒤"
              value={anchor}
              disabled={!editable || existing}
              onChange={(event) => setAnchor(Number(event.target.value))}
            >
              {existing &&
                !analysis.candidates.some(
                  (item) => item.paragraph === anchor,
                ) && <option value={anchor}>기존 목차 위치</option>}
              {analysis.candidates.map((item) => (
                <option key={item.paragraph} value={item.paragraph}>
                  {item.text}
                </option>
              ))}
            </select>
          </label>
          <div className="toc-heading-list">
            {analysis.candidates.map((item) => (
              <div className="toc-heading-choice" key={item.paragraph}>
                <label>
                  <input
                    type="checkbox"
                    aria-label={`목차 제목 ${item.text}`}
                    checked={Boolean(selected[item.paragraph])}
                    disabled={
                      !editable ||
                      existing ||
                      (count >= 30 && !selected[item.paragraph])
                    }
                    onChange={(event) => {
                      const next = { ...selected };
                      if (event.target.checked)
                        next[item.paragraph] = {
                          id: item.heading_id || crypto.randomUUID(),
                          level: item.level || 1,
                        };
                      else delete next[item.paragraph];
                      setSelected(next);
                    }}
                  />
                  {item.text}
                </label>
                {selected[item.paragraph] && (
                  <select
                    aria-label={`제목 계층 ${item.text}`}
                    value={selected[item.paragraph].level}
                    disabled={!editable || existing}
                    onChange={(event) =>
                      setSelected({
                        ...selected,
                        [item.paragraph]: {
                          ...selected[item.paragraph],
                          level: Number(event.target.value) as 1 | 2,
                        },
                      })
                    }
                  >
                    <option value={1}>1계층</option>
                    <option value={2}>2계층</option>
                  </select>
                )}
              </div>
            ))}
          </div>
          {editable && (
            <button
              className="primary"
              disabled={!count || analysis.toc?.status === "damaged"}
              onClick={() => void prepare()}
            >
              {existing
                ? "목차 쪽수 갱신 미리보기"
                : "선택한 제목으로 목차 미리보기"}
            </button>
          )}
        </>
      )}
      {phase === "ready" || phase === "inserting" ? (
        <>
          <p className="notice">
            아래 쪽수는 실제 렌더링에서 확인했습니다. 문서나 선택 위치가
            달라지면 다시 준비하세요.
          </p>
          <button
            className="primary"
            disabled={phase === "inserting"}
            onClick={() => void insert()}
          >
            {phase === "inserting" ? "권한·원문 확인 중…" : "검증한 목차 반영"}
          </button>
        </>
      ) : null}
      {phase !== "idle" && phase !== "inserting" && (
        <button
          onClick={() => {
            clear();
            setAnalysis(null);
            setMeasured([]);
            setPages([]);
            setError("");
            setPhase("idle");
          }}
        >
          목차 준비 취소 · 다시 확인
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
      {measured.length > 0 && (
        <ol className="toc-measured">
          {measured.map((item) => (
            <li key={item.id}>
              <span>{item.text}</span>
              <strong>{item.page}쪽</strong>
            </li>
          ))}
        </ol>
      )}
      <div className="block-preview">
        {pages.map((item) => (
          <figure key={item.url}>
            <img src={item.url} alt={`목차 미리보기 ${item.page}쪽`} />
            <figcaption>{item.page}쪽</figcaption>
          </figure>
        ))}
      </div>
    </aside>
  );
}
