// Portable, public rhwp assets only. Never package documents, DBs or credentials.
import { readFileSync, writeFileSync, mkdirSync, readdirSync, lstatSync, existsSync } from 'node:fs';
import { resolve, relative, dirname, isAbsolute } from 'node:path';
import { randomUUID } from 'node:crypto';
import { parseArgs } from 'node:util';
import { zipSync, unzipSync } from '../frontend/node_modules/fflate/esm/index.mjs';
import { checkBundle } from './check-engine-bundle.mjs';
import { root, hashFile, sha256 } from './engine-build-lib.mjs';

const { values, positionals } = parseArgs({ allowPositionals: true, options: {
  public: { type: 'string' }, out: { type: 'string' }, archive: { type: 'string' }, sha256: { type: 'string' },
} });
const operation = positionals[0];
const maxBytes = 250 * 1024 * 1024;
const check = publicDirectory => {
  const engine = resolve(publicDirectory, 'rhwp');
  const info = checkBundle(engine, { deployment: false });
  if (info.bridge_sha256 !== hashFile(resolve(root, 'scripts/assistant-bridge.ts.txt')))
    throw Error('Engine bridge does not match this checkout; build or obtain a matching developer kit.');
  for (const name of ['LICENSE', 'THIRD_PARTY_LICENSES.md', 'fonts/FONTS.md'])
    if (!existsSync(resolve(engine, name))) throw Error('Engine license notice is missing: ' + name);
  return info;
};
const safeName = name => name.length < 1024 && !name.includes('\\') && !name.includes(':') &&
  !name.startsWith('/') && !name.split('/').some(part => part === '..' || part === '.' || part === '');

if (operation === 'check') {
  const info = check(resolve(root, values.public));
  console.log(JSON.stringify({ mode: info.diagnostic ? 'native diagnostic developer engine' : 'release engine',
    wasm_sha256: info.coreBuild.wasm_sha256, bridge_sha256: info.bridge_sha256 }));
} else if (operation === 'pack') {
  const publicRoot = resolve(root, values.public), out = resolve(root, values.out);
  if (existsSync(out) || existsSync(out + '.sha256')) throw Error('Preserving existing engine archive/receipt. Choose a new output.');
  const info = check(publicRoot), files = {}, manifest = {};
  let total = 0;
  const visit = directory => {
    for (const entry of readdirSync(directory).sort()) {
      const path = resolve(directory, entry), stat = lstatSync(path);
      if (stat.isSymbolicLink()) throw Error('Do not package engine symlinks.');
      if (stat.isDirectory()) { visit(path); continue; }
      const name = relative(publicRoot, path).replaceAll('\\', '/');
      if (!safeName(name) || !name.startsWith('rhwp/') || !stat.isFile()) throw Error('Unexpected engine entry: ' + name);
      total += stat.size;
      if (total > maxBytes) throw Error('Engine archive exceeds size budget.');
      const bytes = readFileSync(path);
      files[name] = [new Uint8Array(bytes), { mtime: new Date('2026-10-18T00:00:00Z') }];
      manifest[name] = sha256(bytes);
    }
  };
  visit(resolve(publicRoot, 'rhwp'));
  const receipt = { kind: 'HWP Assistant portable developer engine', schema: 1,
    diagnostic: info.diagnostic, engine_commit: info.commit, wasm_sha256: info.coreBuild.wasm_sha256,
    bridge_sha256: info.bridge_sha256, files: manifest };
  files['developer-engine.json'] = [new TextEncoder().encode(JSON.stringify(receipt, null, 2)), { mtime: new Date('2026-10-18T00:00:00Z') }];
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, zipSync(files, { level: 6 }), { flag: 'wx' });
  writeFileSync(out + '.sha256', hashFile(out) + '\n', { flag: 'wx' });
  console.log(JSON.stringify({ archive: out, sha256: hashFile(out), files: Object.keys(manifest).length,
    uncompressed_bytes: total, diagnostic: info.diagnostic }));
} else if (operation === 'unpack') {
  const archive = resolve(root, values.archive), out = resolve(root, values.out);
  const cache = resolve(root, '.cache'), local = relative(cache, out);
  if (!local || local.startsWith('..') || isAbsolute(local)) throw Error('Engine import output must be a dedicated directory inside .cache.');
  if (existsSync(out) && readdirSync(out).length) throw Error('Preserving existing engine import output; choose an empty destination.');
  const expected = (values.sha256 || readFileSync(archive + '.sha256', 'utf8')).trim().toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(expected) || hashFile(archive) !== expected) throw Error('Engine archive SHA-256 does not match.');
  if (lstatSync(archive).size > maxBytes) throw Error('Engine archive is too large.');
  let total = 0;
  const files = unzipSync(readFileSync(archive), { filter: entry => {
    if (!safeName(entry.name) || entry.originalSize < 0 || (total += entry.originalSize) > maxBytes)
      throw Error('Unsafe engine archive path/size: ' + entry.name);
    return true;
  } });
  const receipt = JSON.parse(new TextDecoder().decode(files['developer-engine.json']));
  if (receipt.schema !== 1 || !receipt.files || Object.keys(files).length !== Object.keys(receipt.files).length + 1)
    throw Error('Invalid engine archive manifest.');
  for (const [name, bytes] of Object.entries(files)) {
    if (name === 'developer-engine.json') continue;
    if (!name.startsWith('rhwp/') || !safeName(name) || sha256(bytes) !== receipt.files[name])
      throw Error('Engine file/manifest mismatch: ' + name);
  }
  // Validate every extracted artifact before publishing the directory. Failed
  // staging directories remain for diagnosis, never replacing an existing one.
  const staging = resolve(cache, 'developer/engine-import', randomUUID());
  mkdirSync(staging, { recursive: true });
  for (const [name, bytes] of Object.entries(files)) {
    const path = resolve(staging, name);
    mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, bytes, { flag: 'wx' });
  }
  const info = check(staging);
  const { cpSync } = await import('node:fs');
  mkdirSync(out, { recursive: true });
  cpSync(resolve(staging, 'rhwp'), resolve(out, 'rhwp'), { recursive: true, errorOnExist: true, force: false });
  check(out);
  console.log(JSON.stringify({ imported: out, sha256: expected, diagnostic: info.diagnostic,
    files_verified: Object.keys(receipt.files).length, preserved_staging: staging }));
} else throw Error('Usage: developer-engine-kit.mjs check --public DIR | pack --public DIR --out ZIP | unpack --archive ZIP [--sha256 SHA] --out DIR');
