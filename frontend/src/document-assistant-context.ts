import { unzipSync } from "fflate";
import type { ExportSnapshot, Snapshot, StyleTarget } from "./editor";
import type { BodyLayoutEvidence } from "./block-layout";

export type AssistantDocumentContext = { text: string; sha256: string; truncated: boolean; revision: number; epoch: number };
export type AssistantDesignTarget = { kind: "paragraph" | "table"; section: 0; paragraph: number; control?: number };
export type AssistantDesignContext = { source_sha256: string; target: AssistantDesignTarget | null;
  target_text?: string; target_text_truncated?: boolean; target_is_title_frame?: boolean };
export type AssistantRewriteParagraph = { section: 0; paragraph: number; text: string; text_sha256: string; length: number; fixed_prefix: string; protected_text?: string[]; rendered_lines?: number };
export type AssistantRewriteContext = { source_sha256: string; paragraphs: AssistantRewriteParagraph[]; preserved: Record<string, number>; unavailable: string };
export type AssistantCapture = { base_version: string; document_context: AssistantDocumentContext; selection: Snapshot | null; design_context?: AssistantDesignContext | null; rewrite_context?: AssistantRewriteContext | null };
const MAX_TEXT = 60000;
const HP = "http://www.hancom.co.kr/hwpml/2011/paragraph", HH = "http://www.hancom.co.kr/hwpml/2011/head";
const directControls = new Set(["tbl", "pic", "rect", "ellipse", "line", "connectLine", "arc", "polygon", "curve",
  "container", "compose", "dutmal", "equation", "btn", "checkBtn", "radioBtn", "comboBox", "edit"]);
const wrappedControls = new Set(["colPr", "header", "footer", "footNote", "endNote", "autoNum", "hiddenComment", "indexmark",
  "fieldBegin", "pageHiding", "pageNumCtrl", "pageNum", "bookmark", "newNum"]);
const children = (element: Element, name: string) => Array.from(element.children).filter(child => child.namespaceURI === HP && child.localName === name);
const digest = async (bytes: Uint8Array) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)))).map(value => value.toString(16).padStart(2, "0")).join("");

