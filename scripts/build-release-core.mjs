// Container build only. No host documents, authentication or database are needed.
import { execFileSync } from 'node:child_process';
import { existsSync, copyFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { root, lock, hashFile, isPinnedBinaryen } from './engine-build-lib.mjs';

if (process.platform !== 'linux' || process.env.HWP_CORE_BUILD_ENV !== 'docker-linux')
  throw Error('Use npm run engine:core (Docker). Windows native builds remain diagnostic.');
const source = resolve(root, '.cache/rhwp-core-release');
const run = (cmd, args, cwd = root) => execFileSync(cmd, args, { cwd, stdio: 'inherit' });
const version = cmd => execFileSync(cmd, ['--version'], { encoding: 'utf8' }).trim();
if (!version('rustc').startsWith(`rustc ${lock.rust} `) ||
    version('wasm-pack') !== `wasm-pack ${lock.wasm_pack}` ||
    version('wasm-bindgen') !== `wasm-bindgen ${lock.wasm_bindgen}` ||
    !isPinnedBinaryen(version('wasm-opt')))
  throw Error('Pinned Rust/WASM tool versions are required');
if (existsSync(resolve(source, 'pkg')))
  throw Error('Use a fresh container build directory; preserving an existing package.');
run('python3', ['scripts/prepare-patched-core.py', '--source', '.cache/rhwp-core-release']);
// The upstream wrapper locks both cargo metadata and build. Optimization is explicit
// so a skipped wasm-opt can never be recorded as an optimized artifact.
run('sh', ['scripts/wasm-pack-locked.sh', '--target', 'web', '--out-dir', 'pkg', '--release', '--no-opt'], source);
const wasm = resolve(source, 'pkg/rhwp_bg.wasm');
const before = resolve(source, 'rhwp_bg.unoptimized.wasm');
copyFileSync(wasm, before);
const opt = execFileSync('which', ['wasm-opt'], { encoding: 'utf8' }).trim();
run(opt, [before, '-o', wasm, '-O']);
const receipt = {
  version: version(opt), arguments: ['-O'], tool_sha256: hashFile(opt),
  input_sha256: hashFile(before), output_sha256: hashFile(wasm),
};
const receiptFile = resolve(source, 'optimization.json');
writeFileSync(receiptFile, JSON.stringify(receipt, null, 2) + '\n', { flag: 'wx' });
// wasm-pack emits an ESM entry but Node needs the package type for real WASM tests.
run(process.execPath, ['scripts/record-patched-core.mjs', '--source', source, '--optimization-receipt', receiptFile]);
run(process.execPath, ['scripts/test-engine-fill.mjs', resolve(source, 'pkg')]);
run(process.execPath, ['scripts/test-engine-selection.mjs', resolve(source, 'pkg')]);
