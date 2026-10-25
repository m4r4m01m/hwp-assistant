import { publicPath } from "./public-path";
import type { BodyLayoutEvidence } from "./block-layout";
import {
  createEditor,
  type RhwpEditor,
  type RhwpDocumentStateV1,
  type RhwpSelectionContextV1,
} from "@rhwp/editor";
export type Snapshot = {
  state: RhwpDocumentStateV1;
  selection: RhwpSelectionContextV1;
  evidence: {
    text: string;
    textSha256: string;
    formatSha256: string;
    adjacentContextSha256: string;
  };
  revision: number;
};
export type ExportSnapshot = {
  bytes: Uint8Array;
  contentLoss: { count: number; losses: unknown[] };
  revision: number;
  epoch: number;
};
export type DocumentState = RhwpDocumentStateV1;
export type StyleTarget = {
  kind: "paragraph" | "table";
  section: number;
  paragraph: number;
  control?: number;
};
export type LayoutHeading = {
  id: string;
  section: number;
  paragraph: number;
  length: number;
  text_sha256: string;
};
export type LayoutEvidence = {
  revision: number;
  epoch: number;
  page_count: number;
  export_sha256: string;
  headings: (LayoutHeading & { page: number })[];
};
type Bridge = {
  newDocument(): Promise<void>;
  revision(): number;
  subscribe(cb: () => void): () => void;
  snapshot(): Snapshot;
  documentState(): DocumentState;
  styleTarget?(): StyleTarget;
  exportSnapshot(): ExportSnapshot;
  layoutEvidence(headings: LayoutHeading[]): LayoutEvidence;
  bodyLayoutEvidence?(): BodyLayoutEvidence;
  paragraphPage(section: number, paragraph: number): number;
  warnings(): unknown;
  acknowledge(revision: number, epoch: number, name: string): Promise<boolean>;
};
// Persisted snapshots may return with sorted object keys. Compare every value,
// while preserving array order, rather than treating property order as a change.
function canonicalJson(value: unknown) {
  return JSON.stringify(value, (_key, item) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, item[key]]))
      : item,
  );
}
export class EditorAdapter {
  private attempted = new Map<string, string>();
  constructor(
    readonly sdk: RhwpEditor,
    readonly bridge: Bridge,
  ) {}
  static async mount(container: HTMLElement, options: { signal?: AbortSignal; timeoutMs?: number } = {}) {
    const signal = options.signal;
    if (signal?.aborted) throw new DOMException("편집기 준비를 취소했습니다.", "AbortError");
    const existing = new Set(container.children);
    let abandoned = false;
    const creating = createEditor(container, {
      studioUrl: new URL(publicPath("rhwp/index.html?chrome=embed"), location.origin).href,
    });
    // createEditor appends its iframe before waiting for its load event. Its
    // initial load wait has no timeout, so own that lifecycle here as well.
    const ownedFrames = Array.from(container.children).filter(node => !existing.has(node) && node instanceof HTMLIFrameElement);
    const removeFrames = () => ownedFrames.forEach(frame => frame.remove());
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let cancel = () => {};
    const stopped = new Promise<never>((_resolve, reject) => {
      cancel = () => {
        abandoned = true;
        removeFrames();
        reject(new DOMException("편집기 준비를 취소했습니다.", "AbortError"));
      };
      signal?.addEventListener("abort", cancel, { once: true });
      timeout = setTimeout(() => {
        abandoned = true;
        removeFrames();
        reject(new Error("한글 편집기 준비 시간이 길어졌습니다. 연결 상태를 확인한 뒤 다시 열어 주세요."));
      }, options.timeoutMs ?? 180_000);
    });
    let sdk: RhwpEditor;
    try {
      sdk = await Promise.race([
        creating.then(value => {
          if (abandoned) { value.destroy(); throw new DOMException("편집기 준비를 취소했습니다.", "AbortError"); }
          return value;
        }),
        stopped,
      ]);
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", cancel);
    }
    const bridge = (
      sdk.element.contentWindow as unknown as { hwpAssistantBridge: Bridge }
    ).hwpAssistantBridge;
    if (!bridge) {
      sdk.destroy();
      throw new Error(
        "자체 호스팅 편집기 브리지가 없습니다. npm run engine을 실행하세요.",
      );
    }
    return new EditorAdapter(sdk, bridge);
  }
  async load(bytes: ArrayBuffer, name: string, options: { suppressDialogs?: boolean; skipUnsavedGuard?: boolean } = {}) {
    await this.sdk.loadFile(bytes, name, { suppressDialogs: false, ...options });
    return this.sdk.getDocumentState();
  }
  exportHwpx() {
    return this.bridge.exportSnapshot();
  }
  bodyLayout() {
    if (!this.bridge.bodyLayoutEvidence)
      throw new Error("본문 배치 검증 기능을 사용할 수 없습니다. 현재 편집 내용을 저장하거나 복구용으로 내려받은 뒤 페이지를 다시 열어 주세요. 계속되면 관리자에게 문의하세요. 기존 편집 내용은 그대로입니다.");
    return this.bridge.bodyLayoutEvidence();
  }
  selection() {
    return this.bridge.snapshot();
  }
  styleTarget() {
    if (!this.bridge.styleTarget)
      throw new Error("디자인 적용을 위한 위치 조회 기능이 없습니다. 편집 내용을 저장한 뒤 문서를 다시 열어 주세요.");
    return this.bridge.styleTarget();
  }
  async apply(snapshot: Snapshot, replacement: string, commandId: string = crypto.randomUUID()) {
    const current = this.selection();
    const binding = canonicalJson({ snapshot, replacement });
    const previous = this.attempted.get(commandId);
    if (previous && previous !== binding) throw new Error("같은 적용 ID의 내용이 다릅니다.");
    if (
      !previous && (
      current.revision !== snapshot.revision ||
      canonicalJson(current.selection) !==
        canonicalJson(snapshot.selection) ||
      current.evidence.text !== snapshot.evidence.text)
    )
      throw new Error("문서 또는 선택 위치가 바뀌었습니다. 다시 검토하세요.");
    this.attempted.set(commandId, binding);
    // The installed rhwp journal validates a repeated command's binding and current result.
    return this.sdk.applyTextCommand({
      schemaVersion: 1,
      commandId,
      expectedDocumentEpoch: snapshot.state.documentEpoch,
      expectedChangeSeq: snapshot.state.changeSeq,
      expectedDocumentSha256: snapshot.state.documentSha256,
      // The installed rhwp journal also serializes the target for replay binding.
      target: JSON.parse(canonicalJson(snapshot.selection.target!)),
      expectedBeforeSha256: snapshot.evidence.textSha256,
      expectedFormatSha256: snapshot.evidence.formatSha256,
      expectedAdjacentContextSha256: snapshot.evidence.adjacentContextSha256,
      replacement,
    });
  }
  destroy() {
    this.sdk.destroy();
  }
}
