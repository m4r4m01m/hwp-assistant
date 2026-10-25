import { useEffect, useRef, useState } from "react";
import { api, ApiError, jsonRequest, type Document } from "./api";
import { EditorAdapter, type ExportSnapshot, type Snapshot } from "./editor";
import { assertBodyLayoutPreserved, type BodyLayoutEvidence, type BlockInsertionPlan } from "./block-layout";

type Block = {
  id: string;
  revision: number;
  sha256: string;
  name: string;
  description: string;
  kind: string;
  fields: {
    name: string;
    label: string;
    required: boolean;
    max_length: number;
  }[];
};
type Catalog = {
  blocks: Block[];
  standard_reference_sha256: string;
  standard_profile_sha256: string;
};
export type BlockCapture = {
  adapter: EditorAdapter;
  document: Document;
  token: string;
  selection: Snapshot;
  source: ExportSnapshot;
  layout: BodyLayoutEvidence;
};
export type PreparedBlock = {
  capture: BlockCapture;
  adapter: EditorAdapter;
  output: ExportSnapshot;
  outputSha256: string;
  requestId: string;
  layout: BodyLayoutEvidence;
  plan: BlockInsertionPlan;
};
export class BlockFenceError extends Error {}
export function verifyBlockLayout(before: BodyLayoutEvidence, after: BodyLayoutEvidence, plan?: BlockInsertionPlan) {
  try { assertBodyLayoutPreserved(before, after, plan); }
  catch (cause) { throw new BlockFenceError(cause instanceof Error ? cause.message : String(cause)); }
}
type Preview = {
  request_id: string;
  input_sha256: string;
  source_sha256: string;
  output_base64: string;
  output_sha256: string;
  block: { id: string; revision: number; sha256: string };
  standard_reference_sha256: string;
  standard_profile_sha256: string;
  plan: BlockInsertionPlan;
};
type Frozen = {
  capture: BlockCapture;
  block: Block;
  catalog: Catalog;
  body?: Record<string, unknown>;
  preview?: Preview;
  prepared?: PreparedBlock;
  verified?: boolean;
  requestId: string;
};

export async function blockHash(bytes: Uint8Array) {
  return Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)),
    ),
  )
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("");
}
function base64(bytes: Uint8Array) {
  const chunks: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 8192)
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + 8192)));
  return btoa(chunks.join(""));
}
function unbase64(value: string) {
  return Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
}
export function blockJson(value: unknown): string {
  return JSON.stringify(value, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(
          Object.keys(item)
            .sort()
            .map((key) => [key, item[key]]),
        )
      : item,
  );
}
export function safeSvg(value: string) {
  if (value.length > 8_000_000 || /<!DOCTYPE|<!ENTITY/i.test(value))
    throw new Error("표시할 수 없는 미리보기입니다.");
  const doc = new DOMParser().parseFromString(value, "image/svg+xml");
  if (
    doc.querySelector("parsererror") ||
    doc.documentElement.localName !== "svg"
  )
    throw new Error("미리보기를 읽을 수 없습니다.");
  const resource = (text: string) =>
    /^(#|data:(image\/(png|jpeg|gif|webp)|font\/(woff|woff2)|application\/(font-woff|x-font-woff));base64,)/i.test(
      text,
    );
  const externalCss = (text: string) =>
    /@import/i.test(text) ||
    Array.from(text.matchAll(/url\s*\(([^)]*)\)/gi)).some(
      (match) => !resource(match[1].trim().replace(/^['"]|['"]$/g, "")),
    );
  for (const node of Array.from(doc.querySelectorAll("*"))) {
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
      ].includes(node.localName)
    )
      throw new Error("활성 요소가 있는 미리보기는 표시할 수 없습니다.");
    for (const attribute of Array.from(node.attributes)) {
      if (
        /^on/i.test(attribute.name) ||
        (attribute.localName === "href" && !resource(attribute.value)) ||
        externalCss(attribute.value)
      )
        throw new Error("외부 리소스가 있는 미리보기는 표시할 수 없습니다.");
    }
    if (node.localName === "style" && externalCss(node.textContent || ""))
      throw new Error("외부 서식이 있는 미리보기는 표시할 수 없습니다.");
  }
  return new XMLSerializer().serializeToString(doc);
}

