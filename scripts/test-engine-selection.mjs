// Real WASM, read-only selection queries. Optional documents are read as copies;
// neither their text nor their source HWPX is rewritten by this check.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve, basename } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { unzipSync, zipSync, strFromU8, strToU8 } from '../frontend/node_modules/fflate/esm/index.mjs';

const [coreArg, ...args] = process.argv.slice(2);
const baselineAt = args.indexOf('--baseline');
const baselineArg = baselineAt >= 0 ? args.splice(baselineAt, 2)[1] : null;
const inputs = args;
const core = resolve(coreArg || '.cache/rhwp-core-fix/pkg');
const { default: init, HwpDocument } = await import(pathToFileURL(resolve(core, 'rhwp.js')));
await init({ module_or_path: await readFile(resolve(core, 'rhwp_bg.wasm')) });
let BaselineDocument = null;
if (baselineArg) {
  const baseline = resolve(baselineArg);
  const module = await import(pathToFileURL(resolve(baseline, 'rhwp.js')));
  await module.default({ module_or_path: await readFile(resolve(baseline, 'rhwp_bg.wasm')) });
  BaselineDocument = module.HwpDocument;
}
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const parse = json => JSON.parse(json);
const key = run => `${run.secIdx}/${run.paraIdx}`;
const bodyRun = run => Number.isInteger(run.secIdx) && Number.isInteger(run.paraIdx)
  && Number.isInteger(run.charStart) && run.parentParaIdx === undefined && [...run.text].length;
function physicalLines(doc) {
  const lines = [];
  const pages = parse(doc.getDocumentInfo()).pageCount;
  for (let page = 0; page < pages; page++) {
    for (const run of parse(doc.getPageTextLayout(page)).runs.filter(bodyRun)) {
      let line = lines.find(line => line.page === page && line.key === key(run) && Math.abs(line.y - run.y) < 0.15);
      if (!line) {
        line = { key: key(run), sec: run.secIdx, para: run.paraIdx, page, y: run.y,
          start: run.charStart, end: run.charStart + [...run.text].length,
          right: run.x + run.charX.at(-1) };
        lines.push(line);
      } else {
        line.start = Math.min(line.start, run.charStart);
        line.end = Math.max(line.end, run.charStart + [...run.text].length);
        line.right = Math.max(line.right, run.x + run.charX.at(-1));
      }
    }
  }
  return lines;
}
function assertCovered(rects, lines, label) {
  assert.equal(rects.length, lines.length, `${label}: one rectangle per selected physical line`);
  for (const line of lines) {
    assert.ok(rects.some(rect => rect.pageIndex === line.page && Math.abs(rect.y - line.y) < 0.2
      && rect.width > 0 && rect.height > 0), `${label}: missing page ${line.page} line at y=${line.y}`);
  }
}
function check(doc, label) {
  const exportBefore = hash(doc.exportHwpx());
  const lines = physicalLines(doc);
  const groups = [...new Set(lines.map(line => line.key))].map(id => lines.filter(line => line.key === id));
  const wrapped = groups.filter(lines => lines.length > 1);
  assert.ok(wrapped.length, `${label}: fixture must include actual wrapped body text`);
  let partialChecks = 0;
  for (const group of groups) {
    const { sec, para } = group[0], length = doc.getParagraphLength(sec, para);
    const full = parse(doc.getSelectionRects(sec, para, 0, para, length));
    assertCovered(full, group, `${label}: ${sec}/${para}`);
    const lastLines = group.filter(line => line.end === length);
    if (lastLines.length > 1 && new Set(lastLines.map(line => line.page)).size > 1) {
      for (const line of lastLines) {
        const rect = full.find(rect => rect.pageIndex === line.page && Math.abs(rect.y - line.y) < 0.2);
        assert.ok(Math.abs(rect.x + rect.width - line.right) < 1,
          `${label}: duplicated final logical line stops at its actual text end`);
      }
    }
    if (group.length > 1) {
      const start = group[0].start + 1, end = group.at(-1).start + 1;
      assertCovered(parse(doc.getSelectionRects(sec, para, start, para, end)), group,
        `${label}: partial first/last lines`);
      const boundary = group[1].start;
      assertCovered(parse(doc.getSelectionRects(sec, para, 0, para, boundary)), [group[0]],
        `${label}: exact wrapped-line boundary`);
      partialChecks += 2;
    }
  }
  // The Studio orders anchor/focus before this native call. Check both drag orders
  // against the identical ordered cross-paragraph range without changing content.
  const candidates = groups.filter(group => group[0].sec === 0);
  let crossParagraph = false;
  if (candidates.length > 1) {
    const first = candidates[0][0], last = candidates.at(-1).at(-1);
    const start = { para: first.para, offset: 0 }, end = { para: last.para, offset: doc.getParagraphLength(0, last.para) };
    const ordered = (anchor, focus) => [anchor, focus].sort((a, b) => a.para - b.para || a.offset - b.offset);
    const query = (anchor, focus) => {
      const [a, b] = ordered(anchor, focus);
      return parse(doc.getSelectionRects(0, a.para, a.offset, b.para, b.offset));
    };
    const forward = query(start, end);
    assert.deepEqual(query(end, start), forward, `${label}: normalized reverse drag`);
    assertCovered(forward, lines.filter(line => line.sec === 0 && line.para >= start.para && line.para <= end.para),
      `${label}: cross-paragraph selection`);
    crossParagraph = true;
  }
  assert.equal(hash(doc.exportHwpx()), exportBefore, `${label}: selection must not alter exported document`);
  console.log(JSON.stringify({ label, body_paragraphs: groups.length, wrapped_paragraphs: wrapped.length,
    physical_lines: lines.length, partial_checks: partialChecks, cross_paragraph: crossParagraph,
    normalized_reverse: crossParagraph, export_sha256_unchanged: exportBefore }));
}

