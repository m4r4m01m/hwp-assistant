export type SampleRegion = { role: string; texts: string[]; label?: string; page?: number; occurrence?: number };
export type RegionRect = { role: string; x: number; y: number; width: number; height: number };
export type SamplePageGeometry = { width: number; height: number; rects: RegionRect[] };
export function pageSettingRegions(key: string, page: SamplePageGeometry, settings: Record<string, number | string | boolean>): RegionRect[] {
  if (key === "page_width_mm" || key === "page_height_mm") return [{ role: "page", x: 0, y: 0, width: page.width, height: page.height }];
  if (!key.startsWith("margin_")) return [];
  const widthMm = Number(settings.page_width_mm), heightMm = Number(settings.page_height_mm);
  if (!(widthMm > 0 && heightMm > 0)) return [];
  const xScale = page.width / widthMm, yScale = page.height / heightMm;
  const value = (side: string) => Number(settings[`margin_${side}_mm`]);
  const left = value("left") * xScale, right = value("right") * xScale;
  const top = value("top") * yScale, bottom = value("bottom") * yScale;
  const header = value("header") * yScale, footer = value("footer") * yScale;
  const rectangles: Record<string, number[]> = {
    margin_left_mm: [0, 0, left, page.height], margin_right_mm: [page.width - right, 0, right, page.height],
    margin_top_mm: [0, 0, page.width, top], margin_bottom_mm: [0, page.height - bottom, page.width, bottom],
    margin_header_mm: [left, top, page.width - left - right, header],
    margin_footer_mm: [left, page.height - bottom - footer, page.width - left - right, footer],
  };
  const box = rectangles[key];
  if (!box || !box.every(Number.isFinite) || box[2] <= 0 || box[3] <= 0) return [];
  return [{ role: key.replace(/_mm$/, ""), x: box[0], y: box[1], width: box[2], height: box[3] }];
}
type Glyph = { text: string; start: number; end: number; rect: Omit<RegionRect, "role"> };
const normalized = (value: string) => value.replace(/\s+/gu, "");
async function measuredResource<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) throw new DOMException("Aborted", "AbortError");
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancel = () => {};
  try {
    return await Promise.race([promise, new Promise<never>((_, reject) => {
      cancel = () => reject(new DOMException("Aborted", "AbortError"));
      signal.addEventListener("abort", cancel, { once: true });
      timer = setTimeout(() => reject(new Error("예시 위치 확인 시간이 초과되었습니다.")), 8000);
    })]);
  } finally { clearTimeout(timer); signal.removeEventListener("abort", cancel); }
}

function bounds(element: SVGGraphicsElement): Omit<RegionRect, "role"> | null {
  const box = element.getBBox(), matrix = element.getCTM();
  if (!matrix || !box.width || !box.height) return null;
  const points = [[box.x, box.y], [box.x + box.width, box.y], [box.x, box.y + box.height], [box.x + box.width, box.y + box.height]]
    .map(([x, y]) => new DOMPoint(x, y).matrixTransform(matrix));
  const x = Math.min(...points.map(point => point.x)), y = Math.min(...points.map(point => point.y));
  return { x, y, width: Math.max(...points.map(point => point.x)) - x, height: Math.max(...points.map(point => point.y)) - y };
}

