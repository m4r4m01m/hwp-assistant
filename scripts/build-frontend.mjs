import { build } from '../frontend/node_modules/vite/dist/node/index.js';
import { parseArgs } from 'node:util';
import { resolve } from 'node:path';
import { root } from './engine-build-lib.mjs';
import { checkBundle } from './check-engine-bundle.mjs';
import { comparisonCorePlugin } from './comparison-core.mjs';
import { normalizePublicBase } from './public-base.mjs';

const { values } = parseArgs({ options: {
  public: { type: 'string', default: 'frontend/public' },
  out: { type: 'string', default: 'frontend/dist' },
  diagnostic: { type: 'boolean', default: false },
} });
const publicDir = resolve(root, values.public), outDir = resolve(root, values.out);
if (values.diagnostic && outDir === resolve(root, 'frontend/dist'))
  throw Error('Use a separate --out for diagnostic frontend builds; preserving deployment dist.');
const policy = { deployment: !values.diagnostic };
// This programmatic build deliberately skips vite.config.ts. Pass the public
// base here too: HTML, import.meta.env.BASE_URL and the comparison manifest must agree.
const base = normalizePublicBase(process.env.HWP_PUBLIC_BASE_PATH ?? '/');
checkBundle(resolve(publicDir, 'rhwp'), policy);
await build({ root: resolve(root, 'frontend'), configFile: false, publicDir, base,
  plugins: [comparisonCorePlugin({ publicDir, diagnostic: values.diagnostic })],
  build: { outDir, emptyOutDir: false, rollupOptions: { input: {
    app: resolve(root, 'frontend/index.html'), comparison: resolve(root, 'frontend/compare-renderer.html'),
  } } } });
checkBundle(resolve(outDir, 'rhwp'), policy);
console.log('Frontend + verified engine ready:', outDir);
