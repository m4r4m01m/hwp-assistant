// The comparison renderer uses the same patched WASM as Studio, with the exact
// binding recorded in its receipt. The npm WASM is never copied or loaded.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { checkBundle } from './check-engine-bundle.mjs';
import { root, hashFile } from './engine-build-lib.mjs';

export function comparisonAssets(publicDir, diagnostic = false) {
  const info = checkBundle(resolve(publicDir, 'rhwp'), { deployment: !diagnostic });
  const binding = resolve(root, 'frontend/node_modules/@rhwp/core/rhwp.js');
  if (hashFile(binding) !== info.coreBuild.binding_sha256)
    throw Error('Comparison binding does not match the verified Studio core. Rebuild a matching engine; no npm WASM fallback.');
  const wasm = Object.keys(info.bundle).find(name => /^assets\/rhwp_bg-.*\.wasm$/.test(name));
  return {
    'comparison-core.js': readFileSync(binding, 'utf8'),
    'comparison-engine.json': JSON.stringify({ commit: info.commit, wasm: '/rhwp/' + wasm,
      wasm_sha256: info.coreBuild.wasm_sha256, binding_sha256: info.coreBuild.binding_sha256 }),
  };
}

export function comparisonCorePlugin({ publicDir = resolve(root, 'frontend/public'), diagnostic = false } = {}) {
  let build = false;
  return {
    name: 'verified-comparison-core',
    configResolved(config) { build = config.command === 'build'; },
    buildStart() {
      if (!build) return;
      for (const [fileName, source] of Object.entries(comparisonAssets(publicDir, diagnostic)))
        this.emitFile({ type: 'asset', fileName, source });
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const name = (req.url || '').split('?')[0].slice(1);
        if (!['comparison-core.js', 'comparison-engine.json'].includes(name)) return next();
        try {
          const assets = comparisonAssets(publicDir, diagnostic);
          res.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : 'application/json');
          res.setHeader('Cache-Control', 'no-store'); res.end(assets[name]);
        } catch (error) { res.statusCode = 503; res.end(String(error)); }
      });
    },
  };
}
