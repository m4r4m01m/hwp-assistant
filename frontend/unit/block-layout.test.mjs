import test from "node:test";
import assert from "node:assert/strict";
import { loadSource } from "./load-source.mjs";

const { assertBodyLayoutPreserved } = loadSource("block-layout.ts");
const copy = value => structuredClone(value);
const hash = letter => letter.repeat(64);
function evidence() {
  return { revision: 4, epoch: 1, page_count: 2, export_sha256: hash("a"), paragraph_counts: [2, 1], paragraphs: [
    { section: 0, paragraph: 0, length: 3, text_sha256: hash("b"), lines: [{ start: 0, end: 3, glyphs: [[0, 0, 8], [1, 8, 8], [2, 16, 8]] }] },
    { section: 0, paragraph: 1, length: 0, text_sha256: hash("c"), lines: [] },
    { section: 1, paragraph: 0, length: 1, text_sha256: hash("d"), lines: [{ start: 0, end: 1, glyphs: [[0, 0, 9]] }] },
  ] };
}

test("body layout permits insertion and pagination while mapping blanks and later sections", () => {
  const before = evidence(), after = copy(before);
  after.page_count = 3; after.epoch = 2; after.revision = 0; after.export_sha256 = hash("e");
  after.paragraph_counts[0]++;
  after.paragraphs[1].paragraph++;
  after.paragraphs.splice(1, 0, { section: 0, paragraph: 1, length: 0, text_sha256: hash("f"), lines: [] });
  assert.doesNotThrow(() => assertBodyLayoutPreserved(before, after, { insert_after: 0, top_level_count: 1 }));
});

test("changed line boundaries or justified spacing blocks a preview despite identical text", () => {
  const before = evidence(), spacing = copy(before), lines = copy(before), text = copy(before);
  spacing.paragraphs[0].lines[0].glyphs[1][1] += 10;
  lines.paragraphs[0].lines = [{ start: 0, end: 1, glyphs: [[0, 0, 8]] }, { start: 1, end: 3, glyphs: [[1, 0, 8], [2, 8, 8]] }];
  text.paragraphs[0].text_sha256 = hash("f");
  for (const after of [spacing, lines, text]) assert.throws(() => assertBodyLayoutPreserved(before, after), /줄바꿈 또는 글자 간격/);
});

test("subpixel tolerance is bounded and cannot hide reordered glyphs", () => {
  const before = evidence(), after = copy(before);
  after.paragraphs[0].lines[0].glyphs[1][1] += .75;
  assert.doesNotThrow(() => assertBodyLayoutPreserved(before, after));
  after.paragraphs[0].lines[0].glyphs[1][1] += .01;
  assert.throws(() => assertBodyLayoutPreserved(before, after));
  const reordered = copy(before); reordered.paragraphs[0].lines[0].glyphs.reverse();
  assert.throws(() => assertBodyLayoutPreserved(before, reordered));
});

test("missing, duplicate, out-of-range or unbounded evidence fails closed", () => {
  const before = evidence();
  const cases = [null, {}, { ...before, page_count: 0 }, { ...before, page_count: 201 }, { ...before, paragraph_counts: [] }];
  const duplicate = copy(before); duplicate.paragraphs[1] = copy(duplicate.paragraphs[0]); cases.push(duplicate);
  const invalidGlyph = copy(before); invalidGlyph.paragraphs[0].lines[0].glyphs[0][1] = Infinity; cases.push(invalidGlyph);
  const missing = copy(before); missing.paragraphs.pop(); cases.push(missing);
  for (const after of cases) assert.throws(() => assertBodyLayoutPreserved(before, after));
  assert.throws(() => assertBodyLayoutPreserved(before, before, { insert_after: 0, top_level_count: 0 }));
});
