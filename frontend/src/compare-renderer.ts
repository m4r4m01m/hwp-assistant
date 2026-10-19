// This iframe has no editor, autosave, file picker, mutation bridge or API writes.
// Only the verified Studio WASM and its exact JS binding render immutable bytes.
import type { HwpDocument } from "@rhwp/core";
import type {
  CompareBlock,
  ComparisonRenderer,
  PageLocation,
  Rect,
} from "./comparison-types";
import "./compare-renderer.css";

const target = document.getElementById("page")!;
let native: typeof import("@rhwp/core");
let doc: HwpDocument | null = null;
let generation = 0;
const sha = async (bytes: Uint8Array) =>
  Array.from(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", new Uint8Array(bytes)),
    ),
    (b) => b.toString(16).padStart(2, "0"),
  ).join("");
const ready = (async () => {
  const response = await fetch("/comparison-engine.json");
  if (!response.ok)
    throw Error("검증된 비교 엔진이 없습니다. 프런트엔드를 다시 빌드하세요.");
  const manifest = await response.json();
  if (
    !/^\/rhwp\/assets\/rhwp_bg-[\w-]+\.wasm$/.test(manifest.wasm) ||
    !/^[a-f0-9]{64}$/.test(manifest.wasm_sha256)
  )
    throw Error("비교 엔진의 검증 정보가 올바르지 않습니다.");
  const moduleUrl = "/comparison-core.js";
  native = await import(/* @vite-ignore */ moduleUrl);
  const wasm = new Uint8Array(await (await fetch(manifest.wasm)).arrayBuffer());
  if ((await sha(wasm)) !== manifest.wasm_sha256)
    throw Error("문서 엔진 파일이 검증된 버전과 다릅니다.");
  await native.default({ module_or_path: wasm });
  // Same self-hosted fonts as Studio. SVG contains the engine's family fallback
  // chains and absolute glyph positions; no HTML reconstruction or remote fonts.
  const families = [
    ["Nanum Myeongjo", "NanumMyeongjo"],
    ["Nanum Gothic", "NanumGothic"],
    ["Noto Serif KR", "NotoSerifKR"],
    ["Noto Sans KR", "NotoSansKR"],
    ["D2Coding", "D2Coding"],
    ["Pretendard", "Pretendard"],
    ["Gowun Batang", "GowunBatang"],
  ];
  await Promise.all(
    families.flatMap(([family, file]) =>
      ["Regular", "Bold"].map(async (weight) => {
        const face = new FontFace(
          family,
          `url(/rhwp/fonts/${file}-${weight}.woff2)`,
          { weight: weight === "Bold" ? "700" : "400" },
        );
        document.fonts.add(await face.load());
      }),
    ),
  );
  await document.fonts.ready;
})();
// Handle startup failures even before the parent begins awaiting ready.
void ready.catch(() => {});

