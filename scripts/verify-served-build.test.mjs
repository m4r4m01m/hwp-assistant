import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { createHash } from 'node:crypto';
import { collectServedFiles, verifyServedBuild } from './verify-served-build.mjs';
const hash = value => createHash('sha256').update(value).digest('hex');
function fixture() {
  const base = resolve(import.meta.dirname, '../.cache/served-graph-tests'); mkdirSync(base, { recursive: true });
  const root = mkdtempSync(resolve(base, 'run-'));
  const put = (name, value) => { const file = resolve(root, name); mkdirSync(dirname(file), { recursive: true }); writeFileSync(file, value); };
  const binding = 'export default function init() {}';
  put('rhwp/index.html', '<html>engine fixture</html>'); put('rhwp/assets/rhwp_bg-fixture.wasm', 'WASM UNIT FIXTURE');
  put('rhwp/build-info.json', JSON.stringify({ bundle: { 'index.html': hash('<html>engine fixture</html>'),
    'assets/rhwp_bg-fixture.wasm': hash('WASM UNIT FIXTURE') }, coreBuild: { wasm_sha256: hash('WASM UNIT FIXTURE'), binding_sha256: hash(binding) } }));
  put('index.html', '<script type="module" src="/assets/app.js"></script><link rel="modulepreload" href="/assets/shared.js">');
  put('assets/app.js', 'import "./nested/child.js";'); put('assets/shared.js', 'export const shared = 1;');
  put('assets/nested/child.js', 'export { shared } from "../shared.js"; import("./deep/lazy.js");');
  put('assets/nested/deep/lazy.js', 'const preload = ["assets/lazy.css"]; export const lazy = true;');
  put('assets/lazy.css', '@import url("./theme.css"); body { background:url("./tiny.svg"); }');
  put('assets/theme.css', 'body { color: green; }'); put('assets/tiny.svg', '<svg/>');
  put('compare-renderer.html', '<script src="/assets/comparison.js" type="module"></script><link rel="modulepreload" href="/assets/compare-only.js">');
  put('assets/comparison.js', 'const url = "/comparison-core.js"; import(url);');
  put('assets/compare-only.js', 'import "./nested/compare-deep.js";'); put('assets/nested/compare-deep.js', 'export const page = 1;');
  put('comparison-core.js', binding);
  put('comparison-engine.json', JSON.stringify({ wasm: '/rhwp/assets/rhwp_bg-fixture.wasm', wasm_sha256: hash('WASM UNIT FIXTURE'), binding_sha256: hash(binding) }));
  return { root, put };
}
async function withServer(root, mutate, run) {
  const server = createServer((request, response) => {
    const name = new URL(request.url, 'http://127.0.0.1').pathname.slice(1);
    try { response.end(mutate(name, readFileSync(resolve(root, name)))); } catch { response.writeHead(404); response.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try { return await run(`http://127.0.0.1:${server.address().port}/`); }
  finally { await new Promise(resolve => server.close(resolve)); }
}
test('HTTP graph includes both entries, comparison binding/manifest and nested modulepreload/dynamic/CSS assets', async () => {
  const { root } = fixture();
  await withServer(root, (_name, bytes) => bytes, async url => {
    const evidence = await verifyServedBuild({ url, directory: root, built: true });
    for (const name of ['compare-renderer.html', 'comparison-core.js', 'comparison-engine.json', 'assets/nested/compare-deep.js',
      'assets/nested/deep/lazy.js', 'assets/lazy.css', 'assets/theme.css', 'assets/tiny.svg']) assert.ok(evidence.files.some(file => file.path === name), name);
  });
});
test('HTTP graph rejects a changed comparison-only nested chunk', async () => {
  const { root } = fixture();
  await withServer(root, (name, bytes) => name === 'assets/nested/compare-deep.js' ? Buffer.from('export const page = 2;') : bytes,
    async url => assert.rejects(verifyServedBuild({ url, directory: root, built: true }), /Served bytes differ: assets\/nested\/compare-deep.js/));
});
test('HTTP graph rejects a changed comparison binding', async () => {
  const { root } = fixture();
  await withServer(root, (name, bytes) => name === 'comparison-core.js' ? Buffer.from('export default function nope() {}') : bytes,
    async url => assert.rejects(verifyServedBuild({ url, directory: root, built: true }), /Served byte.*differs?: comparison-core.js/));
});
test('asset graph rejects escaping imports and external references before HTTP', () => {
  const { root, put } = fixture();
  put('assets/app.js', 'import "../../outside.js";'); assert.throws(() => collectServedFiles(root, { built: true }), /escapes bundle root/);
  put('assets/app.js', 'import "https://example.com/code.js";'); assert.throws(() => collectServedFiles(root, { built: true }), /Unsafe\/nonlocal/);
});
test('asset graph enforces a finite file count', () => {
  const { root } = fixture(); assert.throws(() => collectServedFiles(root, { built: true, limits: { files: 4 } }), /budget exceeded/);
});
