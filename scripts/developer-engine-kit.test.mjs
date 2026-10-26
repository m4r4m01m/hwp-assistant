import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { zipSync } from '../frontend/node_modules/fflate/esm/index.mjs';
import { root, lock, hashFile, sha256 } from './engine-build-lib.mjs';
import { controllerReceipt } from './studio-controller-patch.mjs';

const temporary = resolve(root, '.cache/developer-kit-tests');
mkdirSync(temporary, { recursive: true });
const directory = mkdtempSync(resolve(temporary, 'run-'));
const run = args => spawnSync(process.execPath, [resolve(root, 'scripts/developer-engine-kit.mjs'), ...args],
  { cwd: root, encoding: 'utf8' });

test('developer kit rejects an archive whose SHA-256 differs before creating output', () => {
  const archive = resolve(directory, 'wrong-hash.zip'), out = resolve(directory, 'wrong-hash-output');
  writeFileSync(archive, 'not the expected archive');
  const result = run(['unpack', '--archive', archive, '--sha256', '0'.repeat(64), '--out', out]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /SHA-256 does not match/);
  assert.equal(existsSync(out), false);
});

test('developer kit rejects ZIP traversal even when the outer archive hash matches', () => {
  const archive = resolve(directory, 'traversal.zip'), out = resolve(directory, 'traversal-output');
  const bytes = zipSync({ 'rhwp/../../escape.txt': new TextEncoder().encode('unsafe') });
  writeFileSync(archive, bytes);
  const result = run(['unpack', '--archive', archive, '--sha256', sha256(bytes), '--out', out]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Unsafe engine archive path/);
  assert.equal(existsSync(out), false);
  assert.equal(existsSync(resolve(directory, 'escape.txt')), false);
});

test('developer kit preserves an existing import output and its contents', () => {
  const archive = resolve(directory, 'not-used.zip'), out = resolve(directory, 'existing-output');
  mkdirSync(out); writeFileSync(resolve(out, 'user-work.txt'), 'preserve this');
  writeFileSync(archive, 'unused');
  const result = run(['unpack', '--archive', archive, '--sha256', '0'.repeat(64), '--out', out]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Preserving existing engine import output/);
  assert.equal(readFileSync(resolve(out, 'user-work.txt'), 'utf8'), 'preserve this');
});

test('developer kit rejects import destinations outside the project cache', () => {
  const out = resolve(root, 'frontend/public/do-not-create');
  const result = run(['unpack', '--archive', resolve(directory, 'unused.zip'), '--sha256', '0'.repeat(64), '--out', out]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /dedicated directory inside .cache/);
  assert.equal(existsSync(out), false);
});

test('developer kit requires the current controller receipt as well as matching bridge and core', () => {
  const publicRoot = resolve(directory, 'controller-receipt'), engine = resolve(publicRoot, 'rhwp');
  mkdirSync(resolve(engine, 'assets'), { recursive: true });
  mkdirSync(resolve(engine, 'fonts'));
  const files = { 'index.html': 'synthetic kit', 'assets/rhwp_bg-test.wasm': 'fixture, never executed',
    'LICENSE': 'synthetic notice', 'THIRD_PARTY_LICENSES.md': 'synthetic notice', 'fonts/FONTS.md': 'synthetic notice' };
  for (const [name, bytes] of Object.entries(files)) writeFileSync(resolve(engine, name), bytes);
  const bundle = Object.fromEntries(Object.entries(files).map(([name, bytes]) => [name, sha256(bytes)]));
  const info = { commit: lock.commit, diagnostic: true, bundle,
    bridge_sha256: hashFile(resolve(root, 'scripts/assistant-bridge.ts.txt')),
    coreBuild: { ...lock, profile: 'release', wasm_opt: false, wasm_sha256: bundle['assets/rhwp_bg-test.wasm'] } };
  const save = () => writeFileSync(resolve(engine, 'build-info.json'), JSON.stringify(info)); save();
  let result = run(['check', '--public', publicRoot]);
  assert.notEqual(result.status, 0); assert.match(result.stderr, /controller receipt/);
  info.controller = controllerReceipt(); save();
  result = run(['check', '--public', publicRoot]);
  assert.equal(result.status, 0, result.stderr);
  info.controller.helper_sha256 = '0'.repeat(64); save();
  result = run(['check', '--public', publicRoot]);
  assert.notEqual(result.status, 0); assert.match(result.stderr, /controller receipt/);
});
