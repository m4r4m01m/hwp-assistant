import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { root, validateCore } from './engine-build-lib.mjs';

const destination = resolve(root, '.cache/rhwp-core-release/pkg');
if (existsSync(destination)) {
  validateCore(destination);
  console.log('Existing verified release core retained:', destination);
} else {
  try {
    const platform = execFileSync('docker', ['info', '--format', '{{.OSType}}'], { encoding: 'utf8', timeout: 15000 }).trim();
    if (platform !== 'linux') throw Error('Linux containers are required');
  }
  catch { throw Error('Docker Linux engine is unavailable. Existing servers/artifacts were left untouched. See docs/engine-empty-fill-fix.md.'); }
  execFileSync('docker', ['build', '--file', 'frontend/Dockerfile', '--target', 'core-export',
    '--output', `type=local,dest=${destination}`, '.'], { cwd: root, stdio: 'inherit' });
  validateCore(destination);
  console.log('Verified release core exported:', destination);
}