// The same sanitized SVG shown in <img> is measured in a script-disabled frame.
// Its stylesheet cannot affect the application; no guessed font/line rectangles
// or HWPX mutations are used for the explanatory overlay.
export async function measureSampleRegions(svgs: string[], anchors: SampleRegion[], signal: AbortSignal): Promise<SamplePageGeometry[]> {
  const frame = document.createElement("iframe");
  frame.setAttribute("sandbox", "allow-same-origin");
  frame.setAttribute("aria-hidden", "true");
  frame.inert = true;
  frame.style.cssText = "position:fixed;left:-20000px;top:0;width:1200px;height:1600px;visibility:hidden;pointer-events:none;border:0";
  const loaded = new Promise<void>((resolve, reject) => {
    frame.onload = () => resolve();
    frame.onerror = () => reject(new Error("예시의 표시 위치를 확인하지 못했습니다."));
  });
  frame.srcdoc = '<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;; img-src data:; font-src data:"><style>html,body{margin:0;padding:0}</style><body></body>';
  document.body.appendChild(frame);
  try {
    await measuredResource(loaded, signal);
    const pages: { geometry: SamplePageGeometry; glyphs: Glyph[]; text: string }[] = [];
    for (const value of svgs) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      const doc = frame.contentDocument!;
      // The caller supplies embedStandardPreviewFonts output: sanitized engine
      // SVG plus allowlisted data fonts (larger than the raw SVG size limit).
      const parsed = new DOMParser().parseFromString(value, "image/svg+xml");
      const svg = doc.importNode(parsed.documentElement, true) as unknown as SVGSVGElement;
      doc.body.replaceChildren(svg);
      svg.getBoundingClientRect();
      await measuredResource(doc.fonts.ready, signal);
      const view = svg.viewBox.baseVal;
      const width = view.width || svg.width.baseVal.value, height = view.height || svg.height.baseVal.value;
      if (![width, height].every(number => Number.isFinite(number) && number > 0 && number < 20000)) throw new Error("예시 용지 크기를 확인하지 못했습니다.");
      const glyphs: Glyph[] = [];
      let text = "";
      for (const node of Array.from(svg.querySelectorAll("text"))) {
        if (node.closest("defs,clipPath,mask")) continue;
        const part = normalized(node.textContent || ""), rect = bounds(node);
        if (!part || !rect || rect.x + rect.width < 0 || rect.y + rect.height < 0 || rect.x > width || rect.y > height) continue;
        glyphs.push({ text: part, start: text.length, end: text.length + part.length, rect });
        text += part;
      }
      pages.push({ geometry: { width, height, rects: [] }, glyphs, text });
    }
    for (const anchor of anchors) {
      if (!anchor || typeof anchor.role !== "string" || !Array.isArray(anchor.texts)) continue;
      for (const raw of anchor.texts.slice(0, 80)) {
        if (typeof raw !== "string") continue;
        const query = normalized(raw);
        if (!query || query.length > 4000) continue;
        const matches: { page: number; start: number; end: number }[] = [];
        pages.forEach((page, index) => {
          if (anchor.page !== undefined && anchor.page !== index + 1) return;
          let position = 0;
          while ((position = page.text.indexOf(query, position)) !== -1) {
            matches.push({ page: index, start: position, end: position + query.length });
            position += query.length;
          }
        });
        // Ambiguous text cannot distinguish (for example) a TOC heading from
        // the matching body heading. Show no location until the contract can.
        const match = anchor.occurrence === undefined ? matches.length === 1 ? matches[0] : undefined : matches[anchor.occurrence];
        if (!match) continue;
        const page = pages[match.page];
        const selected = page.glyphs.filter(glyph => glyph.end > match.start && glyph.start < match.end);
        const lines: Omit<RegionRect, "role">[] = [];
        for (const glyph of selected) {
          const rect = glyph.rect;
          const line = lines.find(line => Math.abs(line.y - rect.y) < Math.min(line.height, rect.height) * .45);
          if (!line) lines.push({ ...rect });
          else {
            const right = Math.max(line.x + line.width, rect.x + rect.width), bottom = Math.max(line.y + line.height, rect.y + rect.height);
            line.x = Math.min(line.x, rect.x); line.y = Math.min(line.y, rect.y);
            line.width = right - line.x; line.height = bottom - line.y;
          }
        }
        page.geometry.rects.push(...lines.map(rect => ({ ...rect, role: anchor.role })));
      }
    }
    return pages.map(page => page.geometry);
  } finally { frame.remove(); }
}