export function BlockInsert({
  capture,
  isOwner,
  release,
  adopt,
  getStagingHost,
  onClose,
}: {
  capture: () => BlockCapture;
  isOwner: (value: BlockCapture) => boolean;
  release: (value: BlockCapture) => void;
  adopt: (value: PreparedBlock) => Promise<void>;
  getStagingHost: () => HTMLDivElement;
  onClose: () => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null),
    [selected, setSelected] = useState(""),
    [values, setValues] = useState<Record<string, string>>({}),
    [phase, setPhase] = useState("input"),
    [error, setError] = useState(""),
    [pages, setPages] = useState<{ url: string; page: number }[]>([]),
    [notice, setNotice] = useState("");
  const frozen = useRef<Frozen | null>(null),
    active = useRef(true),
    running = useRef(false),
    staging = useRef<EditorAdapter | null>(null),
    urls = useRef<string[]>([]),
    abort = useRef<AbortController | null>(null);
  const block = catalog?.blocks.find((item) => item.id === selected);
  function clear() {
    abort.current?.abort();
    abort.current = null;
    const previous = frozen.current;
    frozen.current = null;
    staging.current?.destroy();
    staging.current = null;
    urls.current.forEach((url) => URL.revokeObjectURL(url));
    urls.current = [];
    if (previous) release(previous.capture);
  }
  useEffect(() => {
    active.current = true;
    const controller = new AbortController();
    void api<Catalog>("/document-blocks", { signal: controller.signal })
      .then((value) => {
        if (active.current) {
          setCatalog(value);
          setSelected(value.blocks[0]?.id || "");
        }
      })
      .catch((e) => {
        if (active.current && !controller.signal.aborted) setError(String(e));
      });
    return () => {
      active.current = false;
      controller.abort();
      clear();
    };
  }, []);
  function owner(task: Frozen) {
    if (!active.current || frozen.current !== task || !isOwner(task.capture))
      throw new BlockFenceError(
        "준비한 문서 세션이 바뀌었습니다. 현재 문단에서 다시 준비하세요.",
      );
  }
  async function prepare() {
    if (running.current || !block || !catalog) return;
    running.current = true;
    setPhase("preparing");
    setError("");
    setNotice("");
    let task = frozen.current;
    const controller = new AbortController();
    abort.current = controller;
    try {
      if (!task) {
        const source = capture();
        task = {
          capture: source,
          block,
          catalog,
          requestId: crypto.randomUUID(),
        };
        frozen.current = task;
        const sourceSha = await blockHash(source.source.bytes);
        owner(task);
        if (source.layout.export_sha256 !== sourceSha || source.layout.revision !== source.source.revision || source.layout.epoch !== source.source.epoch)
          throw new BlockFenceError("원문과 본문 배치 검증 정보가 일치하지 않습니다. 현재 문단에서 다시 준비하세요.");
        task.body = {
          request_id: task.requestId,
          base_version: source.document.current_version,
          block_id: block.id,
          block_revision: block.revision,
          block_sha256: block.sha256,
          source_base64: base64(source.source.bytes),
          source_sha256: sourceSha,
          target: {
            section: 0,
            paragraph: source.selection.selection.target!.paragraph,
            expected_text_sha256: source.selection.evidence.textSha256,
          },
          variables: Object.fromEntries(
            block.fields.map((field) => [field.name, values[field.name] || ""]),
          ),
        };
      }
      owner(task);
      const headers = {
        "Content-Type": "application/json",
        "X-Edit-Session": task.capture.token,
      };
      if (!task.preview) {
        const preview = await api<Preview>(
          `/documents/${task.capture.document.id}/block-preview`,
          {
            ...jsonRequest("POST", task.body),
            headers,
            signal: controller.signal,
          },
        );
        owner(task);
        if (
          preview.request_id !== task.requestId ||
          preview.source_sha256 !== task.body!.source_sha256 ||
          preview.block.id !== task.block.id ||
          preview.block.revision !== task.block.revision ||
          preview.block.sha256 !== task.block.sha256 ||
          preview.standard_reference_sha256 !==
            task.catalog.standard_reference_sha256 ||
          preview.standard_profile_sha256 !==
            task.catalog.standard_profile_sha256 ||
          preview.plan?.insert_after !== task.capture.selection.selection.target!.paragraph ||
          !Number.isSafeInteger(preview.plan?.top_level_count) || preview.plan.top_level_count < 1
        )
          throw new BlockFenceError(
            "블록 기준 또는 준비 결과가 일치하지 않습니다. 다시 준비하세요.",
          );
        if (
          (await blockHash(unbase64(preview.output_base64))) !==
          preview.output_sha256
        )
          throw new BlockFenceError("미리보기 파일 해시가 일치하지 않습니다.");
        owner(task);
        task.preview = preview;
      }
      if (!task.prepared) {
        staging.current?.destroy();
        staging.current = null;
        const instance = await EditorAdapter.mount(getStagingHost(), { signal: controller.signal });
        try {
          owner(task);
        } catch (e) {
          instance.destroy();
          throw e;
        }
        staging.current = instance;
        try {
          await instance.sdk.loadFile(
            unbase64(task.preview.output_base64),
            "블록-삽입-미리보기.hwpx",
            { suppressDialogs: true },
          );
          owner(task);
          const output = instance.exportHwpx();
          if (output.contentLoss.count)
            throw new BlockFenceError(
              "한글 엔진이 내용 손실을 보고했습니다. 기존 편집은 그대로 보존했습니다.",
            );
          let layout: BodyLayoutEvidence;
          try { layout = instance.bodyLayout(); }
          catch (cause) { throw new BlockFenceError(cause instanceof Error ? cause.message : String(cause)); }
          if (layout.revision !== output.revision || layout.epoch !== output.epoch)
            throw new BlockFenceError("미리보기의 문서 식별 또는 편집 리비전이 바뀌었습니다. 다시 준비하세요.");
          const outputSha256 = await blockHash(output.bytes);
          owner(task);
          if (layout.export_sha256 !== outputSha256)
            throw new BlockFenceError("미리보기 배치 검증과 출력 파일이 일치하지 않습니다. 다시 준비하세요.");
          verifyBlockLayout(task.capture.layout, layout, task.preview.plan);
          task.prepared = {
            capture: task.capture,
            adapter: instance,
            output,
            outputSha256,
            requestId: task.requestId,
            layout,
            plan: { ...task.preview.plan },
          };
        } catch (e) {
          if (staging.current === instance) {
            instance.destroy();
            staging.current = null;
          }
          throw e;
        }
      }
      if (!task.verified) {
        const verified = await api<{
          verified: boolean;
          output_sha256: string;
          request_id: string;
          input_sha256: string;
        }>(`/documents/${task.capture.document.id}/block-verify`, {
          ...jsonRequest("POST", {
            ...task.body,
            engine_output_base64: base64(task.prepared.output.bytes),
          }),
          headers,
          signal: controller.signal,
        });
        owner(task);
        if (
          !verified.verified ||
          verified.request_id !== task.requestId ||
          verified.input_sha256 !== task.preview.input_sha256 ||
          verified.output_sha256 !== task.prepared.outputSha256
        )
          throw new BlockFenceError("원문 보존 검증을 완료하지 못했습니다.");
        task.verified = true;
      }
      const count = await task.prepared.adapter.sdk.pageCount();
      owner(task);
      const start = Math.max(
        0,
        Math.min(count - 1, task.capture.selection.selection.page - 1),
      );
      if (!urls.current.length) {
        const images: { url: string; page: number }[] = [];
        let committed = false;
        try {
          for (let index = start; index < Math.min(count, start + 3); index++) {
            const svg = safeSvg(
              await task.prepared.adapter.sdk.getPageSvg(index),
            );
            owner(task);
            const url = URL.createObjectURL(
              new Blob([svg], { type: "image/svg+xml" }),
            );
            images.push({ url, page: index + 1 });
          }
          owner(task);
          urls.current = images.map((image) => image.url);
          setPages(images);
          committed = true;
        } finally {
          if (!committed)
            images.forEach((image) => URL.revokeObjectURL(image.url));
        }
      }
      owner(task);
      setPhase("ready");
    } catch (e) {
      if (!active.current || controller.signal.aborted) return;
      const recoverable =
        !(e instanceof BlockFenceError) &&
        !(e instanceof ApiError && e.status >= 400 && e.status < 500);
      setError(
        `${String(e)}${recoverable && frozen.current ? " · 입력과 요청 ID를 보관했습니다. 같은 준비 요청을 재확인하세요." : " · 기존 편집 내용은 보존됐습니다. 현재 문단에서 다시 준비하세요."}`,
      );
      if (!recoverable) {
        clear();
        setPages([]);
        setPhase("input");
      } else setPhase("retry");
    } finally {
      running.current = false;
      if (abort.current === controller) abort.current = null;
    }
  }
  async function insert() {
    const task = frozen.current;
    if (running.current || !task?.prepared || !task.verified) return;
    running.current = true;
    setPhase("inserting");
    setError("");
    try {
      owner(task);
      await adopt(task.prepared);
      // Ownership transfers to the live editor. Panel cleanup must not destroy it.
      staging.current = null;
      frozen.current = null;
      urls.current.forEach((url) => URL.revokeObjectURL(url));
      urls.current = [];
      setPages([]);
      setNotice(
        "블록을 문단 뒤에 삽입했습니다. 문서에서 바로 편집하고 저장할 수 있습니다.",
      );
      setPhase("input");
    } catch (e) {
      if (!active.current) return;
      setError(`${String(e)} · 기존 편집 내용은 보존됐습니다.`);
      if (
        e instanceof BlockFenceError ||
        (e instanceof ApiError && e.status >= 400 && e.status < 500)
      ) {
        clear();
        setPages([]);
        setPhase("input");
      } else setPhase("ready");
    } finally {
      running.current = false;
    }
  }
  const locked = phase !== "input";
  return (
    <aside className="block-insert-panel" aria-label="표준 블록 삽입">
      <div className="block-panel-heading">
        <h2>표준 블록 삽입</h2>
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
        본문 문단에 커서를 놓고 제목·요약·조치표를 추가하세요. 미저장 내용과
        기존 표를 유지합니다.
      </p>
      <label>
        블록 종류
        <select
          aria-label="삽입할 블록"
          value={selected}
          disabled={locked}
          onChange={(e) => {
            setSelected(e.target.value);
            setValues({});
            setError("");
            setNotice("");
          }}
        >
          {catalog?.blocks.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      {block && <p className="hint">{block.description}</p>}
      {block?.id === "summary" && (
        <p className="hint">
          □ 항목은 2~3줄을 목표로 작성하세요. 내용이 길거나 다른 핵심이 있으면
          요약 블록을 하나 더 추가하세요.
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void prepare();
        }}
      >
        {block?.fields.map((field) => (
          <label key={field.name}>
            {field.label}
            {field.required ? "" : " (선택)"}
            <input
              aria-label={field.label}
              value={values[field.name] || ""}
              disabled={locked}
              maxLength={field.max_length}
              required={field.required}
              onChange={(e) =>
                setValues({ ...values, [field.name]: e.target.value })
              }
            />
          </label>
        ))}
        <button
          type="submit"
          className="primary"
          disabled={
            locked ||
            !block ||
            block.fields.some(
              (field) => field.required && !values[field.name]?.trim(),
            )
          }
        >
          현재 문단 뒤 삽입 미리보기
        </button>
      </form>
      {phase === "preparing" && (
        <p role="status">한글 엔진에서 블록을 준비하고 원문 보존을 확인 중…</p>
      )}
      {phase === "retry" && (
        <button onClick={() => void prepare()}>같은 준비 요청 재확인</button>
      )}
      {(phase === "ready" || phase === "inserting") && (
        <>
          <p className="notice">
            미리보기를 확인한 뒤 삽입하세요. 문서나 커서가 바뀌었으면 다시
            준비해야 합니다.
          </p>
          <button
            className="primary"
            disabled={phase === "inserting"}
            onClick={() => void insert()}
          >
            {phase === "inserting" ? "권한·원문 확인 중…" : "확인한 블록 삽입"}
          </button>
        </>
      )}
      {locked && phase !== "inserting" && (
        <button
          onClick={() => {
            clear();
            setPages([]);
            setPhase("input");
            setError("");
          }}
        >
          준비 취소 · 다시 입력
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
      <div className="block-preview">
        {pages.map((page) => (
          <figure key={page.url}>
            <img src={page.url} alt={`블록 미리보기 ${page.page}쪽`} />
            <figcaption>{page.page}쪽</figcaption>
          </figure>
        ))}
      </div>
    </aside>
  );
}
