// Read-only byte comparison of an isolated HTTP server with its intended bundle.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { parseArgs } from 'node:util';
const { values } = parseArgs({ options: {
  url: { type: 'string' }, root: { type: 'string' }, out: { type: 'string' }, built: { type: 'boolean', default: false },
} });
const url = new URL(values.url);
if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1') throw Error('Only isolated loopback HTTP is allowed');
const root = resolve(values.root);
const info = JSON.parse(readFileSync(resolve(root, 'rhwp/build-info.json'), 'utf8'));
const files = ['rhwp/build-info.json', ...Object.keys(info.bundle).map(p => `rhwp/${p}`)];
if (values.built) {
  const index = readFileSync(resolve(root, 'index.html'), 'utf8');
  files.push('index.html', ...[...index.matchAll(/(?:src|href)="\/(assets\/[^"?#]+)"/g)].map(m => m[1]));
  if (!files.some(p => p.startsWith('assets/') && p.endsWith('.js'))) throw Error('Built application JS was not found');
}
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const evidence = [];
for (const path of files) {
  const expected = hash(readFileSync(resolve(root, path)));
  const response = await fetch(new URL(path, url), { signal: AbortSignal.timeout(10000) });
  if (!response.ok) throw Error(`HTTP ${response.status}: ${path}`);
  const actual = hash(Buffer.from(await response.arrayBuffer()));
  if (actual !== expected) throw Error(`Served bytes differ: ${path}`);
  evidence.push({ path, sha256: actual });
}
writeFileSync(values.out, JSON.stringify({ mode: values.built ? 'fresh diagnostic app bundle' : 'source app, copied engine', url: url.origin, files: evidence }, null, 2));
console.log(`Served-byte verification: ${files.length} files match (${values.built ? 'built' : 'source'})`);
