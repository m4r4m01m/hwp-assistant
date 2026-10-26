import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, relative, isAbsolute } from 'node:path';
import { once } from 'node:events';

test('desktop preview survives closed launcher stdin and still closes explicitly', { timeout: 15000 }, async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'hwp-preview-service-'));
  const moduleUrl = new URL('./preview-service.mjs', import.meta.url).href;
  const childCode = `
    import { startPreviewService } from ${JSON.stringify(moduleUrl)};
    const previousCI = process.env.CI;
    const server = await startPreviewService({ configFile: false, root: ${JSON.stringify(directory)},
      build: { outDir: '.' }, preview: { host: '127.0.0.1', port: 0, strictPort: true } });
    if (process.env.CI !== previousCI) throw Error('caller environment changed');
    process.send({ ready: true, port: server.httpServer.address().port });
    process.stdin.on('end', () => process.send({ eof: true }));
    process.stdin.resume();
    process.on('message', async message => {
      if (message === 'close') { await server.close(); process.disconnect(); }
    });
  `;
  await writeFile(join(directory, 'index.html'), '<!doctype html><title>HWP preview probe</title>');
  const child = spawn(process.execPath, ['--input-type=module', '-e', childCode], {
    stdio: ['pipe', 'pipe', 'pipe', 'ipc'], windowsHide: true, env: { ...process.env, CI: '' },
  });
  let stderr = '';
  child.stderr.on('data', data => { stderr += data; });
  child.stdout.resume();
  try {
    const [ready] = await once(child, 'message', { signal: t.signal });
    assert.equal(ready.ready, true, stderr);
    const eof = once(child, 'message', { signal: t.signal });
    child.stdin.end();
    assert.equal((await eof)[0].eof, true);
    // The input stream has ended; the next HTTP request must still be served.
    const response = await fetch(`http://127.0.0.1:${ready.port}/`, { signal: t.signal });
    assert.equal(response.status, 200, stderr);
    assert.match(await response.text(), /HWP preview probe/);
    const closed = once(child, 'exit', { signal: t.signal });
    child.send('close');
    assert.equal((await closed)[0], 0, stderr);
  } finally {
    if (child.exitCode === null) {
      const exited = once(child, 'exit');
      child.kill();
      await exited;
    }
    const owned = relative(tmpdir(), directory);
    assert.ok(owned && !owned.startsWith('..') && !isAbsolute(owned));
    await rm(directory, { recursive: true, force: true });
  }
});
