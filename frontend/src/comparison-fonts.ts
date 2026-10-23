import { publicPath } from "./public-path";

const registered = new WeakMap<FontFaceSet, FontFace[]>();
const families = [
  ["Nanum Myeongjo", "NanumMyeongjo"],
  ["Nanum Gothic", "NanumGothic"],
  ["Noto Serif KR", "NotoSerifKR"],
  ["Noto Sans KR", "NotoSansKR"],
  ["D2Coding", "D2Coding"],
  ["Pretendard", "Pretendard"],
  ["Gowun Batang", "GowunBatang"],
] as const;

// Register the same self-hosted faces as Studio, without starting 14 downloads.
// Native SVG text and the browser's font matching select the required faces.
export function registerComparisonFonts(fonts: FontFaceSet = document.fonts) {
  const previous = registered.get(fonts);
  if (previous) return previous;
  const faces = families.flatMap(([family, file]) =>
    ["Regular", "Bold"].map((weight) => {
      const face = new FontFace(
        family,
        `url(${publicPath(`rhwp/fonts/${file}-${weight}.woff2`)})`,
        { weight: weight === "Bold" ? "700" : "400" },
      );
      fonts.add(face);
      return face;
    }),
  );
  registered.set(fonts, faces);
  return faces;
}

export async function waitForComparisonFonts(
  target: Element,
  fonts: FontFaceSet = document.fonts,
  timeoutMs = 20_000,
) {
  // visibility:hidden preserves layout/font matching; display:none would not.
  target.getBoundingClientRect();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      fonts.ready,
      new Promise<never>((_, reject) => {
        timer = setTimeout(
          () => reject(Error("문서 글꼴 준비 시간이 초과되었습니다. 비교를 닫고 다시 시도하세요.")),
          timeoutMs,
        );
      }),
    ]);
    if ((registered.get(fonts) || []).some((face) => face.status === "error"))
      throw Error("문서 글꼴을 불러오지 못했습니다. 연결을 확인하고 비교를 다시 열어 주세요.");
  } finally {
    clearTimeout(timer);
  }
}
