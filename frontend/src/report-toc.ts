import { api, jsonRequest } from "./api";
import {
  EditorAdapter,
  type DocumentState,
  type ExportSnapshot,
  type LayoutEvidence,
  type LayoutHeading,
} from "./editor";
import { BlockFenceError, blockHash, blockJson } from "./block-insert";

export const REPORT_TOC_POLICY = "report-native-toc-v1";
export const reportDecode = (value: string) =>
  Uint8Array.from(atob(value), (char) => char.charCodeAt(0));
export function reportEncode(bytes: Uint8Array) {
  const chunks: string[] = [];
  for (let i = 0; i < bytes.length; i += 8192)
    chunks.push(String.fromCharCode(...bytes.subarray(i, i + 8192)));
  return btoa(chunks.join(""));
}
export const reportBytesEqual = (a: Uint8Array, b: Uint8Array) =>
  a.length === b.length && a.every((n, i) => n === b[i]);
export type ReportCapture = {
  adapter: EditorAdapter;
  source: ExportSnapshot;
  state: DocumentState;
};
export function captureReport(adapter: EditorAdapter): ReportCapture {
  const state = adapter.bridge.documentState(),
    source = adapter.exportHwpx();
  if (
    source.contentLoss.count ||
    source.epoch !== state.documentEpoch ||
    blockJson(state) !== blockJson(adapter.bridge.documentState())
  )
    throw new BlockFenceError(
      "초안의 문서 상태 또는 내용 손실 검사를 완료하지 못했습니다.",
    );
  return { adapter, source, state };
}
export function fenceReport(capture: ReportCapture) {
  const current = capture.adapter.exportHwpx();
  if (
    current.contentLoss.count ||
    current.epoch !== capture.source.epoch ||
    current.revision !== capture.source.revision ||
    !reportBytesEqual(current.bytes, capture.source.bytes) ||
    blockJson(capture.state) !==
      blockJson(capture.adapter.bridge.documentState())
  )
    throw new BlockFenceError(
      "목차 준비 후 초안이 변경됐습니다. 후속 편집은 보존했으니 현재 초안에서 다시 갱신하세요.",
    );
}
type TocOutput = {
  request_id: string;
  preview_id: string;
  base_layout_sha256: string;
  source_sha256: string;
  output_base64: string;
  output_sha256: string;
  headings: LayoutHeading[];
  pass_index: number;
  pages: Record<string, number>;
  plan: { toc_paragraph: number };
};
type TocVerified<T> = {
  request_id: string;
  verified: boolean;
  converged: boolean;
  exhausted?: boolean;
  measured_pages: Record<string, number>;
  output_sha256: string;
  preview?: T;
};
export type ReportTocTask<T> = {
  capture: ReportCapture;
  requestId: string;
  previewId: string;
  layoutSha256: string;
  body?: Record<string, unknown>;
  output?: TocOutput;
  passBody?: Record<string, unknown>;
  prepared?: {
    adapter: EditorAdapter;
    capture: ReportCapture;
    outputSha256: string;
    evidence: LayoutEvidence;
  };
  verified?: TocVerified<T>;
  cancelling?: boolean;
};

