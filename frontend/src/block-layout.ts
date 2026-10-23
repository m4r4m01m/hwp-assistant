export type BodyLayoutEvidence = {
  revision: number;
  epoch: number;
  page_count: number;
  export_sha256: string;
  paragraph_counts: number[];
  paragraphs: Array<{
    section: number;
    paragraph: number;
    length: number;
    text_sha256: string;
    lines: Array<{ start: number; end: number; glyphs: Array<[number, number, number]> }>;
  }>;
};
export type BlockInsertionPlan = { insert_after: number; top_level_count: number };
const changed = "미리보기에서 기존 본문의 줄바꿈 또는 글자 간격이 달라져 적용을 중단했습니다. 기존 편집 내용은 그대로입니다. 원본을 내려받아 확인하거나 새 문서에서 블록을 사용하세요.";
const invalid = "본문 배치 검증 정보를 확인할 수 없어 적용을 중단했습니다. 현재 편집 내용을 저장하거나 복구용으로 내려받은 뒤 페이지를 다시 열어 주세요. 계속되면 관리자에게 문의하세요. 기존 편집 내용은 그대로입니다.";
const integer = (value: number) => Number.isSafeInteger(value) && value >= 0;

function paragraphs(evidence: BodyLayoutEvidence) {
  if (!evidence || !integer(evidence.revision) || !integer(evidence.epoch) ||
      !integer(evidence.page_count) || evidence.page_count < 1 || evidence.page_count > 200 ||
      !/^[a-f0-9]{64}$/.test(evidence.export_sha256) ||
      !Array.isArray(evidence.paragraph_counts) || !evidence.paragraph_counts.length || !evidence.paragraph_counts.every(integer) ||
      !Array.isArray(evidence.paragraphs) || evidence.paragraphs.length > 10_000 ||
      evidence.paragraph_counts.reduce((sum, count) => sum + count, 0) !== evidence.paragraphs.length)
    throw new Error(invalid);
  const result = new Map<string, BodyLayoutEvidence["paragraphs"][number]>();
  let glyphCount = 0;
  for (const paragraph of evidence.paragraphs) {
    const key = `${paragraph.section}:${paragraph.paragraph}`;
    if (!integer(paragraph.section) || !integer(paragraph.paragraph) ||
        paragraph.section >= evidence.paragraph_counts.length ||
        paragraph.paragraph >= evidence.paragraph_counts[paragraph.section] ||
        !integer(paragraph.length) || !/^[a-f0-9]{64}$/.test(paragraph.text_sha256) ||
        !Array.isArray(paragraph.lines) || result.has(key)) throw new Error(invalid);
    for (const line of paragraph.lines) {
      if (!integer(line.start) || !integer(line.end) || line.end < line.start || line.end > paragraph.length ||
          !Array.isArray(line.glyphs)) throw new Error(invalid);
      glyphCount += line.glyphs.length;
      if (glyphCount > 200_000 || line.glyphs.some(glyph => !Array.isArray(glyph) || glyph.length !== 3 ||
          !integer(glyph[0]) || glyph[0] < line.start || glyph[0] >= line.end ||
          !Number.isFinite(glyph[1]) || !Number.isFinite(glyph[2]))) throw new Error(invalid);
    }
    result.set(key, paragraph);
  }
  return result;
}

// Exported XML semantics do not describe what the native renderer actually drew.
// Match every original body paragraph through the insertion; allow pagination and
// vertical movement, but preserve line breaks and glyph spacing relative to each
// line's first glyph. No bytes or editor state are modified by this comparison.
export function assertBodyLayoutPreserved(
  before: BodyLayoutEvidence,
  after: BodyLayoutEvidence,
  plan?: BlockInsertionPlan,
) {
  const original = paragraphs(before), staged = paragraphs(after);
  if (plan && (!integer(plan.insert_after) || !integer(plan.top_level_count) || plan.top_level_count < 1 ||
      plan.insert_after >= before.paragraph_counts[0])) throw new Error(invalid);
  const inserted = plan?.top_level_count || 0;
  if (before.paragraph_counts.length !== after.paragraph_counts.length ||
      before.paragraph_counts.some((count, section) => after.paragraph_counts[section] !== count + (section === 0 ? inserted : 0)))
    throw new Error(changed);
  for (const paragraph of original.values()) {
    const offset = plan && paragraph.section === 0 && paragraph.paragraph > plan.insert_after ? inserted : 0;
    const next = staged.get(`${paragraph.section}:${paragraph.paragraph + offset}`);
    if (!next || next.length !== paragraph.length || next.text_sha256 !== paragraph.text_sha256 ||
        next.lines.length !== paragraph.lines.length) throw new Error(changed);
    for (let index = 0; index < paragraph.lines.length; index++) {
      const line = paragraph.lines[index], nextLine = next.lines[index];
      if (line.start !== nextLine.start || line.end !== nextLine.end || line.glyphs.length !== nextLine.glyphs.length)
        throw new Error(changed);
      for (let glyph = 0; glyph < line.glyphs.length; glyph++) {
        const previous = line.glyphs[glyph], current = nextLine.glyphs[glyph];
        if (previous[0] !== current[0] || Math.abs(previous[1] - current[1]) > 0.75 || Math.abs(previous[2] - current[2]) > 0.75)
          throw new Error(changed);
      }
    }
  }
}
