// Real WASM serialization, fixed synthetic text; no AI call or HWPX rewriting.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { unzipSync, strFromU8 } from '../frontend/node_modules/fflate/esm/index.mjs';

const core = resolve(process.argv[2] || 'frontend/node_modules/@rhwp/core');
const { default: init, HwpDocument } = await import(pathToFileURL(resolve(core, 'rhwp.js')));
await init({ module_or_path: await readFile(resolve(core, 'rhwp_bg.wasm')) });
const header = doc => strFromU8(unzipSync(doc.exportHwpx())['Contents/header.xml']);
const brushes = xml => [...xml.matchAll(/<hc:winBrush\b[^>]*\/>/g)].map(m => m[0]);
function document() {
  const doc = HwpDocument.createEmpty();
  doc.createBlankDocument();
  doc.insertText(0, 0, 0, '배경 보존 회귀 시험');
  return doc;
}
const plain = document();
assert.equal(brushes(header(plain)).some(b => /faceColor="none"/.test(b) && !/hatchStyle=/.test(b)), false,
  'No-fill brush must not be emitted as a solid faceColor=none');
const reopened = new HwpDocument(plain.exportHwpx());
assert.ok(brushes(header(reopened)).every(b => /faceColor="#FFFFFFFF"/.test(b)), 'No-fill sentinel survives engine reopen');
assert.ok(header(reopened).includes('shadeColor="#FFFFFFFF"'), 'Character shading uses the native Hancom sentinel');
assert.ok(!header(reopened).includes('shadeColor="none"'), 'No unsupported lexical no-shading value');
plain.free(); reopened.free();
console.log('PASS: default no-fill and engine reopen');

for (const color of ['#000000', '#FFFFFF', '#FFCC00']) {
  const doc = document();
  doc.applyParaFormat(0, 0, JSON.stringify({ fillType: 'solid', fillColor: color }));
  assert.ok(brushes(header(doc)).some(b => b.includes(`faceColor="${color}"`)), `${color} must survive export`);
  const again = new HwpDocument(doc.exportHwpx());
  assert.ok(brushes(header(again)).some(b => b.includes(`faceColor="${color}"`)), `${color} must survive reopen`);
  doc.free(); again.free();
  console.log(`PASS: real solid ${color} preserved`);
  const shaded = document();
  shaded.applyCharFormat(0, 0, 0, 4, JSON.stringify({ shadeColor: color }));
  assert.ok(header(shaded).includes(`shadeColor="${color}"`), `${color} character shading must survive`);
  const shadedAgain = new HwpDocument(shaded.exportHwpx());
  assert.ok(header(shadedAgain).includes(`shadeColor="${color}"`), `${color} character shading must survive reopen`);
  shaded.free(); shadedAgain.free();
  console.log(`PASS: real character shading ${color} preserved`);
}
const pattern = document();
pattern.applyParaFormat(0, 0, JSON.stringify({ fillType: 'solid', fillColor: '#FFFFFF', patternColor: '#000000', patternType: 1 }));
assert.ok(brushes(header(pattern)).some(b => /hatchStyle=/.test(b) && /hatchColor="#000000"/.test(b)), 'Hatch must survive export');
const patternAgain = new HwpDocument(pattern.exportHwpx());
assert.ok(brushes(header(patternAgain)).some(b => /hatchStyle=/.test(b)), 'Hatch must survive reopen');
pattern.free(); patternAgain.free();
console.log('PASS: hatch preserved');
