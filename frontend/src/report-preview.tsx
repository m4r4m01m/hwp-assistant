import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  api,
  ApiError,
  downloadBytes,
  jsonRequest,
  type Document,
} from "./api";
import {
  EditorAdapter,
  type ExportSnapshot,
  type LayoutHeading,
} from "./editor";
import { BlockFenceError, blockHash, blockJson } from "./block-insert";
import {
  REPORT_TOC_POLICY,
  captureReport,
  fenceReport,
  prepareReportToc,
  reportBytesEqual,
  reportDecode,
  reportEncode,
  type ReportTocTask,
} from "./report-toc";
import {
  ReportProgress,
  ReportTiming,
  type ReportStage,
  type LiveReport,
} from "./report-progress";
import { ReportContent, type ReportBlock } from "./report-content";

type Preview = {
  id: string;
  name: string;
  scaffold_base64: string;
  layout_base64?: string;
  layout_sha256?: string;
  timings?: Record<string, number | null>;
  toc_policy?: string;
  toc_verified?: boolean;
  toc?: {
    id: string;
    headings: LayoutHeading[];
    pages: Record<string, number>;
    page_count: number;
    layout_sha256: string;
  };
  toc_state?: {
    request_id: string;
    base_layout_sha256: string;
    prepare_input: Record<string, unknown>;
  };
  plan: (ReportBlock & {
    paragraph: number;
    before: string;
    command_id: string;
  })[];
};
type Owner = { taskId: string; generation: number; alive: boolean };
type Operation = {
  kind: "prepare" | "toc" | "save";
  owner: Owner;
  controller: AbortController;
};
type Saving = {
  owner: Owner;
  adapter: EditorAdapter;
  snapshot: ExportSnapshot;
  body: {
    id: string;
    preview_id: string;
    layout_sha256: string;
    output_base64: string;
  };
};

