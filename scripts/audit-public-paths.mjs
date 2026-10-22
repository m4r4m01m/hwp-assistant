// Guard against introducing root-only browser URLs into a subpath deployment.
// A source audit is supplementary, not a replacement for the browser smoke test.
import { readdirSync, readFileSync } from 'node:fs';
import { resolve, relative } from 'node:path';
const root = resolve(import.meta.dirname, '../frontend/src');
const bad = [];
function walk(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) { walk(path); continue; }
    if (!/\.(?:ts|tsx|css)$/.test(entry.name)) continue;
    const lines = readFileSync(path, 'utf8').split('\n');
    lines.forEach((line, index) => {
      if (/["'`]\/(?:api(?:\/|["'`])|rhwp\/|assets\/|comparison-(?:core|engine)\.|compare-renderer\.html)/.test(line))
        bad.push(`${relative(root, path)}:${index + 1}: use publicPath() for browser resource URLs`);
    });
  }
}
walk(root);
if (bad.length) throw Error(bad.join('\n'));
console.log('Browser source URL audit passed. Runtime URLs still require the deployment browser test.');
