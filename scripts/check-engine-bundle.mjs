// Verify the files actually served, not just the presence of a source/config path.
import { readFileSync } from 'node:fs';
import { resolve, relative, isAbsolute } from 'node:path';
import { pathToFileURL } from 'node:url';
import { lock, hashFile, validateProvenance } from './engine-build-lib.mjs';
import { validateControllerReceipt } from './studio-controller-patch.mjs';

export function checkBundle(directory, { deployment = true, allowLegacyController = false } = {}) {
  if (deployment && allowLegacyController) throw Error('Legacy controller inspection is limited to explicit diagnostic migration');
  const root = resolve(directory);
  const info = JSON.parse(readFileSync(resolve(root, 'build-info.json'), 'utf8'));
  if (info.commit !== lock.commit || info.coreBuild?.patch_sha256 !== lock.patch_sha256 || !info.bundle)
    throw Error('Missing pinned core/bundle provenance. Run npm run engine:core then npm run engine; see docs/engine-empty-fill-fix.md for a separate diagnostic build.');
  if (deployment && (info.diagnostic !== false || info.coreBuild.wasm_opt !== true))
    throw Error('Diagnostic bundle cannot be used as deployment evidence');
  validateProvenance(info.coreBuild, { diagnostic: !deployment });
  // Only an explicit old-bridge migration may inspect a pre-extension bundle.
  // An existing receipt is always checked, even in that migration path.
  if (info.controller || !allowLegacyController) validateControllerReceipt(info.controller);
  for (const [name, hash] of Object.entries(info.bundle)) {
    const file = resolve(root, name), local = relative(root, file);
    if (local.startsWith('..') || isAbsolute(local) || hashFile(file) !== hash)
      throw Error('Bundle file/hash mismatch: ' + name);
  }
  const wasm = Object.keys(info.bundle).filter(name => /^assets\/rhwp_bg-.*\.wasm$/.test(name));
  if (!info.bundle['index.html'] || wasm.length !== 1 || info.bundle[wasm[0]] !== info.coreBuild.wasm_sha256)
    throw Error('Bundle does not identify one verified core');
  return info;
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const diagnostic = process.argv.includes('--diagnostic');
  const directory = process.argv.slice(2).find(arg => !arg.startsWith('--'));
  if (!directory) throw Error('Usage: node scripts/check-engine-bundle.mjs <rhwp directory> [--diagnostic]');
  const info = checkBundle(directory, { deployment: !diagnostic });
  console.log(JSON.stringify({ kind: diagnostic ? 'diagnostic bundle' : 'deployment bundle',
    wasm_sha256: info.coreBuild.wasm_sha256, files_verified: Object.keys(info.bundle).length }));
}
