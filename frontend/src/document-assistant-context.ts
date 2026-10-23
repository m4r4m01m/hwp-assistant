import { unzipSync } from "fflate";
import type { ExportSnapshot, Snapshot } from "./editor";

export type AssistantDocumentContext = { text: string; sha256: string; truncated: boolean; revision: number; epoch: number };
export type AssistantCapture = { base_version: string; document_context: AssistantDocumentContext; selection: Snapshot | null };
const MAX_TEXT = 60000;

// Read the current editor export, never a saved server version. Nested table/frame
// paragraphs are visited in document order and their text is not counted twice.
export async function assistantDocumentContext(source: ExportSnapshot): Promise<AssistantDocumentContext> {
  if (source.contentLoss.count) throw new Error("현재 문서를 손실 없이 읽을 수 없습니다. 복구용 파일을 보관하고 문서를 확인하세요.");
  if (source.bytes.length > 30 * 1024 * 1024) throw new Error("AI가 읽을 문서는 30MB 이하여야 합니다.");
  let expanded = 0, entries = 0;
  const files = unzipSync(source.bytes, { filter: file => {
    if (++entries > 3000) throw new Error("문서 구성 항목이 너무 많습니다.");
    if (!/^Contents\/section\d+\.xml$/.test(file.name)) return false;
    expanded += file.originalSize;
    if (expanded > 30 * 1024 * 1024) throw new Error("문서 본문을 읽을 수 있는 크기를 초과했습니다.");
    return true;
  } });
  const parts: string[] = [];
  let length = 0, truncated = false;
  for (const name of Object.keys(files).sort((a, b) => Number(a.match(/section(\d+)/)![1]) - Number(b.match(/section(\d+)/)![1]))) {
    const xml = new TextDecoder("utf-8", { fatal: true }).decode(files[name]);
    if (/<!DOCTYPE|<!ENTITY/i.test(xml)) throw new Error("안전하게 읽을 수 없는 문서 XML입니다.");
    const root = new DOMParser().parseFromString(xml, "application/xml");
    if (root.getElementsByTagName("parsererror").length) throw new Error("문서 본문 XML을 읽을 수 없습니다.");
    for (const paragraph of Array.from(root.getElementsByTagNameNS("*", "p"))) {
      const text = Array.from(paragraph.getElementsByTagNameNS("*", "t")).filter(node => {
        let parent = node.parentElement;
        while (parent && parent.localName !== "p") parent = parent.parentElement;
        return parent === paragraph;
      }).map(node => node.textContent || "").join("");
      if (!text.trim()) continue;
      let parent = paragraph.parentElement, inTable = false;
      while (parent) { if (parent.localName === "tc") inTable = true; parent = parent.parentElement; }
      const item = (inTable ? "[표 셀] " : "") + text + "\n";
      if (length + item.length > MAX_TEXT) {
        // This is a read-only excerpt, not an edit. Keep the limit explicit and
        // never leave a UTF-16 surrogate half at the excerpt boundary.
        parts.push(item.slice(0, MAX_TEXT - length).replace(/[\uD800-\uDBFF]$/, ""));
        truncated = true; break;
      }
      parts.push(item); length += item.length;
    }
    if (truncated) break;
  }
  const text = parts.join("").trim();
  if (!text) throw new Error("현재 문서에 AI가 읽을 본문이 없습니다. 내용을 입력한 뒤 다시 요청하세요.");
  const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))))
    .map(value => value.toString(16).padStart(2, "0")).join("");
  return { text, sha256, truncated, revision: source.revision, epoch: source.epoch };
}
