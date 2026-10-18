// Serves a built app with the configured nginx response headers. This is not nginx.
import { preview } from '../frontend/node_modules/vite/dist/node/index.js';
import { parseArgs } from 'node:util';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { root } from './engine-build-lib.mjs';
import { checkBundle } from './check-engine-bundle.mjs';

const { values } = parseArgs({ options: {
  dist: { type: 'string', default: 'frontend/dist' },
  port: { type: 'string', default: '15178' },
  api: { type: 'string', default: 'http://127.0.0.1:18001' },
  diagnostic: { type: 'boolean', default: false },
} });
const api = new URL(values.api), port = Number(values.port), outDir = resolve(root, values.dist);
if (!['127.0.0.1', 'localhost'].includes(api.hostname) || api.protocol !== 'http:' ||
    !Number.isInteger(port) || port < 1024 || port > 65535)
  throw Error('Use a loopback API and a valid local port');
const info = checkBundle(resolve(outDir, 'rhwp'), { deployment: !values.diagnostic });
const nginx = readFileSync(resolve(root, 'frontend/nginx.conf'), 'utf8');
const headers = Object.fromEntries([...nginx.matchAll(/add_header\s+([\w-]+)\s+(?:"([^"]+)"|([^;\s]+))\s+always;/g)]
  .map(match => [match[1], match[2] || match[3]]));
if (!headers['Content-Security-Policy']) throw Error('Could not read the actual nginx CSP');
// Revalidate the local app shell after a new diagnostic bundle is installed.
// Keep all existing CSP/auth headers; a normal refresh must see the new UI.
headers['Cache-Control'] = 'no-cache';
const server = await preview({ configFile: false, root: resolve(root, 'frontend'), build: { outDir },
  preview: { host: '127.0.0.1', port, strictPort: true, headers,
    proxy: { '/api': { target: api.origin, changeOrigin: false } } } });
server.printUrls();
console.log(JSON.stringify({ mode: 'built frontend, nginx headers, Vite preview (not nginx)',
  diagnostic: info.diagnostic, wasm_sha256: info.coreBuild.wasm_sha256 }));
