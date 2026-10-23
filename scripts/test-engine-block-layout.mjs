// Real WASM regression for the read-only block-layout receipt. The thin Studio
// host is supplied in a VM; document parsing, export, pagination, and glyph
// positions all come from the requested native core. No AI/server is mocked.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { runInNewContext } from 'node:vm';
import { performance } from 'node:perf_hooks';
import ts from '../frontend/node_modules/typescript/lib/typescript.js';
import { unzipSync, zipSync, strFromU8, strToU8 } from '../frontend/node_modules/fflate/esm/index.mjs';

const core = resolve(process.argv[2] || '.cache/rhwp-core-fix/pkg');
const { default: init, HwpDocument } = await import(pathToFileURL(resolve(core, 'rhwp.js')));
await init({ module_or_path: await readFile(resolve(core, 'rhwp_bg.wasm')) });
const source = (await readFile(new URL('./assistant-bridge.ts.txt', import.meta.url), 'utf8'))
  .replace(/^import .*;\r?\n/gm, '');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const comparatorSource = await readFile(new URL('../frontend/src/block-layout.ts', import.meta.url), 'utf8');
const comparatorExports = {};
runInNewContext(ts.transpileModule(comparatorSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports: comparatorExports }, { filename: 'block-layout.ts' });
const { assertBodyLayoutPreserved } = comparatorExports;
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const plain = value => JSON.parse(JSON.stringify(value));

function reportedExport(doc) {
  const artifact = doc.exportHwpxWithReport();
  try { return { bytes: artifact.takeBytes(), contentLoss: JSON.parse(artifact.contentLoss()) }; }
  finally { artifact.free(); }
}
function bridge(doc, pageCount = () => JSON.parse(doc.getDocumentInfo()).pageCount) {
  const window = {};
  const host = {
    window, performance, TextEncoder,
    assistantSha256: value => createHash('sha256').update(value).digest(),
    assistantHex: value => value.toString('hex'),
    eventBus: { on() {} },
    documentAgent: { getDocumentState: () => ({ documentEpoch: 1, changeSeq: 0 }) },
    wasm: {
      documentGeneration: 1,
      get pageCount() { return pageCount(); },
      borrowDocumentHandle: () => doc,
      exportHwpxWithReport: () => reportedExport(doc),
    },
  };
  runInNewContext(compiled, host, { filename: 'assistant-bridge.ts.txt' });
  return window.hwpAssistantBridge;
}
function capture(doc, label) {
  const before = hash(doc.exportHwpx());
  const start = performance.now();
  const evidence = plain(bridge(doc).bodyLayoutEvidence());
  const elapsed = performance.now() - start;
  assert.equal(hash(doc.exportHwpx()), before, `${label}: read-only receipt must not change the export`);
  assert.equal(evidence.export_sha256, before, `${label}: receipt must bind to the same bytes`);
  assert.equal(evidence.paragraphs.length, evidence.paragraph_counts.reduce((sum, count) => sum + count, 0));
  console.log(JSON.stringify({ label, pages: evidence.page_count, paragraphs: evidence.paragraphs.length,
    glyphs: evidence.paragraphs.flatMap(p => p.lines).reduce((sum, line) => sum + line.glyphs.length, 0),
    capture_ms: Math.round(elapsed * 100) / 100, export_unchanged: true }));
  return evidence;
}
function blank() { const doc = HwpDocument.createEmpty(); doc.createBlankDocument(); return doc; }
function paragraphLayout(evidence, paragraph) {
  const value = evidence.paragraphs.find(p => p.section === 0 && p.paragraph === paragraph);
  assert.ok(value);
  const { section, paragraph: ignored, ...layout } = value;
  return layout;
}
function appendTestParagraphAfterFirst(bytes, text) {
  const files = unzipSync(bytes);
  // This synthetic fixture has no nested controls. No user/HWPX source file is
  // rewritten and no production code uses this test-only XML insertion.
  const xml = strFromU8(files['Contents/section0.xml']);
  assert.equal(xml.match(/<hp:p\b/g)?.length, 3);
  const at = xml.indexOf('</hp:p>') + '</hp:p>'.length;
  files['Contents/section0.xml'] = strToU8(xml.slice(0, at) +
    `<hp:p id="999" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t>${text}</hp:t></hp:run></hp:p>` + xml.slice(at));
  return zipSync(files);
}

