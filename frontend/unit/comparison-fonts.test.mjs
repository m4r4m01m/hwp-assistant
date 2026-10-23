import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

function fixture() {
  let loads = 0, layout = 0, release;
  const faces = [];
  const fonts = { add(face) { faces.push(face); }, ready: new Promise(resolve => { release = resolve; }) };
  class FontFace {
    status = 'unloaded';
    constructor(family, source, options) { this.family = family; this.source = source; this.options = options; }
    load() { loads++; throw Error('Eager font load'); }
  }
  const output = ts.transpileModule(readFileSync(new URL('../src/comparison-fonts.ts', import.meta.url), 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const exports = {};
  vm.runInNewContext(output, { exports, FontFace, setTimeout, clearTimeout,
    require(name) { assert.equal(name, './public-path'); return { publicPath: path => '/hwp-assistant/' + path }; } });
  return { api: exports, fonts, faces, release,
    target: { getBoundingClientRect() { layout++; return {}; } },
    counts: () => ({ loads, layout }) };
}

test('comparison registers exact self-hosted faces once without downloading them', () => {
  const f = fixture();
  f.api.registerComparisonFonts(f.fonts);
  f.api.registerComparisonFonts(f.fonts);
  assert.equal(f.faces.length, 14);
  assert.equal(f.counts().loads, 0);
  assert.ok(f.faces.every(face => face.source.startsWith('url(/hwp-assistant/rhwp/fonts/')));
  assert.deepEqual(new Set(f.faces.map(face => face.options.weight)), new Set(['400', '700']));
});

test('visible page readiness waits for font layout and rejects failed faces', async () => {
  const f = fixture(); f.api.registerComparisonFonts(f.fonts);
  let done = false;
  const waiting = f.api.waitForComparisonFonts(f.target, f.fonts).then(() => { done = true; });
  await Promise.resolve();
  assert.equal(done, false); assert.equal(f.counts().layout, 1);
  f.release(); await waiting; assert.equal(done, true);
  f.faces[0].status = 'error';
  await assert.rejects(f.api.waitForComparisonFonts(f.target, f.fonts), /글꼴을 불러오지/);
});

test('font loading deadline terminates a stalled page', async () => {
  const f = fixture();
  await assert.rejects(f.api.waitForComparisonFonts(f.target, f.fonts, 5), /시간이 초과/);
});