export function ReportPreview({
  taskId,
  draftHash,
  onLock,
  onSaved,
  onExit,
  sourceBlocks,
  toolbarHost,
  workspacePanelOpen,
  onShowDetails,
}: {
  taskId: string;
  draftHash: string;
  onLock: (locked: boolean) => void;
  onSaved: (doc: Document, options?: { unsavedChanges: boolean }) => void;
  onExit: () => void;
  sourceBlocks?: LiveReport["blocks"];
  toolbarHost?: HTMLElement | null;
  workspacePanelOpen?: boolean;
  onShowDetails?: () => void;
}) {
  const hosts = useRef<(HTMLDivElement | null)[]>([null, null]),
    editor = useRef<EditorAdapter | null>(null),
    stageEditor = useRef<EditorAdapter | null>(null);
  const ownerRef = useRef<Owner | null>(null),
    generation = useRef(0),
    operation = useRef<Operation | null>(null);
  const prepared = useRef<Preview | null>(null),
    prepareBody = useRef<unknown>(null),
    renderBody = useRef<unknown>(null),
    tocTask = useRef<ReportTocTask<Preview> | null>(null),
    savingBody = useRef<Saving | null>(null);
  const readyRef = useRef(false),
    savedRef = useRef(false),
    cachedTocRefresh = useRef(false);
  const [busy, setBusy] = useState(true),
    [ready, setReady] = useState(false),
    [saved, setSaved] = useState(false),
    [pendingSave, setPendingSave] = useState(false),
    [dirty, setDirty] = useState(false),
    [error, setError] = useState(""),
    [status, setStatus] = useState("한글 서식 초안을 준비합니다."),
    [pages, setPages] = useState(0),
    [tocStatus, setTocStatus] = useState<
      "legacy" | "pending" | "current" | "stale"
    >("legacy"),
    [tocBusy, setTocBusy] = useState(false),
    [tocRetry, setTocRetry] = useState(false),
    [cancelRetry, setCancelRetry] = useState(false),
    [activeHost, setActiveHost] = useState(0);
  const [stage, setStage] = useState<ReportStage>("applying"),
    [applied, setApplied] = useState<Preview["plan"]>([]),
    [total, setTotal] = useState(0),
    [showProcess, setShowProcess] = useState(false);
  const processPace = useRef(false),
    livePaper = useRef<HTMLDivElement>(null),
    off = useRef<(() => void) | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(true);
  useEffect(() => { if (ready) setDetailsOpen(false); }, [ready]);
  useEffect(() => {
    if (workspacePanelOpen) setDetailsOpen(false);
    else if (!ready) setDetailsOpen(true);
  }, [workspacePanelOpen]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setDetailsOpen(false); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, []);
  useEffect(() => {
    if (error) { setDetailsOpen(true); onShowDetails?.(); }
  }, [error]);
  function owner(value: Owner) {
    if (!value.alive || ownerRef.current !== value || value.taskId !== taskId)
      throw new BlockFenceError("다른 보고서 초안의 늦은 응답입니다.");
  }
  function owns(value: Owner) {
    return value.alive && ownerRef.current === value;
  }
  function activate(index: number) {
    hosts.current.forEach((host, candidate) => {
      if (host) {
        host.classList.toggle("active", candidate === index);
        host.inert = candidate !== index;
        host.setAttribute("aria-hidden", String(candidate !== index));
      }
    });
    setActiveHost(index);
  }
  function watch(instance: EditorAdapter, value: Owner) {
    off.current?.();
    off.current = instance.bridge.subscribe(() => {
      if (!owns(value) || editor.current !== instance) return;
      setDirty(true);
      if (prepared.current?.toc_policy === REPORT_TOC_POLICY)
        setTocStatus("stale");
    });
  }
  function freeHost() {
    const host = hosts.current.find(
      (node) => node && !node.querySelector("iframe"),
    );
    if (!host)
      throw new BlockFenceError("미리보기 편집기 공간을 확인하지 못했습니다.");
    return host;
  }
  function discardStage() {
    stageEditor.current?.destroy();
    stageEditor.current = null;
  }
  function cancelServer(value: Owner, task: ReportTocTask<Preview>) {
    return api<{ preview: Preview }>(
      `/report-tasks/${value.taskId}/preview/toc-cancel`,
      {
        ...jsonRequest("POST", {
          request_id: task.requestId,
          preview_id: task.previewId,
          layout_sha256: task.layoutSha256,
        }),
        keepalive: true,
      },
    );
  }
  async function settleToc(
    value: Owner,
    task: ReportTocTask<Preview>,
    op: Operation,
  ) {
    const check = () => {
      owner(value);
      if (operation.current !== op || tocTask.current !== task)
        throw new BlockFenceError("목차 종료 요청의 소유자가 바뀌었습니다.");
    };
    task.cancelling = true;
    task.prepared = undefined;
    discardStage();
    let current: Preview;
    try {
      current = (await cancelServer(value, task)).preview;
    } catch (e) {
      check();
      if (!(e instanceof ApiError && e.status >= 400 && e.status < 500))
        throw e;
      // A rejected prepare never created pending work; a failed commit response
      // may already have changed the layout. Read metadata without loading it.
      current = await api<Preview>(`/report-tasks/${value.taskId}/preview`);
    }
    check();
    if (
      current.id !== task.previewId ||
      current.toc_state?.request_id === task.requestId
    )
      throw new BlockFenceError(
        "서버 목차 준비의 종료를 확인하지 못했습니다. 같은 취소를 재확인하세요.",
      );
    prepared.current = current;
    tocTask.current = null;
    setCancelRetry(false);
    setTocRetry(false);
  }
  useEffect(() => {
    const scroller = livePaper.current?.parentElement;
    scroller?.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
  }, [applied.length]);
  useEffect(() => {
    const value: Owner = {
      taskId,
      generation: ++generation.current,
      alive: true,
    };
    ownerRef.current = value;
    prepared.current = null;
    prepareBody.current = null;
    renderBody.current = null;
    savingBody.current = null;
    tocTask.current = null;
    readyRef.current = false;
    savedRef.current = false;
    cachedTocRefresh.current = false;
    setReady(false);
    setSaved(false);
    setPendingSave(false);
    setDirty(false);
    setTocRetry(false);
    setCancelRetry(false);
    setTocStatus("legacy");
    void prepare(value);
    return () => {
      value.alive = false;
      if (ownerRef.current !== value) return;
      operation.current?.controller.abort();
      const task = tocTask.current;
      if (task) void cancelServer(value, task).catch(() => {});
      off.current?.();
      off.current = null;
      editor.current?.destroy();
      editor.current = null;
      discardStage();
      if (ownerRef.current === value) {
        ownerRef.current = null;
        operation.current = null;
        tocTask.current = null;
        onLock(false);
      }
    };
  }, [taskId]);
  useEffect(() => {
    if (!ready || (saved && !dirty)) return;
    const guard = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [ready, saved, dirty]);

  async function runToc(value: Owner, op: Operation, initial: boolean) {
    owner(value);
    if (savingBody.current || savedRef.current)
      throw new BlockFenceError(
        "미해결 저장은 같은 출력으로 먼저 재확인하세요. 확정한 보고서는 저장된 문서에서 갱신하세요.",
      );
    const source = editor.current,
      preview = prepared.current;
    if (!source || !preview?.layout_sha256)
      throw new BlockFenceError("목차를 갱신할 초안이 없습니다.");
    let task = tocTask.current;
    if (task?.cancelling)
      throw new BlockFenceError("이전 목차 준비의 취소를 먼저 재확인하세요.");
    if (!task) {
      task = {
        capture: captureReport(source),
        requestId: crypto.randomUUID(),
        previewId: preview.id,
        layoutSha256: preview.layout_sha256,
      };
      if (initial && preview.toc_state) {
        task.requestId = preview.toc_state.request_id;
        task.layoutSha256 = preview.toc_state.base_layout_sha256;
        task.body = preview.toc_state.prepare_input;
      }
      tocTask.current = task;
    }
    const ownTask = () => {
      owner(value);
      if (
        operation.current !== op ||
        tocTask.current !== task ||
        editor.current !== task!.capture.adapter
      )
        throw new BlockFenceError("목차 준비의 소유자가 바뀌었습니다.");
    };
    setTocBusy(true);
    setTocRetry(false);
    try {
      const result = await prepareReportToc(task, {
        taskId: value.taskId,
        signal: op.controller.signal,
        owner: ownTask,
        host: freeHost,
        mounted: (instance) => {
          if (owns(value) && tocTask.current === task)
            stageEditor.current = instance;
        },
        status: (text) => {
          ownTask();
          setStatus(text);
        },
      });
      ownTask();
      // A server commit can precede a failed live fence. Reconcile metadata only;
      // never reload or replace user input while handling that failure.
      prepared.current = result.preview;
      if (
        result.preview.id !== task.previewId ||
        result.preview.layout_sha256 !== task.prepared!.outputSha256 ||
        !result.preview.toc_verified
      )
        throw new BlockFenceError(
          "최종 목차의 초안 식별과 저장 기준이 일치하지 않습니다.",
        );
      fenceReport(task.capture);
      fenceReport(result.captured);
      const index = hosts.current.findIndex(
        (host) => host === result.adapter.sdk.element.parentElement,
      );
      if (index < 0 || !result.adapter.sdk.element.isConnected)
        throw new BlockFenceError(
          "목차 편집기의 컨테이너를 확인하지 못했습니다.",
        );
      // Final fences, actual DOM activation and adapter transfer are synchronous.
      off.current?.();
      activate(index);
      editor.current = result.adapter;
      stageEditor.current = null;
      watch(result.adapter, value);
      source.destroy();
      tocTask.current = null;
      setTocRetry(false);
      setTocStatus("current");
      setPages(result.evidence.page_count);
      setDirty(!initial);
      return true;
    } catch (e) {
      if (
        !owns(value) ||
        operation.current !== op ||
        op.controller.signal.aborted
      )
        return false;
      const recoverable =
        !(e instanceof BlockFenceError) &&
        !(e instanceof ApiError && e.status >= 400 && e.status < 500);
      if (recoverable) {
        setTocRetry(true);
        setError(
          `${String(e)} · 목차 입력과 요청 ID를 보관했습니다. 같은 목차 준비를 재확인하세요.`,
        );
      } else {
        if (task.verified?.preview) prepared.current = task.verified.preview;
        try {
          await settleToc(value, task, op);
        } catch (cancelError) {
          if (!owns(value) || operation.current !== op) return false;
          setCancelRetry(true);
          setError(
            `${String(e)} · ${String(cancelError)} · 현재 초안과 요청 ID를 보존했습니다. 목차 취소를 재확인하세요.`,
          );
          return false;
        }
        owner(value);
        if (operation.current !== op) return false;
        setTocStatus(initial ? "pending" : "stale");
        setTocRetry(false);
        setError(`${String(e)} · 현재 초안과 후속 편집은 그대로 보존했습니다.`);
      }
      return false;
    } finally {
      if (owns(value) && operation.current === op) setTocBusy(false);
    }
  }
  async function prepare(value = ownerRef.current!) {
    if (!value || operation.current || readyRef.current) return;
    const op: Operation = {
      kind: "prepare",
      owner: value,
      controller: new AbortController(),
    };
    operation.current = op;
    setBusy(true);
    setError("");
    onLock(true);
    try {
      owner(value);
      let preview: Preview | null = null;
      try {
        preview = await api<Preview>(`/report-tasks/${value.taskId}/preview`, {
          signal: op.controller.signal,
        });
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 404)) throw e;
      }
      owner(value);
      if (!editor.current) {
        const mounted = await EditorAdapter.mount(hosts.current[0]!);
        if (!owns(value)) {
          mounted.destroy();
          return;
        }
        editor.current = mounted;
        activate(0);
      }
      const adapter = editor.current;
      if (!preview) {
        if (!prepareBody.current) {
          await adapter.bridge.newDocument();
          owner(value);
          const seed = adapter.exportHwpx();
          if (seed.contentLoss.count)
            throw new Error("빈 문서 출력 검사에 실패했습니다.");
          prepareBody.current = {
            id: crypto.randomUUID(),
            draft_sha256: draftHash,
            seed_base64: reportEncode(seed.bytes),
          };
        }
        preview = await api<Preview>(`/report-tasks/${value.taskId}/preview`, {
          ...jsonRequest("POST", prepareBody.current),
          signal: op.controller.signal,
        });
        owner(value);
      }
      prepared.current = preview;
      setTotal(preview.plan.length);
      if (!preview.layout_base64) {
        if (!renderBody.current) {
          setApplied([]);
          setStage("applying");
          const started = performance.now();
          await adapter.sdk.loadFile(
            reportDecode(preview.scaffold_base64),
            preview.name,
            { suppressDialogs: true, skipUnsavedGuard: true },
          );
          owner(value);
          const receipts = [];
          for (const [i, item] of preview.plan.entries()) {
            owner(value);
            setStatus(
              `문서에 내용 작성 중… ${i + 1}/${preview.plan.length} 문단`,
            );
            await adapter.sdk.focusTarget({
              kind: "body_paragraph",
              section: 0,
              paragraph: item.paragraph,
              charOffset: 0,
              length: [...item.before].length,
            });
            owner(value);
            const snapshot = adapter.selection();
            if (
              snapshot.evidence.text !== item.before ||
              snapshot.selection.target?.paragraph !== item.paragraph
            )
              throw new Error("초안의 적용 위치가 달라졌습니다.");
            receipts.push(
              await adapter.apply(snapshot, item.text, item.command_id),
            );
            owner(value);
            setApplied(preview.plan.slice(0, i + 1));
            if (processPace.current) {
              await new Promise((resolve) => setTimeout(resolve, 220));
              owner(value);
            }
          }
          const exported = adapter.exportHwpx(),
            state = await adapter.sdk.getDocumentState();
          owner(value);
          if (exported.contentLoss.count)
            throw new Error("초안 출력에서 내용 손실이 감지되었습니다.");
          renderBody.current = {
            preview_id: preview.id,
            output_base64: reportEncode(exported.bytes),
            receipts,
            document_sha256: state.documentSha256,
            client_apply_ms: Math.round(performance.now() - started),
          };
        }
        setStage("formatting");
        setStatus("서식 적용 중… 제목선·내어쓰기·페이지 배치를 정리합니다.");
        preview = await api<Preview>(
          `/report-tasks/${value.taskId}/preview/render`,
          {
            ...jsonRequest("POST", renderBody.current),
            signal: op.controller.signal,
          },
        );
        owner(value);
        prepared.current = preview;
      }
      setStage("formatting");
      setStatus("페이지 배치를 확인합니다.");
      // Pre-ready only; retry keeps an untouched frozen TOC source.
      if (!tocTask.current) {
        await adapter.sdk.loadFile(
          reportDecode(preview.layout_base64!),
          preview.name,
          { suppressDialogs: true, skipUnsavedGuard: true },
        );
        owner(value);
      }
      let count: number;
      if (preview.toc_policy === REPORT_TOC_POLICY) {
        setTocStatus("pending");
        if (
          preview.toc_verified &&
          preview.toc &&
          !tocTask.current &&
          !cachedTocRefresh.current
        ) {
          const captured = captureReport(adapter),
            evidence = adapter.bridge.layoutEvidence(preview.toc.headings);
          fenceReport(captured);
          if (
            (await blockHash(captured.source.bytes)) !== preview.layout_sha256
          )
            throw new BlockFenceError(
              "저장된 초안과 재열기 출력의 전체 바이트가 다릅니다. 현재 출력을 보존하고 재열기 검증을 중단했습니다.",
            );
          owner(value);
          fenceReport(captured);
          if (
            blockJson(
              Object.fromEntries(
                evidence.headings.map((item) => [item.id, item.page]),
              ),
            ) !== blockJson(preview.toc.pages)
          ) {
            cachedTocRefresh.current = true;
            throw new BlockFenceError(
              "저장된 목차의 쪽수가 현재 렌더링과 다릅니다. 준비 재시도로 실제 쪽수를 갱신하세요.",
            );
          }
          count = evidence.page_count;
          setTocStatus("current");
        } else {
          if (!(await runToc(value, op, true))) return;
          owner(value);
          count = await editor.current!.sdk.pageCount();
          owner(value);
        }
      } else count = await adapter.sdk.pageCount();
      owner(value);
      setPages(count);
      readyRef.current = true;
      setReady(true);
      setStage("ready");
      setStatus(
        "한글 초안 · 아래 문서에서 바로 편집할 수 있습니다. 아직 서버 문서로 저장하지 않았습니다.",
      );
      watch(editor.current!, value);
    } catch (e) {
      if (owns(value) && !op.controller.signal.aborted) {
        setError(String(e));
        setStatus(
          "한글 초안 준비를 완료하지 못했습니다. 생성한 내용은 보존되어 있습니다.",
        );
      }
    } finally {
      if (owns(value) && operation.current === op) {
        operation.current = null;
        setBusy(false);
      }
    }
  }
  async function refreshToc() {
    const value = ownerRef.current;
    if (
      !value ||
      operation.current ||
      !readyRef.current ||
      savingBody.current ||
      savedRef.current ||
      prepared.current?.toc_policy !== REPORT_TOC_POLICY
    )
      return;
    const op: Operation = {
      kind: "toc",
      owner: value,
      controller: new AbortController(),
    };
    operation.current = op;
    setError("");
    try {
      if (await runToc(value, op, false)) {
        owner(value);
        setStatus(
          "목차 쪽수를 갱신했습니다. 현재 초안에서 계속 편집할 수 있습니다.",
        );
      }
    } catch (e) {
      if (owns(value)) setError(String(e));
    } finally {
      if (owns(value) && operation.current === op) operation.current = null;
    }
  }
  async function cancelToc() {
    const value = ownerRef.current,
      task = tocTask.current,
      previous = operation.current;
    if (
      !value ||
      !task ||
      previous?.kind === "save" ||
      (task.cancelling && previous)
    )
      return;
    previous?.controller.abort();
    const op: Operation = {
      kind: "toc",
      owner: value,
      controller: new AbortController(),
    };
    operation.current = op;
    setTocBusy(true);
    setTocRetry(false);
    setError("");
    try {
      await settleToc(value, task, op);
      owner(value);
    } catch (e) {
      if (owns(value) && operation.current === op) {
        setCancelRetry(true);
        setError(
          `${String(e)} · 서버 목차 준비의 취소를 확인하지 못했습니다. 요청 ID를 보관했습니다.`,
        );
      }
    } finally {
      if (owns(value) && operation.current === op) {
        operation.current = null;
        setBusy(false);
        setTocBusy(false);
        setTocStatus(readyRef.current ? "stale" : "pending");
      }
    }
  }
  async function save() {
    const value = ownerRef.current,
      instance = editor.current,
      preview = prepared.current;
    if (
      !value ||
      !instance ||
      !preview ||
      !readyRef.current ||
      operation.current ||
      tocTask.current ||
      savedRef.current
    )
      return;
    const op: Operation = {
      kind: "save",
      owner: value,
      controller: new AbortController(),
    };
    operation.current = op;
    setBusy(true);
    setError("");
    try {
      if (!savingBody.current) {
        const exported = instance.exportHwpx();
        if (exported.contentLoss.count)
          throw new Error("내용 손실이 감지되어 저장하지 않았습니다.");
        savingBody.current = {
          owner: value,
          adapter: instance,
          snapshot: exported,
          body: {
            id: crypto.randomUUID(),
            preview_id: preview.id,
            layout_sha256: preview.layout_sha256!,
            output_base64: reportEncode(exported.bytes),
          },
        };
      }
      const frozen = savingBody.current;
      if (frozen.owner !== value || frozen.adapter !== instance)
        throw new BlockFenceError("저장 재확인의 보고서 세션이 다릅니다.");
      setPendingSave(true);
      setStatus("확인한 한글 초안과 편집 내용을 저장합니다.");
      const doc = await api<Document>(
        `/report-tasks/${value.taskId}/preview/save`,
        { ...jsonRequest("POST", frozen.body), signal: op.controller.signal },
      );
      owner(value);
      const current = instance.exportHwpx();
      if (current.contentLoss.count || current.epoch !== frozen.snapshot.epoch)
        throw new BlockFenceError(
          "저장은 접수됐지만 현재 편집 버퍼의 문서 상태를 확인하지 못했습니다. 복구용 출력을 확인하세요.",
        );
      const changed =
        current.revision !== frozen.snapshot.revision ||
        !reportBytesEqual(current.bytes, frozen.snapshot.bytes);
      savedRef.current = true;
      setSaved(true);
      setPendingSave(false);
      setDirty(changed);
      if (changed) {
        setStatus(
          "서버 저장은 확인됐지만 후속 편집은 미저장입니다. 현재 초안을 다운로드하세요.",
        );
        onLock(true);
        onSaved(doc, { unsavedChanges: true });
      } else {
        setStatus("HWPX 서버 저장 완료");
        onLock(false);
        onSaved(doc);
      }
    } catch (e) {
      if (owns(value) && !op.controller.signal.aborted) {
        setError(String(e));
        setStatus(
          "편집한 출력과 저장 키를 보존했습니다. 같은 저장 결과를 재확인하세요.",
        );
      }
    } finally {
      if (owns(value) && operation.current === op) {
        operation.current = null;
        setBusy(false);
      }
    }
  }
  function download() {
    try {
      const exported = editor.current?.exportHwpx();
      if (!exported || exported.contentLoss.count)
        throw new Error("복구용 출력 검사를 완료하지 못했습니다.");
      downloadBytes(exported.bytes, prepared.current?.name || "한글 초안.hwpx");
    } catch (e) {
      setError(String(e));
    }
  }
  const previewStatus = `${pages ? `${pages}쪽 · ` : ""}${saved
    ? dirty ? "서버 저장 확인 · 후속 편집 미저장" : "저장된 보고서"
    : dirty ? "편집한 초안 · 미저장" : "한글 서식 미리보기"}`;
  const toolbar = (
      <div className="formatted-report-toolbar" aria-label="한글 초안 작업 도구">
        {ready && !saved && (
          <button
            className="primary"
            aria-label={pendingSave ? "같은 보고서 저장 재확인" : "초안 확정·서버 저장"}
            title={pendingSave ? "같은 보고서 저장 재확인" : "초안 확정·서버 저장"}
            disabled={busy || tocBusy || Boolean(tocTask.current)}
            onClick={() => void save()}
          >
            {pendingSave ? <><span>저장</span><span>재확인</span></> : <><span>초안 확정</span><span>서버 저장</span></>}
          </button>
        )}
        {ready &&
          prepared.current?.toc_policy === REPORT_TOC_POLICY &&
          !saved && (
            <button
              aria-label={tocRetry ? "같은 목차 준비 재확인" : "목차 쪽수 갱신"}
              title={tocRetry ? "같은 목차 준비 재확인" : "목차 쪽수 갱신"}
              disabled={busy || tocBusy || pendingSave || cancelRetry}
              onClick={() => void refreshToc()}
            >
              {tocRetry ? <><span>목차 준비</span><span>재확인</span></> : <span>목차 갱신</span>}
            </button>
          )}
        {(((tocBusy || tocRetry) && ready) || cancelRetry) && (
          <button
            aria-label={cancelRetry ? "같은 목차 취소 재확인" : "목차 갱신 취소"}
            title={cancelRetry ? "같은 목차 취소 재확인" : "목차 갱신 취소"}
            disabled={Boolean(tocTask.current?.cancelling && operation.current)}
            onClick={() => void cancelToc()}
          >
            {cancelRetry ? <><span>목차 취소</span><span>재확인</span></> : <><span>목차 갱신</span><span>취소</span></>}
          </button>
        )}
        {ready && (
          <button disabled={busy} onClick={download}
            aria-label={saved && dirty ? "후속 편집 복구용 다운로드" : "현재 초안 다운로드"}
            title={saved && dirty ? "후속 편집 복구용 다운로드" : "현재 초안 다운로드"}>
            {saved && dirty
              ? <><span>후속 편집</span><span>복구 출력</span></>
              : <span>다운로드</span>}
          </button>
        )}
        <button className={error || tocStatus === "stale" ? "report-status-attention" : ""}
          aria-label="초안 상태와 진행 정보" aria-expanded={detailsOpen} aria-controls="report-preview-status-panel"
          title={previewStatus} onClick={() => {
            if (!detailsOpen) onShowDetails?.();
            setDetailsOpen(value => !value);
          }}>
          <span>{pages ? `${pages}쪽` : "진행"}</span><span>{error ? "상태 !" : tocStatus === "stale" ? "갱신 필요" : "상태"}</span>
        </button>
        {!ready && !busy && !cancelRetry && (
          <button aria-label="한글 초안 준비 재시도" title="한글 초안 준비 재시도" onClick={() => void prepare()}><span>초안 준비</span><span>재시도</span></button>
        )}
        {!busy && !tocBusy && !pendingSave && (!saved || dirty) && (
          <button
            aria-label="초안 편집 종료" title="초안 편집 종료"
            onClick={() => {
              if (
                !dirty ||
                window.confirm(
                  "직접 편집한 내용이 서버에 저장되지 않았습니다. 편집을 종료할까요?",
                )
              )
                onExit();
            }}
          >
            <span>편집 종료</span>
          </button>
        )}
      </div>
  );
  return (
    <div className="formatted-report">
      {toolbarHost ? createPortal(toolbar, toolbarHost) : toolbar}
      <p role="status" className="report-accessible-status">{status}</p>
      <aside id="report-preview-status-panel" className="report-workspace-drawer report-status-drawer" hidden={!detailsOpen} aria-label="초안 상태와 진행 정보">
        <div className="report-drawer-heading"><h2>초안 상태</h2><button aria-label="초안 상태 닫기" onClick={() => setDetailsOpen(false)}>×</button></div>
        <p>{previewStatus}</p>
        <ReportProgress stage={stage} count={applied.length} total={total} />
        {!ready && <label className="check-label"><input type="checkbox" checked={showProcess} onChange={e => {
          setShowProcess(e.target.checked); processPace.current = e.target.checked;
        }} />작성 과정 천천히 보기</label>}
        {tocStatus !== "legacy" && (
        <p className="notice" aria-label="초안 목차 상태">
          {tocStatus === "current"
            ? "목차 쪽수 검증 완료 · 현재 한글 엔진의 실제 쪽수입니다."
            : tocStatus === "stale"
              ? "목차 갱신 필요 · 본문 수정 후 실제 쪽수를 다시 확인하세요."
              : "목차와 실제 쪽수를 준비 중입니다."}
          {pendingSave && " 저장 재확인이 남아 있어 목차 변경을 보류합니다."}
        </p>
      )}
      <p>{status}</p>
      <ReportTiming timings={prepared.current?.timings} />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      </aside>
      <div
        className="formatted-report-surface"
        aria-label="한글 초안 편집기"
        aria-busy={busy || tocBusy}
        data-ready={ready}
      >
        <div
          className={`formatted-report-editor ${!ready ? "report-staging-editor" : ""}`}
          inert={!ready || busy || saved}
        >
          {[0, 1].map((index) => (
            <div
              key={index}
              ref={(node) => {
                hosts.current[index] = node;
              }}
              className={`report-editor-slot${activeHost === index ? " active" : ""}`}
              inert={activeHost !== index}
              aria-hidden={activeHost !== index}
            />
          ))}
        </div>
        {!ready && (
          <div
            className="report-live-scroll"
            aria-label="작성 중인 문단 미리보기"
          >
            <div ref={livePaper} className="report-live-paper">
              <ReportContent
                blocks={sourceBlocks?.length ? sourceBlocks : applied}
                testId={
                  sourceBlocks?.length ? undefined : "applied-report-paragraph"
                }
              />
              {!sourceBlocks?.length && !applied.length && (
                <p className="hint">한글 문서 틀을 준비합니다…</p>
              )}
              <p className="report-live-caption">
                {stage === "formatting"
                  ? "서식 적용과 실제 목차 쪽수 확인 중… 곧 편집 가능한 한글 문서가 열립니다."
                  : "문서에 입력된 내용입니다. 최종 페이지 배치는 다음 단계에서 적용합니다."}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