function compareUnchangedLayoutAndCells(doc, bytes, label) {
  if (!BaselineDocument) return;
  const before = new BaselineDocument(bytes);
  assert.equal(hash(doc.exportHwpx()), hash(before.exportHwpx()), `${label}: identical serialization`);
  const pageCount = parse(doc.getDocumentInfo()).pageCount;
  assert.equal(pageCount, parse(before.getDocumentInfo()).pageCount, `${label}: same pagination`);
  const seen = new Set();
  const layoutHashes = [];
  let cellChecks = 0;
  for (let page = 0; page < pageCount; page++) {
    const layout = doc.getPageTextLayout(page);
    assert.equal(layout, before.getPageTextLayout(page), `${label}: same text/layout page ${page}`);
    layoutHashes.push(hash(Buffer.from(layout)));
    for (const run of parse(layout).runs) {
      if (!Number.isInteger(run.parentParaIdx) || !run.text || run.cellPath?.length > 1) continue;
      const id = [run.secIdx, run.parentParaIdx, run.controlIdx, run.cellIdx, run.cellParaIdx].join('/');
      if (seen.has(id) || cellChecks >= 12) continue;
      seen.add(id);
      const end = run.charStart + [...run.text].length;
      const query = [run.secIdx, run.parentParaIdx, run.controlIdx, run.cellIdx,
        run.cellParaIdx, run.charStart, run.cellParaIdx, end];
      assert.deepEqual(parse(doc.getSelectionRectsInCell(...query)), parse(before.getSelectionRectsInCell(...query)),
        `${label}: existing cell selection ${id}`);
      cellChecks++;
    }
  }
  before.free();
  console.log(JSON.stringify({ label, unchanged_layout_pages: pageCount, unchanged_cell_queries: cellChecks,
    identical_native_export_sha256: hash(doc.exportHwpx()),
    identical_layout_page_hashes_sha256: hash(Buffer.from(JSON.stringify(layoutHashes))) }));
}

