const safeResource = (value: string) =>
  /^(#|data:(image\/(png|jpeg|gif|webp)|font\/(woff|woff2)|application\/(font-woff|x-font-woff));base64,)/i.test(
    value,
  );
const remoteCss = (value: string) =>
  /@import/i.test(value) ||
  Array.from(value.matchAll(/url\s*\(([^)]*)\)/gi)).some(
    (match) => !safeResource(match[1].trim().replace(/^['"]|['"]$/g, "")),
  );

// Render engine output only in an image context, without active content or
// external resources. Used by both pinned templates and the standard designer.
export function previewSvg(value: string) {
  if (value.length > 8_000_000 || /<!DOCTYPE|<!ENTITY/i.test(value))
    throw new Error("안전하게 표시할 수 없는 미리보기입니다.");
  const doc = new DOMParser().parseFromString(value, "image/svg+xml");
  if (
    doc.querySelector("parsererror") ||
    doc.documentElement.localName !== "svg"
  )
    throw new Error("미리보기 SVG를 읽을 수 없습니다.");
  for (const node of Array.from(doc.querySelectorAll("*"))) {
    if (
      [
        "script", "foreignObject", "iframe", "object", "embed", "animate",
        "set", "animateMotion", "animateTransform",
      ].includes(node.localName)
    )
      throw new Error("활성 요소가 있는 미리보기는 표시할 수 없습니다.");
    for (const attr of Array.from(node.attributes)) {
      if (/^on/i.test(attr.name))
        throw new Error("활성 요소가 있는 미리보기는 표시할 수 없습니다.");
      if (attr.localName === "href" && !safeResource(attr.value))
        throw new Error("외부 이미지를 참조하는 미리보기는 표시할 수 없습니다.");
      if (remoteCss(attr.value))
        throw new Error("외부 리소스를 참조하는 미리보기는 표시할 수 없습니다.");
    }
    if (node.localName === "style" && remoteCss(node.textContent || ""))
      throw new Error("외부 서식을 참조하는 미리보기는 표시할 수 없습니다.");
  }
  return new XMLSerializer().serializeToString(doc);
}
