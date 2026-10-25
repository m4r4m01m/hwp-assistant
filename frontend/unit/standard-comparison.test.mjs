import test from 'node:test';
import assert from 'node:assert/strict';
import { loadSource } from './load-source.mjs';
const { compareSampleRegions, alignComparisonPages } = loadSource('standard-preview-regions.ts');
function page(text, { role = 'parenthetical_square', x = 20, y = 30, style = 'black', wrap = 100 } = {}) {
  return [{ width: 800, height: 1100, rects: [], regions: [{ id: `${role}:0`, role, text,
    glyphs: [...text].map((text, i) => ({ text, start: i, end: i + 1, style,
      rect: { x: x + i % wrap * 10, y: y + Math.floor(i / wrap) * 15, width: 10, height: 12 } })) }] }];
}
test('parenthetical comparison marks inserted label, never unchanged explanation', () => {
  const a = page('□자료확인함'), b = page('□(현황)자료확인함');
  const result = compareSampleRegions(a, b, ['body_parenthetical_labels'], 'appendix-1');
  assert.equal(result.before[0].length, 0);
  assert.equal(result.after[0].length, 1);
  assert.equal(result.after[0][0].x, 30);
  assert.equal(result.after[0][0].width, 40);
  const reverse = compareSampleRegions(b, a, ['body_parenthetical_labels'], 'appendix-1');
  assert.equal(reverse.after[0].length, 0);
  assert.equal(reverse.before[0][0].width, 40);
});
test('unchanged paragraphs and vertical displacement are not changes', () => {
  const a = page('자료확인함', { role: 'square' });
  const b = page('자료확인함', { role: 'square', y: 500 });
  assert.equal(compareSampleRegions(a, b, ['square_color'], 'appendix-1').after[0].length, 0);
  assert.equal(compareSampleRegions(a, a, ['line_space_policy'], 'appendix-1').after[0].length, 0);
});
test('actual style and wrap changes retain a setting key for the matching UI color', () => {
  const a = page('자료확인함', { role: 'square' });
  const b = page('자료확인함', { role: 'square', style: 'blue' });
  const marks = compareSampleRegions(a, b, ['square_color'], 'appendix-1').after[0];
  assert.ok(marks.length > 0); assert.equal(marks[0].setting, 'square_color');
  const c = page('자료확인함', { role: 'square', wrap: 3 });
  assert.ok(compareSampleRegions(a, c, ['line_space_policy'], 'appendix-1').after[0].length > 0);
});
test('cover and TOC additions align the same body pages, including reverse removal', () => {
  const a = page('본문', { role: 'square' });
  const b = [...page('표지', { role: 'cover_title' }), ...page('목차', { role: 'toc_title' }), ...a];
  assert.deepEqual(alignComparisonPages(a, b), [{ before: null, after: 0 }, { before: null, after: 1 }, { before: 0, after: 2 }]);
  assert.deepEqual(alignComparisonPages(b, a), [{ before: 0, after: null }, { before: 1, after: null }, { before: 2, after: 0 }]);
});
test('two separate inline note changes do not underline the unchanged middle words', () => {
  const a = page('A*와중간설명B**이다', { role: 'square' });
  const b = page('A주1)와중간설명B주2)이다', { role: 'square' });
  const marks = compareSampleRegions(a, b, ['citation_style'], 'appendix-1').after[0];
  assert.equal(marks.length, 2);
  assert.equal(marks[0].width, 30);
  assert.equal(marks[1].width, 30);
});
