import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { comparisonAssets } from './comparison-core.mjs';
import { root, lock, hashFile, sha256 } from './engine-build-lib.mjs';
import { controllerReceipt } from './studio-controller-patch.mjs';

function fixture(t) {
  const directory = mkdtempSync(resolve(tmpdir(), 'hwp-comparison-core-'));
  t.after(() => rmSync(directory, { recursive: true }));
  const engine = resolve(directory, 'rhwp'); mkdirSync(resolve(engine, 'assets'), { recursive: true });
  const files = { 'index.html': '<html/>', 'assets/rhwp_bg-test.wasm': 'synthetic artifact, never executed' };
  for (const [name, value] of Object.entries(files)) writeFileSync(resolve(engine, name), value);
  const info = { commit: lock.commit, diagnostic: true, controller: controllerReceipt(), bundle: Object.fromEntries(Object.entries(files).map(([name, value]) => [name, sha256(value)])),
    coreBuild: { ...lock, profile: 'release', wasm_opt: false, wasm_sha256: sha256(files['assets/rhwp_bg-test.wasm']),
      binding_sha256: hashFile(resolve(root, 'frontend/node_modules/@rhwp/core/rhwp.js')) } };
  const save = () => writeFileSync(resolve(engine, 'build-info.json'), JSON.stringify(info)); save();
  return { directory, engine, info, save };
}

test('comparison packages only the binding matching the verified patched core', t => {
  const { directory, info } = fixture(t);
  const assets = comparisonAssets(directory, true);
  assert.equal(sha256(assets['comparison-core.js']), info.coreBuild.binding_sha256);
  const manifest = JSON.parse(assets['comparison-engine.json']);
  assert.equal(manifest.wasm, '/rhwp/assets/rhwp_bg-test.wasm');
  assert.equal(manifest.wasm_sha256, info.coreBuild.wasm_sha256);
  assert.equal(Object.keys(assets).length, 2);
  assert.throws(() => comparisonAssets(directory), /Diagnostic bundle/);
});

test('comparison refuses a mismatched binding or changed WASM instead of falling back', t => {
  const { directory, engine, info, save } = fixture(t);
  const correct = info.coreBuild.binding_sha256;
  info.coreBuild.binding_sha256 = '0'.repeat(64); save();
  assert.throws(() => comparisonAssets(directory, true), /binding does not match/);
  info.coreBuild.binding_sha256 = correct; save();
  writeFileSync(resolve(engine, 'assets/rhwp_bg-test.wasm'), 'changed');
  assert.throws(() => comparisonAssets(directory, true), /Bundle file\/hash mismatch/);
});
