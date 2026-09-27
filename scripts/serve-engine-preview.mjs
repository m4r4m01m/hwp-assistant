// Separate local preview; never overwrites or restarts the default public engine.
import { createServer } from '../frontend/node_modules/vite/dist/node/index.js';
import { parseArgs } from 'node:util';
import { resolve } from 'node:path';
import { readFileSync } from 'node:fs';
const { values } = parseArgs({ options: {
  public: { type: 'string', default: '.cache/hancom-empty-fill-v2-2026-09-27/public' },
  port: { type: 'string', default: '15176' },
  api: { type: 'string', default: 'http://127.0.0.1:18001' },
  help: { type: 'boolean', default: false },
} });
if (values.help) {
  console.log('node scripts/serve-engine-preview.mjs [--public <engine public directory>] [--port 15176] [--api http://127.0.0.1:18001]');
  process.exit(0);
}
const api = new URL(values.api), port = Number(values.port);
if (!['127.0.0.1', 'localhost'].includes(api.hostname) || api.protocol !== 'http:' ||
    !Number.isInteger(port) || port < 1024 || port > 65535) throw Error('Use a loopback API and a valid local port');
const root = resolve(import.meta.dirname, '..'), publicDir = resolve(root, values.public);
const build = JSON.parse(readFileSync(resolve(publicDir, 'rhwp/build-info.json'), 'utf8'));
if (!build.coreBuild?.wasm_sha256) throw Error('A recorded patched engine build is required');
const server = await createServer({ configFile: false, root: resolve(root, 'frontend'), publicDir,
  cacheDir: resolve(publicDir, `../vite-cache-${port}`),
  server: { host: '127.0.0.1', port, strictPort: true,
    proxy: { '/api': { target: api.origin, changeOrigin: false } } } });
await server.listen(); server.printUrls();
