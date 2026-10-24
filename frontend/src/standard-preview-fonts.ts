import { publicPath } from "./public-path";
import { previewSvg } from "./preview-svg";

// These faces ship with the pinned Studio bundle (also used by comparison-fonts).
// System fonts in a saved standard remain selectable; this is not an inventory
// of fonts installed on the user's computer.
const families = [
  ["Nanum Myeongjo", "NanumMyeongjo"],
  ["Nanum Gothic", "NanumGothic"],
  ["Noto Serif KR", "NotoSerifKR"],
  ["Noto Sans KR", "NotoSansKR"],
  ["D2Coding", "D2Coding"],
  ["Pretendard", "Pretendard"],
  ["Gowun Batang", "GowunBatang"],
] as const;

export const standardPreviewFontNames: readonly string[] = families.map(([name]) => name);
const faces = new Map<string, Promise<string>>();
const familyFiles = new Map<string, string>(families);

function fontData(file: string): Promise<string> {
  const previous = faces.get(file);
  if (previous) return previous;
  const pending = (async () => {
    const response = await fetch(publicPath(`rhwp/fonts/${file}.woff2`), {
      credentials: "same-origin", redirect: "error", signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) throw Error("샘플에 사용할 글꼴을 불러오지 못했습니다. 미리보기를 다시 확인하세요.");
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.length > 2_000_000 || bytes.length < 4 ||
        String.fromCharCode(...bytes.subarray(0, 4)) !== "wOF2")
      throw Error("샘플 글꼴 파일을 확인할 수 없습니다.");
    const parts: string[] = [];
    for (let offset = 0; offset < bytes.length; offset += 8192)
      parts.push(String.fromCharCode(...bytes.subarray(offset, offset + 8192)));
    return `data:font/woff2;base64,${btoa(parts.join(""))}`;
  })();
  faces.set(file, pending);
  void pending.catch(() => { if (faces.get(file) === pending) faces.delete(file); });
  return pending;
}

/** An SVG image cannot inherit Studio's web fonts. Embed only its used bundled
 * fallback faces, keeping every native glyph position and the HWPX unchanged.
 * Sanitize the engine SVG first. All added CSS/data URLs come from this fixed
 * local allowlist, never document-provided URLs or user-provided font names.
 */
export async function embedStandardPreviewFonts(value: string): Promise<string> {
  const safe = previewSvg(value);
  const doc = new DOMParser().parseFromString(safe, "image/svg+xml");
  const used = new Map<string, { family: string; file: string; weight: number }>();
  for (const text of Array.from(doc.querySelectorAll("text"))) {
    const chain = (text.getAttribute("font-family") || "").split(",")
      .map(name => name.trim().replace(/^['"]|['"]$/g, ""));
    const family = chain.find(name => familyFiles.has(name));
    if (!family) continue;
    const weight = text.getAttribute("font-weight") || "400";
    const bold = weight === "bold" || Number(weight) >= 600;
    const file = `${familyFiles.get(family)}-${bold ? "Bold" : "Regular"}`;
    used.set(file, { family, file, weight: bold ? 700 : 400 });
  }
  if (!used.size) return safe;
  const rules = await Promise.all(Array.from(used.values(), async face =>
    `@font-face{font-family:'${face.family}';font-style:normal;font-weight:${face.weight};src:url('${await fontData(face.file)}') format('woff2')}`));
  const style = doc.createElementNS("http://www.w3.org/2000/svg", "style");
  style.textContent = rules.join("\n");
  doc.documentElement.insertBefore(style, doc.documentElement.firstChild);
  const result = new XMLSerializer().serializeToString(doc);
  if (result.length > 24_000_000) throw Error("샘플의 글꼴 표시 용량을 초과했습니다.");
  return result;
}
