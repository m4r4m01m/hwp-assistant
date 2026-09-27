// Actual product UI + fixed synthetic table. No model call or XML mutation.
import { chromium, expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import { liveTools } from './verify-codex-human-tools.mjs';
import { parseArgs } from 'node:util';
import { resolve } from 'node:path';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { unzipSync, strFromU8 } from '../frontend/node_modules/fflate/esm/index.mjs';
const { values } = parseArgs({ options: {
  url: { type: 'string', default: 'http://127.0.0.1:15178' },
  out: { type: 'string', default: '.cache/hancom-table-2026-09-27' },
  input: { type: 'string' },
} });
if (!['127.0.0.1', 'localhost'].includes(new URL(values.url).hostname)) throw Error('Loopback only');
const out = resolve(values.out), phase = values.input ? 'import' : 'create';
await mkdir(out, { recursive: true });
const proofFile = resolve(out, `${phase}-proof.json`);
const proof = { kind: 'REAL product UI, fixed synthetic table; NO AI', started_at: new Date().toISOString(),
  url: values.url, stages: {}, page_errors: [], csp_violations: [], external_requests: [], wasm_responses: [] };
await writeFile(proofFile, JSON.stringify(proof), { flag: 'wx' });
const record = () => writeFile(proofFile, JSON.stringify(proof, null, 2) + '\n');
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const browser = await chromium.launch({ channel: 'chrome', headless: false, args: ['--start-maximized'] });
const context = await browser.newContext({ baseURL: values.url, viewport: null });
const page = await context.newPage();
page.on('pageerror', e => proof.page_errors.push(e.message));
page.on('request', r => { if (/^https?:/.test(r.url()) && new URL(r.url()).origin !== new URL(values.url).origin) proof.external_requests.push(new URL(r.url()).origin); });
const wasmReads = [];
page.on('response', r => { if (/\/rhwp\/assets\/rhwp_bg-[^/]+\.wasm$/.test(new URL(r.url()).pathname))
  wasmReads.push(r.body().then(b => proof.wasm_responses.push({ path: new URL(r.url()).pathname, sha256: hash(b) }))); });
await page.exposeFunction('recordCspViolation', data => proof.csp_violations.push(data));
await page.addInitScript(() => document.addEventListener('securitypolicyviolation', e =>
  window.recordCspViolation({ directive: e.violatedDirective, blocked_uri: e.blockedURI })));
const get = async url => { const r = await context.request.get(url); expect(r.ok()).toBe(true); return r.json(); };
function inspect(bytes) {
  const files = unzipSync(bytes);
  const section = strFromU8(files['Contents/section0.xml']), header = strFromU8(files['Contents/header.xml']);
  expect(section).toContain('표 서식 보존 시험');
  expect(section).toContain('보존 문단: 표 밖 문장을 유지합니다.');
  for (const cell of ['항목', '값', '일정', '금요일']) expect(section).toContain(cell);
  expect(section).toMatch(/<hp:tbl\b[^>]*rowCnt="2"[^>]*colCnt="2"/);
  expect(header).toContain(':bold'); expect(header).toContain('CENTER');
  expect(header).not.toContain('shadeColor="none"');
  return { sha256: hash(bytes), size: bytes.length, table_count: (section.match(/<hp:tbl\b/g) || []).length,
    cell_count: (section.match(/<hp:tc\b/g) || []).length, has_hancom_cell_edit: section.includes('금요일 한컴 확인') };
}
try {
  await context.route('**/api/threads/*/rewrite-tasks', route => route.abort('blockedbyclient'));
  await page.goto('/'); await page.bringToFront();
  if ((await get('/api/auth/status')).authentication_required) {
    await page.getByLabel('인증 토큰').fill(process.env.HWP_TEST_APP_TOKEN || 'alice-' + 'a'.repeat(32));
    const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/auth/login' && r.request().method() === 'POST');
    await page.getByRole('button', { name: '로그인', exact: true }).click(); expect((await response).ok()).toBe(true);
  }
  proof.engine = await get('/rhwp/build-info.json');
  let document;
  if (values.input) {
    const input = await readFile(resolve(values.input));
    proof.input = inspect(input); expect(proof.input.has_hancom_cell_edit).toBe(true);
    const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/documents' && r.request().method() === 'POST');
    await page.getByLabel('HWPX 업로드').setInputFiles(resolve(values.input));
    const uploaded = await response; expect(uploaded.ok()).toBe(true); document = await uploaded.json();
    const url = `/api/documents/${document.id}/versions/${document.current_version}/file`;
    await page.getByRole('row').filter({ has: page.locator(`a[href="${url}"]`) }).locator('td').first().getByRole('button').click();
    await expect(page.getByRole('status')).toHaveText('저장 완료', { timeout: 90000 });
  } else {
    await page.getByRole('button', { name: '+ 새 문서', exact: true }).click();
    await expect(page.getByRole('status')).toHaveText('저장되지 않은 변경 있음', { timeout: 90000 });
    const frame = page.frames().find(f => f.url().includes('/rhwp/'));
    await frame.locator('#scroll-container canvas').first().click({ position: { x: 160, y: 150 }, force: true });
    await page.keyboard.press('Control+End'); await page.keyboard.press('Enter');
    await page.keyboard.insertText('표 서식 보존 시험');
    await page.keyboard.press('Home'); await page.keyboard.press('Shift+End'); await page.keyboard.press('Control+b');
    await frame.getByRole('button', { name: '가운데 정렬', exact: true }).click();
    await page.keyboard.press('ArrowRight'); await page.keyboard.press('End'); await page.keyboard.press('Enter');
    await page.keyboard.press('Control+b');
    await frame.getByRole('button', { name: '왼쪽 정렬', exact: true }).click();
    await page.keyboard.insertText('보존 문단: 표 밖 문장을 유지합니다.'); await page.keyboard.press('Enter');
    await frame.locator('button[data-cmd="table:create"]').click();
    const grid = frame.locator('[data-row="1"][data-col="1"]'); await grid.hover(); await grid.click();
    for (const [index, text] of ['항목', '값', '일정', '금요일'].entries()) {
      if (index) await page.keyboard.press('Tab'); await page.keyboard.insertText(text);
    }
    await page.getByRole('button', { name: '저장', exact: true }).click();
    await expect(page.getByRole('status')).toHaveText('저장 완료');
    const url = await page.getByRole('link', { name: '저장본 다운로드' }).getAttribute('href'); document = await get(url.split('/versions/')[0]);
  }
  proof.document = document;
  const url = `/api/documents/${document.id}/versions/${document.current_version}/file`;
  const downloaded = await context.request.get(url); expect(downloaded.ok()).toBe(true);
  const bytes = await downloaded.body(); proof.saved = inspect(bytes);
  if (values.input) expect(proof.saved.sha256).toBe(proof.input.sha256);
  await writeFile(resolve(out, `${phase}-download.hwpx`), bytes, { flag: 'wx' });
  proof.versions = await get(`/api/documents/${document.id}/versions`); expect(proof.versions).toHaveLength(1);
  proof.stages.saved_download = 'passed'; await record();
  await page.getByRole('button', { name: '← 문서 목록', exact: true }).click();
  await page.getByRole('row').filter({ has: page.locator(`a[href="${url}"]`) }).locator('td').first().getByRole('button').click();
  await expect(page.getByRole('status')).toHaveText('저장 완료');
  const h = liveTools({ browser, context, page });
  proof.reopened = inspect(await h.exported());
  if (values.input) expect(proof.reopened.has_hancom_cell_edit).toBe(true);
  await Promise.all(wasmReads);
  expect(proof.wasm_responses.length).toBeGreaterThan(0);
  expect(proof.wasm_responses.every(r => r.sha256 === proof.engine.coreBuild.wasm_sha256)).toBe(true);
  expect(proof.page_errors).toEqual([]); expect(proof.csp_violations).toEqual([]); expect(proof.external_requests).toEqual([]);
  await page.screenshot({ path: resolve(out, `${phase}-reopened.png`) });
  proof.stages.web_reopen = 'passed'; proof.completed_at = new Date().toISOString(); await record();
  console.log(JSON.stringify({ document_id: document.id, version_id: document.current_version, saved: proof.saved, stages: proof.stages }));
} catch (error) { proof.failure = String(error); await record(); console.error(String(error)); process.exitCode = 1; }
console.log('합성 표 시험 창을 유지합니다. 실제 AI 호출 시험이 아닙니다.');
await new Promise(resolve => browser.once('disconnected', resolve));