function staleLineFixture(doc) {
  const files = unzipSync(doc.exportHwpx());
  files['Contents/section0.xml'] = strToU8(strFromU8(files['Contents/section0.xml'])
    .replace(/(<hp:linesegarray>)([\s\S]*?)(<\/hp:linesegarray>)/g, (all, open, content, close) => {
      const first = content.match(/<hp:lineseg\b[^>]*\/>/);
      assert.ok(first, 'synthetic input must have a saved line segment');
      return open + first[0] + close;
    }));
  return zipSync(files);
}

const synthetic = HwpDocument.createEmpty();
synthetic.createBlankDocument();
synthetic.insertText(0, 0, 0, '□ (범위) 실제로 다시 줄바꿈된 본문 전체에 선택 영역이 표시되어야 합니다. '.repeat(12));
synthetic.applyCharFormat(0, 0, 0, synthetic.getParagraphLength(0, 0), JSON.stringify({ fontSize: 1500 }));
synthetic.applyCharFormat(0, 0, 2, 6, JSON.stringify({ bold: true }));
check(synthetic, 'synthetic-wrapped-mixed-format');
// Imported scaffolds can contain one saved placeholder line while the engine
// paints many lines. Construct that test-only input in memory; never rewrite a
// user's or historical HWPX and never use this fixture step in production.
const fixtureBytes = staleLineFixture(synthetic);
const imported = new HwpDocument(fixtureBytes);
assert.equal(parse(imported.getLineInfo(0, 0, 0)).lineCount, 1, 'one placeholder line is retained in imported metadata');
check(imported, 'synthetic-imported-stale-line-metadata');
compareUnchangedLayoutAndCells(imported, fixtureBytes, 'synthetic-imported-stale-line-metadata');
imported.free();
synthetic.free();
const multipage = HwpDocument.createEmpty();
multipage.createBlankDocument();
multipage.insertText(0, 0, 0, '□ 실제 본문 줄과 쪽 경계에서도 선택 영역이 유지되어야 합니다. '.repeat(70));
multipage.applyCharFormat(0, 0, 0, multipage.getParagraphLength(0, 0), JSON.stringify({ fontSize: 1500 }));
const multipageBytes = staleLineFixture(multipage);
const split = new HwpDocument(multipageBytes);
assert.ok(parse(split.getDocumentInfo()).pageCount > 1, 'synthetic fixture must split one paragraph across pages');
check(split, 'synthetic-imported-multipage-paragraph');
compareUnchangedLayoutAndCells(split, multipageBytes, 'synthetic-imported-multipage-paragraph');
split.free(); multipage.free();
const equation = HwpDocument.createEmpty();
equation.createBlankDocument();
equation.insertText(0, 0, 0, '수식 뒤 본문 선택 확인');
equation.insertEquation(0, 0, 0, 'x=1', 1000, 0);
const equationQuery = [0, 0, 0, 0, equation.getParagraphLength(0, 0)];
const equationRects = parse(equation.getSelectionRects(...equationQuery));
assert.equal(equationRects.length, 1, 'inline equation and its body text remain selectable');
assert.ok(equationRects[0].width > 0 && equationRects[0].height > 0);
if (BaselineDocument) {
  const before = new BaselineDocument(equation.exportHwpx());
  assert.deepEqual(equationRects, parse(before.getSelectionRects(...equationQuery)),
    'inline equation selection uses the unchanged logical-control path');
  before.free();
  console.log('PASS: unchanged inline equation selection');
}
equation.free();
for (const input of inputs) {
  const bytes = await readFile(resolve(input));
  const doc = new HwpDocument(bytes);
  check(doc, basename(input));
  compareUnchangedLayoutAndCells(doc, bytes, basename(input));
  doc.free();
}
console.log('PASS: actual body lines, partial boundaries, cross-paragraph ranges, immutable export');
