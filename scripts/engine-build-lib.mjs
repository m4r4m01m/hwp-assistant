import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';

export const root = resolve(import.meta.dirname, '..');
export const lock = JSON.parse(readFileSync(resolve(root, 'scripts/engine-lock.json'), 'utf8'));
export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
export const hashFile = file => sha256(readFileSync(file));
export const normalizeNewlines = value => value.replaceAll('\r\n', '\n');
// Binaryen appends its exact Git tag when a release was built from a checkout.
export const isPinnedBinaryen = value => [
  `wasm-opt version ${lock.binaryen}`,
  `wasm-opt version ${lock.binaryen} (version_${lock.binaryen})`,
].includes(value);

// Never checkout/reset an existing vendor directory: it can contain local work.
export function ensureSource(repo = resolve(root, '.cache/rhwp')) {
  if (!existsSync(resolve(repo, '.git'))) {
    if (existsSync(repo) && readdirSync(repo).length)
      throw Error('Preserving non-empty vendor directory without Git metadata: ' + repo);
    mkdirSync(repo, { recursive: true });
    const git = args => execFileSync('git', args, { cwd: repo, stdio: 'inherit' });
    git(['init']);
    git(['remote', 'add', 'origin', 'https://github.com/edwardkim/rhwp.git']);
    git(['sparse-checkout', 'init', '--cone']);
    git(['sparse-checkout', 'set', 'rhwp-studio', 'npm', 'assets/fonts']);
    git(['fetch', '--depth=1', '--filter=blob:none', 'origin', lock.commit]);
    git(['checkout', '--detach', lock.commit]);
  }
  const head = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repo, encoding: 'utf8' }).trim();
  if (head !== lock.commit) throw Error('Preserving vendor checkout at a different commit: ' + head);
  const changed = execFileSync('git', ['diff', 'HEAD', '--name-only'], { cwd: repo, encoding: 'utf8' })
    .trim().split(/\r?\n/).filter(Boolean);
  if (changed.some(name => !['rhwp-studio/src/main.ts', 'rhwp-studio/public/fonts'].includes(name)))
    throw Error('Preserving unexpected vendor source changes: ' + changed.join(', '));
  return repo;
}

export function validateCore(coreDir, { diagnostic = false } = {}) {
  const metadataFile = resolve(coreDir, 'assistant-core-build.json');
  if (!existsSync(metadataFile))
    throw Error('Patched core is missing. Run npm run engine:core with Docker, or use --diagnostic --core-dir <verified native pkg> --out <separate directory>. No npm-core fallback.');
  const info = JSON.parse(readFileSync(metadataFile, 'utf8'));
  validateProvenance(info, { diagnostic });
  if (info.wasm_sha256 !== hashFile(resolve(coreDir, 'rhwp_bg.wasm')) ||
      info.binding_sha256 !== hashFile(resolve(coreDir, 'rhwp.js')))
    throw Error('Core artifact hash mismatch');
  return info;
}

export function validateProvenance(info, { diagnostic = false } = {}) {
  for (const key of ['commit', 'patch', 'patch_sha256', 'rust', 'wasm_bindgen'])
    if (info[key] !== lock[key]) throw Error('Core provenance mismatch: ' + key);
  if (hashFile(resolve(root, `scripts/patches/${lock.patch}.patch`)) !== lock.patch_sha256)
    throw Error('Checked-in patch differs from engine-lock.json');
  for (const [name, hash] of Object.entries(lock.source_sha256))
    if (info.source_sha256?.[name] !== hash) throw Error('Core source mismatch: ' + name);
  if (info.profile !== 'release') throw Error('Cargo release profile is required');
  if (info.wasm_opt === false) {
    if (!diagnostic) throw Error('Native diagnostic core is not a deployment build; use --diagnostic and a separate --out explicitly.');
  } else {
    const opt = info.optimization;
    if (info.wasm_opt !== true || info.build_environment !== 'docker-linux' || info.wasm_pack !== lock.wasm_pack ||
        !isPinnedBinaryen(opt?.version) || opt?.output_sha256 !== info.wasm_sha256 ||
        !/^[a-f0-9]{64}$/.test(opt?.input_sha256 || '') || !/^[a-f0-9]{64}$/.test(opt?.tool_sha256 || '') ||
        JSON.stringify(opt?.arguments) !== JSON.stringify(['-O']))
      throw Error('Missing or inconsistent Docker optimization receipt');
  }
  return info;
}
