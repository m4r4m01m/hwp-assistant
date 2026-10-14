// Build gate regression fixtures. These do not execute Docker or claim optimization.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { root, lock, sha256, validateCore, ensureSource, isPinnedBinaryen, assertStudioBridge, validateInstalledDependencies } from './engine-build-lib.mjs';
import { checkBundle } from './check-engine-bundle.mjs';

const parent = resolve(root, '.cache/engine-build-tests');
mkdirSync(parent, { recursive: true });
const directory = () => mkdtempSync(resolve(parent, 'fixture-'));
function fixture(change = () => {}) {
  const path = directory();
  const wasm = Buffer.from('UNIT TEST FIXTURE; not an executable WASM');
  const binding = Buffer.from('// UNIT TEST FIXTURE');
  const info = { ...lock, profile: 'release', wasm_opt: false,
    wasm_sha256: sha256(wasm), binding_sha256: sha256(binding) };
  change(info);
  writeFileSync(resolve(path, 'rhwp_bg.wasm'), wasm);
  writeFileSync(resolve(path, 'rhwp.js'), binding);
  writeFileSync(resolve(path, 'assistant-core-build.json'), JSON.stringify(info));
  return path;
}

test('diagnostic core requires explicit opt-in', () => {
  const path = fixture();
  assert.throws(() => validateCore(path), /not a deployment build/);
  assert.equal(validateCore(path, { diagnostic: true }).wasm_opt, false);
});
test('missing metadata cannot fall back to unpatched npm core', () => {
  assert.throws(() => validateCore(directory()), /No npm-core fallback/);
});
test('different source, patch, or toolchain is refused', () => {
  for (const key of ['commit', 'patch_sha256', 'rust', 'wasm_bindgen']) {
    const path = fixture(info => { info[key] = 'different'; });
    assert.throws(() => validateCore(path, { diagnostic: true }), /provenance mismatch/);
  }
  const path = fixture(info => { info.source_sha256 = { ...lock.source_sha256, 'char_shapes.rs': 'different' }; });
  assert.throws(() => validateCore(path, { diagnostic: true }), /source mismatch/);
});
test('changed WASM and changed binding are independently refused', () => {
  for (const name of ['rhwp_bg.wasm', 'rhwp.js']) {
    const path = fixture();
    writeFileSync(resolve(path, name), 'tampered fixture');
    assert.throws(() => validateCore(path, { diagnostic: true }), /artifact hash mismatch/);
  }
});
test('optimization boolean alone is insufficient, including diagnostic opt-in', () => {
  const path = fixture(info => { info.wasm_opt = true; });
  assert.throws(() => validateCore(path, { diagnostic: true }), /optimization receipt/);
});
test('Binaryen version accepts its exact release tag but rejects another or dirty build', () => {
  assert.equal(isPinnedBinaryen('wasm-opt version 117'), true);
  assert.equal(isPinnedBinaryen('wasm-opt version 117 (version_117)'), true);
  for (const version of ['wasm-opt version 118', 'wasm-opt version 1170',
    'wasm-opt version 117 (version_117-1-gabcdef)', 'wasm-opt version 117 (version_118)'])
    assert.equal(isPinnedBinaryen(version), false);
});
test('receipt output must bind to the same WASM (synthetic metadata only)', () => {
  const path = fixture(info => {
    info.wasm_opt = true; info.build_environment = 'docker-linux';
    info.optimization = { version: `wasm-opt version ${lock.binaryen}`, arguments: ['-O'],
      input_sha256: 'a'.repeat(64), tool_sha256: 'b'.repeat(64), output_sha256: info.wasm_sha256 };
  });
  assert.equal(validateCore(path).wasm_opt, true);
  const info = JSON.parse(readFileSync(resolve(path, 'assistant-core-build.json')));
  info.optimization.output_sha256 = 'c'.repeat(64);
  writeFileSync(resolve(path, 'assistant-core-build.json'), JSON.stringify(info));
  assert.throws(() => validateCore(path), /optimization receipt/);
});
test('CLI refuses default-directory diagnostic publishing before any write', () => {
  const defaultInfo = resolve(root, 'frontend/public/rhwp/build-info.json');
  const before = existsSync(defaultInfo) ? readFileSync(defaultInfo) : null;
  const result = spawnSync(process.execPath, [resolve(root, 'scripts/build-engine.mjs'), '--diagnostic', '--core-dir', fixture()], { encoding: 'utf8' });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /separate --out/);
  assert.deepEqual(existsSync(defaultInfo) ? readFileSync(defaultInfo) : null, before);
});
test('served bundle detects stale or changed deployed bytes', () => {
  const path = directory();
  mkdirSync(resolve(path, 'assets'));
  const files = { 'index.html': 'fixture index', 'assets/rhwp_bg-fixture.wasm': 'fixture wasm' };
  for (const [name, bytes] of Object.entries(files)) writeFileSync(resolve(path, name), bytes);
  const bundle = Object.fromEntries(Object.entries(files).map(([name, bytes]) => [name, sha256(bytes)]));
  writeFileSync(resolve(path, 'build-info.json'), JSON.stringify({ commit: lock.commit, diagnostic: true,
    coreBuild: { ...lock, profile: 'release', wasm_opt: false, wasm_sha256: bundle['assets/rhwp_bg-fixture.wasm'] }, bundle }));
  assert.throws(() => checkBundle(path), /Diagnostic bundle/);
  assert.equal(checkBundle(path, { deployment: false }).diagnostic, true);
  writeFileSync(resolve(path, 'assets/rhwp_bg-fixture.wasm'), 'stale wasm');
  assert.throws(() => checkBundle(path, { deployment: false }), /Bundle file\/hash mismatch/);
});
test('CLI rejects altered core before creating output', () => {
  const core = fixture(info => { info.patch_sha256 = 'wrong'; });
  const out = resolve(directory(), 'must-not-exist');
  const result = spawnSync(process.execPath, [resolve(root, 'scripts/build-engine.mjs'), '--diagnostic', '--core-dir', core, '--out', out], { encoding: 'utf8' });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /provenance mismatch/);
  assert.equal(existsSync(out), false);
});
test('unrelated checkout and local files are preserved', () => {
  const repo = directory();
  const git = args => execFileSync('git', args, { cwd: repo, stdio: 'pipe' });
  git(['init']);
  writeFileSync(resolve(repo, 'local.txt'), 'keep');
  git(['add', 'local.txt']);
  git(['-c', 'user.name=Build fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture']);
  writeFileSync(resolve(repo, 'local.txt'), 'uncommitted work');
  const head = git(['rev-parse', 'HEAD']);
  assert.throws(() => ensureSource(repo), /Preserving vendor checkout/);
  assert.deepEqual(git(['rev-parse', 'HEAD']), head);
  assert.equal(readFileSync(resolve(repo, 'local.txt'), 'utf8'), 'uncommitted work');
});
test('bridge migration accepts only bytes bound to a verified previous receipt', () => {
  const original = '// pinned\n', old = Buffer.from('// old bridge\n'), current = '// new bridge\n';
  const previous = { bytes: old, sha256: sha256(old) };
  assertStudioBridge(original + old, original, current, previous);
  assertStudioBridge(original + current, original, current);
  assert.throws(() => assertStudioBridge(original + old, original, current), /Preserving local/);
  assert.throws(() => assertStudioBridge(original + old + '// user edit', original, current, previous), /Preserving local/);
  assert.throws(() => assertStudioBridge(original + old, original, current, { ...previous, sha256: 'a'.repeat(64) }), /receipt/);
});
test('offline dependency reuse rejects missing packages and mismatched installed receipts', () => {
  const path = directory();
  assert.throws(() => validateInstalledDependencies(path), /receipt is missing/);
  const packages = { 'node_modules/vite': { version: '1.0.0', resolved: 'fixture', integrity: 'fixture-hash' } };
  mkdirSync(resolve(path, 'node_modules/vite/bin'), { recursive: true });
  writeFileSync(resolve(path, 'package-lock.json'), JSON.stringify({packages}));
  writeFileSync(resolve(path, 'node_modules/.package-lock.json'), JSON.stringify({packages}));
  assert.throws(() => validateInstalledDependencies(path), /missing or changed/);
  writeFileSync(resolve(path, 'node_modules/vite/package.json'), JSON.stringify({version:'1.0.0'}));
  writeFileSync(resolve(path, 'node_modules/vite/bin/vite.js'), '// fixture');
  assert.ok(validateInstalledDependencies(path).vite.endsWith('vite.js'));
  writeFileSync(resolve(path, 'node_modules/.package-lock.json'), JSON.stringify({packages:{}}));
  assert.throws(() => validateInstalledDependencies(path), /differs from pinned lock/);
});