// Read-only scope discovery. Native exact commands currently support complete,
// single-format body paragraphs; expose every excluded class instead of silently
// treating a partial plan as an edit to the whole document.
export async function assistantRewriteContext(source: ExportSnapshot, layout?: BodyLayoutEvidence): Promise<AssistantRewriteContext> {
  if (source.contentLoss.count) throw new Error("내용 손실이 감지된 문서는 현재 문장 범위를 확인할 수 없습니다.");
  const result: AssistantRewriteContext = { source_sha256: await digest(source.bytes), paragraphs: [], preserved: {}, unavailable: "" };
  if (layout && (layout.revision !== source.revision || layout.epoch !== source.epoch || layout.export_sha256 !== result.source_sha256))
    throw new Error("문장과 실제 배치의 확인 시점이 다릅니다. 다시 요청하세요.");
  if (source.bytes.length > 10 * 1024 * 1024) {
    result.unavailable = "10MB를 넘는 문서는 전체 문장 수정이 제한됩니다. 문서 내용에 대한 질문은 가능합니다.";
    return result;
  }
  let expanded = 0, entries = 0;
  const files = unzipSync(source.bytes, { filter: file => {
    if (++entries > 3000) throw new Error("문서 구성 항목이 너무 많습니다.");
    if (!/^Contents\/section\d+\.xml$/.test(file.name) && file.name !== "Contents/header.xml") return false;
    expanded += file.originalSize;
    if (expanded > 30 * 1024 * 1024) throw new Error("문서 본문 크기를 초과했습니다.");
    return true;
  } });
  if (Object.keys(files).filter(name => /^Contents\/section\d+\.xml$/.test(name)).length !== 1 || !files["Contents/section0.xml"]) {
    result.unavailable = "여러 구역으로 구성된 문서는 전체 문장 수정을 지원하지 않습니다. 문서 내용에 대한 질문은 가능합니다.";
    return result;
  }
  const xml = new TextDecoder("utf-8", { fatal: true }).decode(files["Contents/section0.xml"]);
  if (/<!DOCTYPE|<!ENTITY/i.test(xml)) throw new Error("안전하게 읽을 수 없는 문서 XML입니다.");
  const root = new DOMParser().parseFromString(xml, "application/xml");
  if (root.querySelector("parsererror")) throw new Error("본문 XML을 읽을 수 없습니다.");
  const headerXml = files["Contents/header.xml"] ? new TextDecoder("utf-8", { fatal: true }).decode(files["Contents/header.xml"]) : "<header/>";
  if (/<!DOCTYPE|<!ENTITY/i.test(headerXml)) throw new Error("안전하게 읽을 수 없는 문서 XML입니다.");
  const header = new DOMParser().parseFromString(headerXml, "application/xml");
  if (header.querySelector("parsererror")) throw new Error("문서 서식을 읽을 수 없습니다.");
  const definition = (name: string, id: string | null) => Array.from(header.getElementsByTagNameNS(HH, name)).find(node => node.getAttribute("id") === id);
  const isTitle = (node: Element) => {
    if (definition("style", node.getAttribute("styleIDRef"))?.getAttribute("name") === "HWP Assistant editor title") return true;
    const properties = definition("paraPr", node.getAttribute("paraPrIDRef"));
    const border = properties && Array.from(properties.children).find(child => child.localName === "border");
    const fill = border && definition("borderFill", border.getAttribute("borderFillIDRef"));
    const edge = (name: string) => fill && Array.from(fill.children).find(child => child.localName === name);
    const blues = new Set(["#437fc1", "#107fb5", "#3f7fc1"]);
    return ["topBorder", "bottomBorder"].every(name => edge(name)?.getAttribute("type") === "SOLID" && blues.has(edge(name)?.getAttribute("color")?.toLowerCase() || "")) &&
      ["leftBorder", "rightBorder"].every(name => !edge(name) || edge(name)?.getAttribute("type") === "NONE");
  };
  const preserved = (name: string) => { result.preserved[name] = (result.preserved[name] || 0) + 1; };
  let total = 0;
  for (const [paragraph, node] of children(root.documentElement, "p").entries()) {
    const text = paragraphText(node), controls = paragraphControls(node);
    if (controls === null || controls.length) { preserved("표·개체·주석 포함 문단"); continue; }
    if (!text.trim()) { preserved("빈 문단"); continue; }
    if (isTitle(node) || !/^\s*[□ㅇ○\-․]\s+/.test(text)) { preserved("제목·주석·기타 문단"); continue; }
    const runs = children(node, "run").filter(run => children(run, "t").some(t => t.textContent));
    let fixed_prefix = "", split = runs.length;
    while (split > 0 && runs[split - 1].getAttribute("charPrIDRef") === runs.at(-1)?.getAttribute("charPrIDRef")) split--;
    if (split) fixed_prefix = runs.slice(0, split).flatMap(run => children(run, "t")).map(t => t.textContent || "").join("");
    if (!runs.length || runs.some(run => children(run, "t").some(t => t.children.length))) {
      preserved("혼합 서식 문단"); continue;
    }
    let protected_text: string[] = [];
    if (split && (Array.from(fixed_prefix).length > 120 || !/^\s*[□ㅇ○\-․]\s+\([^()\r\n]{1,40}\)\s*$/.test(fixed_prefix) || !text.slice(fixed_prefix.length).trim())) {
      fixed_prefix = "";
      const grouped: { style: string | null; text: string }[] = [];
      for (const run of runs) {
        const style = run.getAttribute("charPrIDRef"), value = children(run, "t").map(t => t.textContent || "").join("");
        if (grouped.at(-1)?.style === style) grouped.at(-1)!.text += value;
        else grouped.push({ style, text: value });
      }
      protected_text = grouped.filter(run => run.style !== grouped[0].style).map(run => run.text);
      let end = 0;
      const valid = protected_text.length > 0 && protected_text.length <= 24 && protected_text.join("").length <= 1000 && protected_text.every(value => {
        const start = text.indexOf(value), ok = !!value.trim() && start >= end && start === text.lastIndexOf(value);
        end = start + value.length; return ok;
      });
      if (!valid) { preserved("혼합 서식 문단"); continue; }
    }
    if (Array.from(text).length > 4000 || /[\u0000-\u001f\u007f]/.test(text)) { preserved("긴 문단·특수 문자"); continue; }
    total += Array.from(text).length;
    result.paragraphs.push({ section: 0, paragraph, text, text_sha256: await digest(new TextEncoder().encode(text)), length: Array.from(text).length, fixed_prefix,
      ...(protected_text.length ? { protected_text } : {}) });
    const placed = layout?.paragraphs.find(p => p.section === 0 && p.paragraph === paragraph && p.text_sha256 === result.paragraphs.at(-1)!.text_sha256);
    if (placed?.lines.length) result.paragraphs.at(-1)!.rendered_lines = placed.lines.length;
  }
  if (result.paragraphs.length > 120 || total > 24000) {
    result.unavailable = "전체 문장 수정 범위가 120문단 또는 24,000자를 넘었습니다. 일부를 조용히 생략하지 않고 요청을 보류합니다.";
    result.paragraphs = [];
  }
  return result;
}
function paragraphControls(paragraph: Element): Element[] | null {
  const controls: Element[] = [];
  for (const run of children(paragraph, "run")) for (const node of Array.from(run.children)) {
    if (node.namespaceURI !== HP) return null;
    if (node.localName === "t") continue;
    if (node.localName === "secPr") { controls.push(node, ...Array.from(node.getElementsByTagNameNS(HP, "colPr"))); }
    else if (directControls.has(node.localName)) controls.push(node);
    else if (node.localName === "ctrl") {
      for (const control of Array.from(node.children)) {
        if (control.namespaceURI !== HP) return null;
        if (wrappedControls.has(control.localName)) {
          if (control.localName !== "hiddenComment" || control.children.length) controls.push(control);
        } else if (control.localName !== "fieldEnd") return null;
      }
    } else return null;
  }
  return controls;
}
function paragraphText(paragraph: Element): string {
  return children(paragraph, "run").flatMap(run => children(run, "t")).map(node => node.textContent || "").join("");
}
function titleFrame(table: Element, header?: XMLDocument): boolean {
  const rows = children(table, "tr"), cells = rows.length === 1 ? children(rows[0], "tc") : [];
  if (!header || table.getAttribute("rowCnt") !== "1" || table.getAttribute("colCnt") !== "1" || cells.length !== 1) return false;
  const fill = Array.from(header.getElementsByTagNameNS(HH, "borderFill")).find(node => node.getAttribute("id") === cells[0].getAttribute("borderFillIDRef"));
  const edge = (name: string) => fill && Array.from(fill.children).find(node => node.localName === name);
  const blues = new Set(["#437fc1", "#107fb5", "#3f7fc1"]);
  if (["topBorder", "bottomBorder"].every(name => edge(name)?.getAttribute("type") === "SOLID" && blues.has(edge(name)?.getAttribute("color")?.toLowerCase() || ""))
    && ["leftBorder", "rightBorder"].every(name => !edge(name) || edge(name)?.getAttribute("type") === "NONE")) return true;
  // App-applied title frames remain identifiable after the user changes rule color.
  const styles = Array.from(header.getElementsByTagNameNS(HH, "style"));
  return Array.from(cells[0].getElementsByTagNameNS(HP, "p")).some(p => styles.some(style => style.getAttribute("id") === p.getAttribute("styleIDRef")
    && ["HWP Assistant editor title", "HWP Assistant editor cover_title"].includes(style.getAttribute("name") || "")));
}

