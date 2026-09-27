import { build } from '../frontend/node_modules/vite/dist/node/index.js';
import { parseArgs } from 'node:util';
import { resolve } from 'node:path';
import { root } from './engine-build-lib.mjs';
import { checkBundle } from './check-engine-bundle.mjs';

const { values } = parseArgs({ options: {
  public: { type: 'string', default: 'frontend/public' },
  out: { type: 'string', default: 'frontend/dist' },
  diagnostic: { type: 'boolean', default: false },
} });
const publicDir = resolve(root, values.public), outDir = resolve(root, values.out);
if (values.diagnostic && outDir === resolve(root, 'frontend/dist'))
  throw Error('Use a separate --out for diagnostic frontend builds; preserving deployment dist.');
const policy = { deployment: !values.diagnostic };
checkBundle(resolve(publicDir, 'rhwp'), policy);
await build({ root: resolve(root, 'frontend'), configFile: false, publicDir,
  build: { outDir, emptyOutDir: false } });
checkBundle(resolve(outDir, 'rhwp'), policy);
console.log('Frontend + verified engine ready:', outDir);