const allowed = new Set([
  "svg",
  "g",
  "defs",
  "clipPath",
  "rect",
  "path",
  "circle",
  "ellipse",
  "line",
  "polyline",
  "polygon",
  "text",
  "tspan",
  "image",
  "linearGradient",
  "radialGradient",
  "stop",
  "pattern",
  "mask",
  "use",
  "title",
  "desc",
]);
function safeSvg(source: string) {
  if (source.length > 12_000_000)
    throw Error("이 페이지의 렌더링 크기 한도를 초과했습니다.");
  const parsed = new DOMParser().parseFromString(source, "image/svg+xml");
  if (
    parsed.querySelector("parsererror") ||
    parsed.documentElement.localName !== "svg"
  )
    throw Error("페이지를 렌더링하지 못했습니다.");
  for (const node of Array.from(parsed.querySelectorAll("*"))) {
    if (!allowed.has(node.localName)) {
      node.remove();
      continue;
    }
    for (const attr of Array.from(node.attributes)) {
      const value = attr.value.trim();
      if (
        attr.name.toLowerCase().startsWith("on") ||
        attr.localName === "style" ||
        (/href$/i.test(attr.localName) &&
          !(
            /^#[\w:.-]+$/.test(value) ||
            /^data:image\/(png|jpeg|gif|webp);base64,[a-z0-9+/=\s]+$/i.test(
              value,
            )
          )) ||
        (/url\s*\(/i.test(value) && !/^url\(#[\w:.-]+\)$/.test(value))
      )
        node.removeAttributeNode(attr);
    }
  }
  return document.importNode(
    parsed.documentElement,
    true,
  ) as unknown as SVGSVGElement;
}
const validRect = (r: Rect) =>
  [r.pageIndex, r.x, r.y, r.width, r.height].every(Number.isFinite) &&
  Number.isInteger(r.pageIndex) &&
  r.pageIndex >= 0 &&
  r.pageIndex < (doc?.pageCount() || 0) &&
  r.height > 0;
function nativeTableIndex(section: number, paragraph: number, ordinal: number) {
  // Section/column definitions occupy native control slots but are not tables.
  // Resolve the table ordinal explicitly; never pass XML sibling indices through.
  const controls = JSON.parse(doc!.getControlTextPositions(section, paragraph));
  if (!Array.isArray(controls) || controls.length > 512)
    throw Error("표 위치 조회 범위 초과");
  let found = 0;
  for (let ci = 0; ci < controls.length; ci++) {
    try {
      const dim = JSON.parse(doc!.getTableDimensions(section, paragraph, ci));
      if (dim.rowCount > 0 && dim.colCount > 0 && found++ === ordinal)
        return ci;
    } catch {
      /* This native control is not a table. */
    }
  }
  throw Error("해당 표를 찾지 못했습니다.");
}

const api: ComparisonRenderer = {
  ready,
  async open(bytes, expected) {
    const ticket = ++generation;
    await ready;
    if (bytes.length > 30 * 1024 * 1024 || (await sha(bytes)) !== expected)
      throw Error("비교할 저장본의 해시가 일치하지 않습니다.");
    if (ticket !== generation) throw Error("비교 요청이 바뀌었습니다.");
    doc?.free();
    doc = null;
    target.replaceChildren();
    const loaded = new native.HwpDocument(new Uint8Array(bytes));
    if (loaded.pageCount() < 1 || loaded.pageCount() > 500) {
      loaded.free();
      throw Error("실제 문서 비교는 500쪽까지 지원합니다.");
    }
    doc = loaded;
    return { pages: doc.pageCount() };
  },
  locate(block) {
    if (!doc) throw Error("저장본이 열리지 않았습니다.");
    const { section, paragraph, cell_path: path } = block.anchor;
    const absent: PageLocation = {
      page: null,
      rects: [],
      note: "변경 위치를 정확히 확인하지 못했습니다. 변경 목록과 페이지를 함께 확인하세요.",
    };
    try {
      if (block.kind === "section")
        return {
          page: null,
          rects: [],
          note: "구역 설정 변경입니다. 용지와 여백을 확인하세요.",
        };
      if (block.kind === "table" && !path.length) {
        const ci = nativeTableIndex(
          section,
          paragraph,
          block.anchor.table ?? block.anchor.control!,
        );
        const dimensions = JSON.parse(
          doc.getTableDimensions(section, paragraph, ci),
        );
        if (
          block.text !==
          `표 ${dimensions.rowCount}행 × ${dimensions.colCount}열`
        )
          return absent;
        const box = JSON.parse(doc.getTableBBox(section, paragraph, ci));
        if (validRect(box))
          return {
            page: box.pageIndex,
            rects: [box],
            note: "표 전체 변경 위치",
          };
        return absent;
      }
      if (!["paragraph", "cell"].includes(block.kind))
        return {
          ...absent,
          note: "개체 단위 변경입니다. 목록의 위치와 실제 문서를 확인하세요.",
        };
      let text: string, rects: Rect[], cursor: Rect;
      if (!path.length) {
        const length = doc.getParagraphLength(section, paragraph);
        text = doc.getTextRange(section, paragraph, 0, length);
        rects = JSON.parse(
          doc.getSelectionRects(section, paragraph, 0, paragraph, length),
        );
        cursor = JSON.parse(doc.getCursorRect(section, paragraph, 0));
      } else if (path.length === 1) {
        const hop = path[0];
        const ci = nativeTableIndex(
          section,
          paragraph,
          hop.table ?? hop.control,
        );
        const length = doc.getCellParagraphLength(
          section,
          paragraph,
          ci,
          hop.cell,
          hop.paragraph,
        );
        text = doc.getTextInCell(
          section,
          paragraph,
          ci,
          hop.cell,
          hop.paragraph,
          0,
          length,
        );
        rects = JSON.parse(
          doc.getSelectionRectsInCell(
            section,
            paragraph,
            ci,
            hop.cell,
            hop.paragraph,
            0,
            hop.paragraph,
            length,
          ),
        );
        cursor = JSON.parse(
          doc.getCursorRectInCell(
            section,
            paragraph,
            ci,
            hop.cell,
            hop.paragraph,
            0,
          ),
        );
      } else
        return {
          ...absent,
          note: "중첩 표의 변경은 셀 주소와 내용을 확인하세요. 위치를 추정해 표시하지 않습니다.",
        };
      // A successful native lookup is insufficient: check the exact source text.
      if (text !== block.text) return absent;
      rects = rects.filter(validRect);
      if (!rects.length && cursor && Number.isInteger(cursor.pageIndex)) {
        const marker = { ...cursor, width: 14, height: cursor.height || 16 };
        if (validRect(marker)) rects = [marker];
      }
      return rects.length
        ? {
            page: rects[0].pageIndex,
            rects,
            note: "실제 렌더링에서 확인한 변경 문단",
          }
        : absent;
    } catch {
      return absent;
    }
  },
  render(page, location, kind = "modified", zoom = 1) {
    if (!doc || !Number.isInteger(page) || page < 0 || page >= doc.pageCount())
      throw Error("유효하지 않은 문서 쪽수입니다.");
    const svg = safeSvg(doc.renderPageSvg(page));
    if (zoom && [0.75, 1, 1.25, 1.5].includes(zoom)) {
      const width = Number(svg.getAttribute("width"));
      if (Number.isFinite(width) && width > 0 && width < 10000)
        svg.style.width = `${width * zoom}px`;
    }
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", `${page + 1}쪽 저장된 문서`);
    for (const r of location?.rects || []) {
      if (r.pageIndex !== page || !validRect(r)) continue;
      const marker = document.createElementNS(
        "http://www.w3.org/2000/svg",
        "rect",
      );
      for (const [key, value] of Object.entries({
        x: r.x,
        y: r.y,
        width: Math.max(8, r.width),
        height: r.height,
      }))
        marker.setAttribute(key, String(value));
      marker.setAttribute(
        "class",
        `change-mark ${kind === "removed" ? "removed" : kind === "added" ? "added" : "modified"}`,
      );
      svg.appendChild(marker);
    }
    target.replaceChildren(svg);
    const first = target.querySelector(".change-mark");
    if (first) {
      // scrollIntoView also scrolls the embedding modal, clipping its header and
      // sidebar at larger zooms. Center only this read-only frame's viewport.
      const rect = first.getBoundingClientRect();
      window.scrollTo({
        left: window.scrollX + rect.x + rect.width / 2 - window.innerWidth / 2,
        top: window.scrollY + rect.y + rect.height / 2 - window.innerHeight / 2,
        behavior: "instant",
      });
    } else window.scrollTo(0, 0);
  },
  dispose() {
    generation++;
    doc?.free();
    doc = null;
    target.replaceChildren();
  },
};
(
  window as Window & { hwpComparisonRenderer?: ComparisonRenderer }
).hwpComparisonRenderer = api;
window.addEventListener("pagehide", () => api.dispose(), { once: true });
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && window.parent !== window)
    window.parent.postMessage(
      { type: "hwp-comparison-escape" },
      location.origin,
    );
});
