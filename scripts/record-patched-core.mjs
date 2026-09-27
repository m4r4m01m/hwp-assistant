import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { parseArgs } from 'node:util';
import { root, lock, validateProvenance } from './engine-build-lib.mjs';
const { values } = parseArgs({ options: {
  source: { type: 'string', default: '.cache/rhwp-core-fix' },
  'optimization-receipt': { type: 'string' },
} });
const source = resolve(root, values.source);
const hash = file => createHash('sha256').update(readFileSync(file)).digest('hex');
const commit = lock.commit;
const cargoLock = execFileSync('git', ['show', `${commit}:Cargo.lock`], { cwd: resolve(root, '.cache/rhwp') });
const normalize = bytes => bytes.toString('utf8').replaceAll('\r\n', '\n');
if (normalize(readFileSync(resolve(source, 'Cargo.lock'))) !== normalize(cargoLock))
  throw Error('Pinned Cargo.lock changed');
const optimization = values['optimization-receipt']
  ? JSON.parse(readFileSync(resolve(values['optimization-receipt']), 'utf8')) : undefined;
if (optimization && (process.platform !== 'linux' || process.env.HWP_CORE_BUILD_ENV !== 'docker-linux'))
  throw Error('Optimized release recording belongs to the Docker Linux build');
const metadata = {
  commit, patch: lock.patch, rust: lock.rust, wasm_bindgen: lock.wasm_bindgen,
  profile: 'release', wasm_opt: !!optimization,
  purpose: optimization ? 'Docker optimized core; container/runtime validation is recorded separately' : 'native diagnostic build; deployment optimization not verified',
  ...(optimization ? { build_environment: 'docker-linux', wasm_pack: lock.wasm_pack, optimization } : {}),
  patch_sha256: hash(resolve(root, `scripts/patches/${lock.patch}.patch`)),
  source_sha256: Object.fromEntries(['header.rs', 'shape.rs', 'char_shapes.rs'].map(name => [name, hash(resolve(source, 'src/serializer/hwpx', name))])),
  lock_sha256: hash(resolve(source, 'Cargo.lock')),
  wasm_sha256: hash(resolve(source, 'pkg/rhwp_bg.wasm')),
  binding_sha256: hash(resolve(source, 'pkg/rhwp.js')),
};
validateProvenance(metadata, { diagnostic: !optimization });
writeFileSync(resolve(source, 'pkg/package.json'), JSON.stringify({ name: '@rhwp/core', version: lock.version, type: 'module' }, null, 2));
writeFileSync(resolve(source, 'pkg/assistant-core-build.json'), JSON.stringify(metadata, null, 2) + '\n');
console.log(JSON.stringify(metadata, null, 2));