export function settingImpact(key: string, preset: string) {
  if (key.startsWith("body_parenthetical_scope.")) {
    const role = key.slice("body_parenthetical_scope.".length);
    const names: Record<string, string> = { square: "주요 항목 □", circle: "하위 설명 ㅇ", dash: "세부 설명 -", dot: "보충 항목 ․", body: "일반 문단", summary: "핵심 요약" };
    return { roles: names[role] ? [`parenthetical_${role}`] : [], title: `${names[role] || "항목"} · 괄호 요약어`, description: "이 역할의 문장 앞에 붙은 (현황), (조치) 같은 요약어를 표시하거나 숨깁니다. 번호형에서도 문단 역할을 기준으로 적용합니다." };
  }
  if (key.endsWith("_rule")) return { roles: [], title: "새 초안 작성 기준", description: "새 AI 초안을 작성할 때 적용합니다. 고정 예시의 문구는 이 설정으로 다시 쓰지 않습니다." };
  if (key.startsWith("cover_")) return { roles: [key.startsWith("cover_date_") ? "cover_date" : key.startsWith("cover_department_") ? "cover_department" : "cover_title"], title: "표지", description: preset === "appendix-1" ? "별첨 1에는 표지가 없습니다. 표지 서식은 별첨 2 또는 3에서 확인하세요." : "문서 맨 앞 표지에 적용합니다. 본문 안의 제목·내용과 별도 서식입니다." };
  if (key.startsWith("toc_")) return { roles: key.startsWith("toc_title_") ? ["toc_title"] : key.startsWith("toc_major_") ? ["toc_major"] : key.startsWith("toc_minor_") ? ["toc_minor"] : ["toc_title", "toc_major", "toc_minor"], title: "목차", description: preset === "appendix-1" ? "별첨 1에는 목차가 없습니다. 목차 서식은 별첨 2 또는 3에서 확인하세요." : "본문 제목을 참조하는 목차에 적용합니다. 본문 절 제목의 글꼴은 따로 조절합니다." };
  if (key === "show_core_summary") return { roles: ["summary"], title: "핵심 요약", description: "예시의 핵심 요약을 넣거나 뺍니다. 나머지 본문 내용은 유지합니다." };
  if (key === "body_parenthetical_labels") return { roles: ["parenthetical_square", "parenthetical_circle", "parenthetical_dash", "parenthetical_dot", "parenthetical_body", "parenthetical_summary"], title: "괄호 요약어", description: "문장 앞의 (현황), (조치) 같은 짧은 요약어입니다. 아래에서 표시할 항목을 고르세요. 제목·표·주석과 문장 안의 괄호는 바꾸지 않습니다." };
  if (key.startsWith("page_") || key.startsWith("margin_")) return { roles: [key.startsWith("page_number_") ? "page_number" : "page"], title: key.startsWith("page_number_") ? "쪽번호" : "용지·여백", description: key.startsWith("page_number_") ? "각 쪽에 표시하는 번호의 모양과 위치입니다." : key === "margin_header_mm" ? "위쪽 여백 다음에 놓이는 머리말 공간입니다. 현재 예시의 용지와 저장된 수치로 영역을 표시합니다." : key === "margin_footer_mm" ? "아래쪽 여백 앞에 놓이는 꼬리말 공간입니다. 현재 예시의 용지와 저장된 수치로 영역을 표시합니다." : key.startsWith("margin_") ? "용지의 해당 방향 여백입니다. 현재 예시의 용지 크기와 저장된 수치로 영역을 표시합니다." : "문서 전체 용지 크기입니다. 글자의 크기를 줄이는 설정이 아닙니다." };
  if (key.startsWith("title_")) return { roles: ["title"], title: "본문 문서 제목", description: "본문을 시작하는 문서 이름과 제목 상자입니다. ‘수집 현황’ 같은 절 제목은 대제목 설정에서 조절합니다." };
  if (key.startsWith("table_source_")) return { roles: ["table_source"], title: "표·그림 아래 주석", description: "표나 그림 바로 아래의 출처·설명에 적용합니다. 표 안의 글자는 표 내용에서 조절합니다." };
  if (key.startsWith("table_")) return { roles: ["table"], title: "표", description: "예시의 실제 표 영역에 적용합니다. 표 제목·표 아래 주석은 각각의 설정에서 조절합니다." };
  if (key.startsWith("citation_")) return { roles: ["note", "table_source", "source"], title: "주석 표시", description: "해당 본문이나 표에 연결된 주석의 표식에 적용합니다. 본문 번호 체계와는 별개입니다." };
  if (key === "numbering_style") return { roles: ["heading", "subheading", "minor_heading", "square", "circle", "dash", "dot", "numbered_4", "numbered_5", "numbered_6"], title: "항목 번호·기호", description: "같은 예시의 항목 계층을 번호 또는 기호로 표시합니다. 내용은 유지하고 항목 표시를 바꿉니다." };
  const numbered = /^numbered_([1-6])_/.exec(key), bullet = /^bullet_(square|circle|dash|dot)_/.exec(key);
  if (numbered || bullet) return { roles: [numbered ? ({ "1": "heading", "2": "subheading", "3": "minor_heading" } as Record<string, string>)[numbered[1]] || `numbered_${numbered[1]}` : bullet![1]], title: "항목 단계 서식", description: "해당 단계의 항목에만 적용합니다. 현재 번호·기호 체계에서 사용하지 않는 단계는 예시에 나타나지 않습니다." };
  const roles = ["minor_heading", "subheading", "heading", "attachment", "caption", "square", "circle", "source", "body", "note", "unit", "dash", "dot"];
  const role = roles.find(role => key.startsWith(role + "_"));
  const labels: Record<string, [string, string]> = {
    heading: ["대제목 · 첫째 항목", "‘정비 필요성’ 같은 본문의 절 제목입니다. 문서 전체 제목과 별개입니다."],
    subheading: ["중제목 · 둘째 항목", "대제목 아래의 하위 절을 구분합니다."], minor_heading: ["소제목 · 셋째 항목", "중제목 아래의 더 작은 절을 구분합니다."],
    body: ["일반 본문", "기호가 없는 일반 문단에 적용합니다. □·ㅇ·- 항목은 각 기호 항목 설정에서 조절합니다."],
    square: ["□ 기호 항목", "본문의 핵심 논점 항목입니다. 하위 설명 ㅇ·-와 서식을 따로 정합니다."],
    circle: ["ㅇ 기호 항목", "핵심 논점을 뒷받침하는 한 단계 아래 설명입니다."], dash: ["- 기호 항목", "구체적인 조건·조치 등 더 아래 단계의 설명입니다."],
    source: ["참고·출처", "예시에서 인용한 자료 정보를 표시하는 문단입니다."], note: ["본문 주석", "해당 본문 바로 아래에 붙는 설명·주석입니다."],
    caption: ["표·그림 제목", "표나 그림을 식별하는 제목입니다. 표 안의 글자와는 별도입니다."], unit: ["표·그림 단위", "표나 그림의 단위를 나타내는 문구입니다."],
    attachment: ["붙임·별첨 제목", "본문 뒤 붙임·별첨의 이름을 표시하는 제목입니다."], dot: ["․ 기호 항목", "더 세부적인 본문 항목에 적용합니다."],
  };
  return role ? { roles: [role], title: labels[role]?.[0] || role, description: labels[role]?.[1] || "해당 문단 종류에 적용합니다." } : { roles: [], title: "서식 설정", description: "이 설정의 적용 대상은 항목 아래 설명을 확인하세요. 위치를 확인할 수 없는 영역은 임의로 표시하지 않습니다." };
}
