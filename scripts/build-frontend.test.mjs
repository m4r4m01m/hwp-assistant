// Runs the real CLI and Vite against a tiny, isolated frontend. The engine bytes
// are synthetic diagnostic fixtures, never executed or published as a release.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync,
  rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { root, lock, hashFile, sha256 } from './engine-build-lib.mjs';
import { controllerReceipt } from './studio-controller-patch.mjs';

function fixture(t) {
  const directory = mkdtempSync(resolve(tmpdir(), 'hwp-frontend-base-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const scripts = resolve(directory, 'scripts'), frontend = resolve(directory, 'frontend');
  mkdirSync(scripts, { recursive: true });
  mkdirSync(resolve(frontend, 'src'), { recursive: true });
  for (const name of ['build-frontend.mjs', 'engine-build-lib.mjs', 'engine-lock.json',
    'check-engine-bundle.mjs', 'studio-controller-patch.mjs', 'comparison-core.mjs', 'public-base.mjs', 'precompress-static.mjs'])
    cpSync(resolve(root, 'scripts', name), resolve(scripts, name));
  cpSync(resolve(root, 'scripts/patches'), resolve(scripts, 'patches'), { recursive: true });
  symlinkSync(resolve(root, 'frontend/node_modules'), resolve(frontend, 'node_modules'), 'junction');
  writeFileSync(resolve(frontend, 'package.json'), '{"type":"module"}\n');
  // Changing the CLI to load a different config is not the fix for this regression.
  writeFileSync(resolve(frontend, 'vite.config.ts'), 'throw Error("CLI must use explicit config");\n');
  const html = '<!doctype html><html><body><div id="root"></div>' +
    '<script type="module" src="/src/app.js"></script></body></html>';
  for (const name of ['index.html', 'compare-renderer.html'])
    writeFileSync(resolve(frontend, name), html);
  writeFileSync(resolve(frontend, 'src/app.js'),
    'import "./app.css"; window.__fixtureBase = import.meta.env.BASE_URL;\n');
  writeFileSync(resolve(frontend, 'src/app.css'), 'body { margin: 0; }\n');
  const publicDir = resolve(directory, 'fixture-public'), engine = resolve(publicDir, 'rhwp');
  mkdirSync(resolve(engine, 'assets'), { recursive: true });
  const files = { 'index.html': '<html>Diagnostic fixture only</html>',
    'assets/rhwp_bg-fixture.wasm': 'synthetic diagnostic artifact; never executed' };
  for (const [name, value] of Object.entries(files)) writeFileSync(resolve(engine, name), value);
  writeFileSync(resolve(engine, 'build-info.json'), JSON.stringify({
    commit: lock.commit, diagnostic: true, controller: controllerReceipt(),
    bundle: Object.fromEntries(Object.entries(files).map(([name, value]) => [name, sha256(value)])),
    coreBuild: { ...lock, profile: 'release', wasm_opt: false,
      wasm_sha256: sha256(files['assets/rhwp_bg-fixture.wasm']),
      binding_sha256: hashFile(resolve(root, 'frontend/node_modules/@rhwp/core/rhwp.js')) },
  }));
  const out = resolve(directory, 'diagnostic-output');
  function run(base, diagnostic = true) {
    const env = { ...process.env };
    if (base === undefined) delete env.HWP_PUBLIC_BASE_PATH;
    else env.HWP_PUBLIC_BASE_PATH = base;
    return spawnSync(process.execPath, [resolve(scripts, 'build-frontend.mjs'),
      '--public', publicDir, '--out', out, ...(diagnostic ? ['--diagnostic'] : [])],
    { cwd: directory, env, encoding: 'utf8', timeout: 60000, maxBuffer: 2 * 1024 * 1024 });
  }
  return { run, out, engine };
}

for (const [input, expected] of [[undefined, '/'], ['/hwp-assistant/', '/hwp-assistant/'],
  ['/demo/hwp', '/demo/hwp/']]) {
  test(`actual frontend CLI emits consistent base ${expected}`, t => {
    const { run, out } = fixture(t);
    const result = run(input);
    assert.equal(result.status, 0, `${result.error || ''}\n${result.stdout}\n${result.stderr}`);
    for (const name of ['index.html', 'compare-renderer.html']) {
      const html = readFileSync(resolve(out, name), 'utf8');
      const urls = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map(match => match[1]);
      assert.ok(urls.some(url => url.endsWith('.js')), `${name}: missing JS`);
      assert.ok(urls.some(url => url.endsWith('.css')), `${name}: missing CSS`);
      for (const url of urls) {
        assert.ok(url.startsWith(expected + 'assets/'), `${name}: escaped public base: ${url}`);
        assert.ok(existsSync(resolve(out, url.slice(expected.length))), `Missing asset ${url}`);
      }
    }
    const manifest = JSON.parse(readFileSync(resolve(out, 'comparison-engine.json'), 'utf8'));
    assert.equal(manifest.wasm, expected + 'rhwp/assets/rhwp_bg-fixture.wasm');
    const javascript = readdirSync(resolve(out, 'assets')).filter(name => name.endsWith('.js'))
      .map(name => readFileSync(resolve(out, 'assets', name), 'utf8')).join('\n');
    const injected = javascript.match(/__fixtureBase\s*=\s*["']([^"']+)["']/);
    assert.ok(injected, 'The runtime BASE_URL probe was not emitted');
    assert.equal(injected[1], expected);
  });
}

test('actual frontend CLI rejects a non-path public base before publishing', t => {
  const { run, out } = fixture(t);
  const result = run('https://untrusted.invalid/');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /HWP_PUBLIC_BASE_PATH/);
  assert.equal(existsSync(out), false);
});

test('actual frontend CLI still refuses a diagnostic engine as a deployment build', t => {
  const { run, out } = fixture(t);
  const result = run('/hwp-assistant/', false);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Diagnostic bundle cannot be used as deployment evidence/);
  assert.equal(existsSync(out), false);
});

test('actual frontend CLI still refuses altered engine bytes', t => {
  const { run, out, engine } = fixture(t);
  writeFileSync(resolve(engine, 'assets/rhwp_bg-fixture.wasm'), 'tampered');
  const result = run('/hwp-assistant/');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Bundle file\/hash mismatch/);
  assert.equal(existsSync(out), false);
});