const normal = blank();
normal.insertText(0, 0, 0, '정상적인 첫 문단입니다. 😀');
normal.insertParagraph(0, 1);
normal.insertText(0, 1, 0, '기존 둘째 문단의 한글과 English 글자 간격을 보존합니다. '.repeat(12));
normal.insertParagraph(0, 2); // An empty paragraph must also be in the receipt.
const normalBefore = capture(normal, 'body-layout-before-block');
assert.equal(normalBefore.paragraphs[0].lines.length, 1, 'first body paragraph has visible text');
assert.ok(normalBefore.paragraphs[1].lines.length > 1, 'fixture contains actual wrapped text');
assert.equal(normalBefore.paragraphs[2].length, 0);
assert.deepEqual(normalBefore.paragraphs[2].lines, []);
const inserted = new HwpDocument(appendTestParagraphAfterFirst(normal.exportHwpx(), '새 블록 제목'));
const normalAfter = capture(inserted, 'body-layout-after-block');
assert.deepEqual(normalAfter.paragraph_counts, [4]);
assert.deepEqual(paragraphLayout(normalBefore, 0), paragraphLayout(normalAfter, 0), 'insertion preserves first body paragraph');
assert.deepEqual(paragraphLayout(normalBefore, 1), paragraphLayout(normalAfter, 2), 'line/glyph receipt follows existing paragraphs after insertion');
assert.deepEqual(paragraphLayout(normalBefore, 2), paragraphLayout(normalAfter, 3), 'empty paragraph follows insertion');
assert.doesNotThrow(() => assertBodyLayoutPreserved(normalBefore, normalAfter,
  { insert_after: 0, top_level_count: 1 }), 'the production frontend guard permits a normal insertion');
assert.throws(() => bridge(normal, () => 201).bodyLayoutEvidence(), /200/,
  'oversized rendering requests fail before enumerating pages');
inserted.free(); normal.free();

const multipage = blank();
multipage.insertText(0, 0, 0, '여러 쪽 문서의 첫 문단');
multipage.insertParagraph(0, 1);
multipage.insertText(0, 1, 0, '여러 쪽에 걸친 기존 문단은 앞에 블록이 추가되어 쪽 위치가 바뀌어도 줄과 글자 간격을 유지합니다. '.repeat(150));
multipage.insertParagraph(0, 2);
const multipleBefore = capture(multipage, 'multipage-before-block');
assert.ok(multipleBefore.page_count > 1, 'fixture must exercise actual pagination');
const moved = new HwpDocument(appendTestParagraphAfterFirst(multipage.exportHwpx(), '추가 블록의 내용입니다. '.repeat(200)));
const multipleAfter = capture(moved, 'multipage-after-block');
assert.ok(multipleAfter.page_count > multipleBefore.page_count, 'block must actually move existing text onto later pages');
assert.doesNotThrow(() => assertBodyLayoutPreserved(multipleBefore, multipleAfter,
  { insert_after: 0, top_level_count: 1 }), 'the production guard permits native page movement with unchanged body lines');
moved.free(); multipage.free();

// This is the exact public-API sequence that exposed the current pinned core's
// first-paragraph control-axis bug, without requiring the block API at all.
// An engine fix may make it stable later; until then the receipts must expose
// the actual differing line boundaries/glyph advances to the frontend guard.
const explicit = blank();
explicit.insertText(0, 0, 0, 'HWP Assistant 점검용 문서\n이 문서는 기능 점검을 위해 만든 시험 문서입니다.\n실제 업무자료나 개인정보는 포함하지 않습니다.');
const explicitBefore = capture(explicit, 'explicit-line-breaks-before-reload');
assert.equal(explicitBefore.paragraphs[0].lines.length, 3, 'fresh document renders the three explicit lines');
const reloaded = new HwpDocument(explicit.exportHwpx());
const explicitAfter = capture(reloaded, 'explicit-line-breaks-after-reload');
assert.equal(explicitBefore.paragraphs[0].text_sha256, explicitAfter.paragraphs[0].text_sha256, 'bug fixture preserves its text');
const changed = JSON.stringify(paragraphLayout(explicitBefore, 0)) !== JSON.stringify(paragraphLayout(explicitAfter, 0));
console.log(JSON.stringify({ label: 'known-first-paragraph-roundtrip', layout_change_detected: changed,
  before_lines: explicitBefore.paragraphs[0].lines.length, after_lines: explicitAfter.paragraphs[0].lines.length }));
if (changed) {
  assert.notDeepEqual(explicitBefore.paragraphs[0].lines, explicitAfter.paragraphs[0].lines,
    'receipt must expose the real rendering change even though text and source styles survive');
  assert.throws(() => assertBodyLayoutPreserved(explicitBefore, explicitAfter), /줄바꿈|글자 간격/,
    'the production frontend guard refuses the native round-trip distortion');
} else assert.doesNotThrow(() => assertBodyLayoutPreserved(explicitBefore, explicitAfter));
reloaded.free(); explicit.free();
console.log('PASS: bounded, read-only body glyph evidence; normal block insertion and native round-trip diagnosis');