// The server pins source/headings on toc-prepare. Each phase keeps its exact
// input across response loss. Only this isolated staging editor is loaded.
export async function prepareReportToc<T>(
  task: ReportTocTask<T>,
  options: {
    taskId: string;
    signal: AbortSignal;
    owner: () => void;
    host: () => HTMLElement;
    mounted: (adapter: EditorAdapter | null) => void;
    status: (value: string) => void;
  },
) {
  const { owner, signal } = options;
  const path = `/report-tasks/${options.taskId}/preview`;
  if (!task.body) {
    const hash = await blockHash(task.capture.source.bytes);
    owner();
    task.body = {
      request_id: task.requestId,
      preview_id: task.previewId,
      layout_sha256: task.layoutSha256,
      source_base64: reportEncode(task.capture.source.bytes),
      source_sha256: hash,
    };
  }
  while (true) {
    owner();
    if (!task.output) {
      const output = await api<TocOutput>(
        `${path}/${task.passBody ? "toc-preview" : "toc-prepare"}`,
        { ...jsonRequest("POST", task.passBody || task.body), signal },
      );
      owner();
      if (
        output.request_id !== task.requestId ||
        output.preview_id !== task.previewId ||
        output.base_layout_sha256 !== task.layoutSha256 ||
        output.source_sha256 !== task.body.source_sha256 ||
        output.pass_index !== (task.passBody?.pass_index || 1) ||
        (await blockHash(reportDecode(output.output_base64))) !==
          output.output_sha256
      )
        throw new BlockFenceError(
          "목차 요청·기준 초안·출력 해시가 일치하지 않습니다.",
        );
      owner();
      task.output = output;
    }
    if (!task.prepared) {
      options.status(
        `목차 쪽수 확인 중… ${task.output.pass_index}/3회 실제 렌더링`,
      );
      const instance = await EditorAdapter.mount(options.host());
      try {
        owner();
      } catch (e) {
        instance.destroy();
        throw e;
      }
      options.mounted(instance);
      try {
        await instance.sdk.loadFile(
          reportDecode(task.output.output_base64),
          "보고서-목차-미리보기.hwpx",
          { suppressDialogs: true },
        );
        owner();
        const captured = captureReport(instance),
          outputSha256 = await blockHash(captured.source.bytes);
        owner();
        const evidence = instance.bridge.layoutEvidence(
          task.output.headings.map(
            ({ id, section, paragraph, length, text_sha256 }) => ({
              id,
              section,
              paragraph,
              length,
              text_sha256,
            }),
          ),
        );
        fenceReport(captured);
        if (
          evidence.epoch !== captured.source.epoch ||
          evidence.revision !== captured.source.revision ||
          evidence.export_sha256 !== outputSha256 ||
          evidence.page_count < 1
        )
          throw new BlockFenceError(
            "목차의 실제 렌더링 상태와 출력이 일치하지 않습니다.",
          );
        task.prepared = {
          adapter: instance,
          capture: captured,
          outputSha256,
          evidence,
        };
      } catch (e) {
        instance.destroy();
        options.mounted(null);
        throw e;
      }
    }
    if (!task.verified) {
      const verified = await api<TocVerified<T>>(`${path}/toc-verify`, {
        ...jsonRequest("POST", {
          request_id: task.requestId,
          preview_id: task.previewId,
          layout_sha256: task.layoutSha256,
          pass_index: task.output.pass_index,
          engine_output_base64: reportEncode(
            task.prepared.capture.source.bytes,
          ),
          layout_evidence: task.prepared.evidence,
        }),
        signal,
      });
      owner();
      if (
        !verified.verified ||
        verified.request_id !== task.requestId ||
        verified.output_sha256 !== task.prepared.outputSha256
      )
        throw new BlockFenceError(
          "목차와 기존 초안 보존 검증을 완료하지 못했습니다.",
        );
      task.verified = verified;
    }
    fenceReport(task.prepared.capture);
    if (task.verified.converged) {
      if (!task.verified.preview)
        throw new BlockFenceError("검증한 최종 초안의 저장 기준이 없습니다.");
      return {
        preview: task.verified.preview,
        adapter: task.prepared.adapter,
        captured: task.prepared.capture,
        evidence: task.prepared.evidence,
      };
    }
    if (task.output.pass_index >= 3)
      throw new BlockFenceError(
        "세 번의 렌더링으로 목차 쪽수가 수렴하지 않았습니다. 기존 초안을 보존합니다.",
      );
    task.passBody = {
      request_id: task.requestId,
      preview_id: task.previewId,
      layout_sha256: task.layoutSha256,
      pass_index: task.output.pass_index + 1,
      pages: { ...task.verified.measured_pages },
    };
    task.prepared.adapter.destroy();
    options.mounted(null);
    task.prepared = undefined;
    task.output = undefined;
    task.verified = undefined;
  }
}
