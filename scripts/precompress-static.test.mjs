import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync, mkdirSync, symlinkSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { gunzipSync } from 'node:zlib';
import { randomBytes } from 'node:crypto';
import { precompressStatic } from './precompress-static.mjs';

function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'hwp-static-gzip-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test('compression is deterministic and preserves original and decompressed bytes', t => {
  const dir = fixture(t), path = join(dir, 'rhwp-test.wasm');
  const source = Buffer.from('WASM synthetic byte fixture '.repeat(1000));
  writeFileSync(path, source);
  assert.equal(precompressStatic(dir).files, 1);
  const first = readFileSync(path + '.gz');
  assert.deepEqual(gunzipSync(first), source);
  assert.deepEqual(readFileSync(path), source);
  precompressStatic(dir);
  assert.deepEqual(readFileSync(path + '.gz'), first);
});

test('small or incompressible files and already compressed formats are not retained as gzip', t => {
  const dir = fixture(t);
  for (const [name, bytes] of [['small.js', Buffer.from('small')], ['noise.wasm', randomBytes(4096)], ['font.woff2', Buffer.alloc(4096)]]) {
    writeFileSync(join(dir, name), bytes);
    writeFileSync(join(dir, name + '.gz'), 'stale');
  }
  writeFileSync(join(dir, 'removed.js.gz'), 'stale');
  assert.equal(precompressStatic(dir).files, 0);
  for (const name of ['small.js', 'noise.wasm', 'font.woff2', 'removed.js'])
    assert.equal(existsSync(join(dir, name + '.gz')), false);
});

test('nested assets are compressed without following symlinks', t => {
  const dir = fixture(t), outside = fixture(t);
  mkdirSync(join(dir, 'assets'));
  writeFileSync(join(dir, 'assets', 'app-hashed.js'), 'const repeated=1;'.repeat(1000));
  writeFileSync(join(outside, 'external.js'), 'const external=1;'.repeat(1000));
  symlinkSync(outside, join(dir, 'linked'));
  assert.equal(precompressStatic(dir).files, 1);
  assert.equal(existsSync(join(outside, 'external.js.gz')), false);
});
