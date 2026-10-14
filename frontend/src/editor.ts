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
  exportSnapshot(): ExportSnapshot;
  layoutEvidence(headings: LayoutHeading[]): LayoutEvidence;
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
  static async mount(container: HTMLElement) {
    const sdk = await createEditor(container, {
      studioUrl: new URL("/rhwp/index.html?chrome=embed", location.origin).href,
    });
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
  async load(bytes: ArrayBuffer, name: string) {
    await this.sdk.loadFile(bytes, name, { suppressDialogs: false });
    return this.sdk.getDocumentState();
  }
  exportHwpx() {
    return this.bridge.exportSnapshot();
  }
  selection() {
    return this.bridge.snapshot();
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