// Exact read-only selection semantics from the same exported bytes as the
// content snapshot. The all-controls order mirrors editor_design_hwpx.py;
// a table ordinal would point at the wrong object when secPr/shapes precede it.
export async function assistantDesignContext(source: ExportSnapshot, target: StyleTarget | null): Promise<AssistantDesignContext> {
  if (source.contentLoss.count || source.bytes.length > 30 * 1024 * 1024) throw new Error("현재 문서의 디자인 대상을 안전하게 읽을 수 없습니다.");
  const source_sha256 = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(source.bytes)))).map(value => value.toString(16).padStart(2, "0")).join("");
  const empty: AssistantDesignContext = { source_sha256, target: null, target_text: "", target_text_truncated: false, target_is_title_frame: false };
  const integer = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0;
  if (!target || target.section !== 0 || !integer(target.paragraph) || (target.kind === "table" && !integer(target.control))) return empty;
  let size = 0, entries = 0;
  const files = unzipSync(source.bytes, { filter: file => {
    if (++entries > 3000) throw new Error("문서 구성 항목이 너무 많습니다.");
    if (!["Contents/section0.xml", "Contents/header.xml"].includes(file.name)) return false;
    size += file.originalSize;
    if (size > 30 * 1024 * 1024) throw new Error("문서 선택 내용을 읽을 수 있는 크기를 초과했습니다.");
    return true;
  } });
  const parse = (name: string) => {
    if (!files[name]) return undefined;
    const xml = new TextDecoder("utf-8", { fatal: true }).decode(files[name]);
    if (/<!DOCTYPE|<!ENTITY/i.test(xml)) throw new Error("안전하게 읽을 수 없는 문서 XML입니다.");
    const doc = new DOMParser().parseFromString(xml, "application/xml");
    if (doc.getElementsByTagName("parsererror").length) throw new Error("선택 내용 XML을 읽을 수 없습니다.");
    return doc;
  };
  const section = parse("Contents/section0.xml");
  if (!section) return empty;
  const paragraph = children(section.documentElement, "p")[target.paragraph];
  if (!paragraph) return empty;
  const controls = paragraphControls(paragraph);
  if (controls === null) return empty;
  let text = "", isTitle = false;
  if (target.kind === "paragraph") {
    // Section/page metadata often shares the first title's run. The style-only
    // transformer preserves it; these controls do not turn that title into an object.
    const metadata = new Set(["secPr", "colPr", "pageNum", "pageNumCtrl", "newNum", "pageHiding"]);
    if (controls.some(control => !metadata.has(control.localName)) || target.control !== undefined) return empty;
    text = paragraphText(paragraph);
  } else {
    const table = controls[target.control!];
    if (table?.localName !== "tbl" || table.getElementsByTagNameNS(HP, "tbl").length) return empty;
    const paragraphs = Array.from(table.getElementsByTagNameNS(HP, "p"));
    if (paragraphs.some(p => { const nested = paragraphControls(p); return nested === null || nested.length > 0; })) return empty;
    text = children(table, "tr").map(row => children(row, "tc").map(cell => Array.from(cell.getElementsByTagNameNS(HP, "p")).map(paragraphText).join("\n")).join(" | ")).join("\n");
    isTitle = titleFrame(table, parse("Contents/header.xml"));
  }
  const truncated = text.length > 8000;
  return { source_sha256, target: { ...target, section: 0 }, target_text: text.slice(0, 8000).replace(/[\uD800-\uDBFF]$/, ""),
    target_text_truncated: truncated, target_is_title_frame: isTitle };
}

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
