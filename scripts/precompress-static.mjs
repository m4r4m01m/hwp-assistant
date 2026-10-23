import { readdirSync, readFileSync, writeFileSync, renameSync, unlinkSync, existsSync } from 'node:fs';
import { extname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';

const compressible = new Set(['.js', '.mjs', '.css', '.wasm', '.json', '.html', '.svg', '.txt', '.xml']);

export function precompressStatic(directory) {
  let files = 0, originalBytes = 0, compressedBytes = 0;
  const visit = (folder) => {
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
      const path = join(folder, entry.name);
      if (entry.isDirectory()) { visit(path); continue; }
      if (!entry.isFile()) continue; // Never follow a link outside the build.
      if (entry.name.endsWith('.gz')) {
        const original = path.slice(0, -3);
        if (!existsSync(original) || !compressible.has(extname(original))) unlinkSync(path);
        continue;
      }
      if (!compressible.has(extname(path))) continue;
      const source = readFileSync(path);
      const zipped = source.length >= 1024 ? gzipSync(source, { level: 9 }) : null;
      const destination = path + '.gz';
      if (!zipped || zipped.length > source.length * 0.95 || source.length - zipped.length < 100) {
        if (existsSync(destination)) unlinkSync(destination);
        continue;
      }
      const temporary = destination + '.tmp';
      writeFileSync(temporary, zipped);
      renameSync(temporary, destination);
      files++; originalBytes += source.length; compressedBytes += zipped.length;
    }
  };
  visit(resolve(directory));
  return { files, originalBytes, compressedBytes };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  if (!process.argv[2]) throw Error('Usage: node scripts/precompress-static.mjs <built-directory>');
  console.log(precompressStatic(process.argv[2]));
}
