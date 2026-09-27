// Product UI save/reopen for a synthetic fixture. No AI request and no HWPX rewriting.
import { chromium, expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import { liveTools } from './verify-codex-human-tools.mjs';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { createHash } from 'node:crypto';
import { unzipSync, strFromU8 } from '../frontend/node_modules/fflate/esm/index.mjs';
const { values } = parseArgs({ options: {
  url: { type: 'string', default: 'http://127.0.0.1:15175' },
  out: { type: 'string', default: '.cache/hancom-empty-fill-2026-09-27' },
  input: { type: 'string' },
} });
const out = resolve(values.out);
if (!['127.0.0.1', 'localhost'].includes(new URL(values.url).hostname)) throw Error('Loopback only');
await mkdir(out, { recursive: true });
const phase = values.input ? 'import' : 'create';
const proofPath = resolve(out, `${phase}-proof.json`);
const proof = { started_at: new Date().toISOString(), kind: 'REAL product engine; FIXED synthetic text; NO AI',
  stages: {}, url: values.url };
await writeFile(proofPath, JSON.stringify(proof), { flag: 'wx' });
const record = () => writeFile(proofPath, JSON.stringify(proof, null, 2) + '\n');
const hash = b => createHash('sha256').update(b).digest('hex');
const expectedText = ['', '보존 확인: 회의 장소는 제1회의실입니다. 담당 부서는 운영팀입니다.',
  '배경 수정 확인: 채우기 없음인 문단은 흰 종이 위에 읽을 수 있어야 합니다. 추가 편집도 함께 보존합니다.'];
const browser = await chromium.launch({ channel: 'chrome', headless: false, args: ['--start-maximized'] });
const context = await browser.newContext({ baseURL: values.url, viewport: null, acceptDownloads: true });
const page = await context.newPage();
const get = async url => { const r = await context.request.get(url); expect(r.ok()).toBe(true); return r.json(); };
try {
  await context.route('**/api/threads/*/rewrite-tasks', route => route.abort('blockedbyclient'));
  await page.goto('/'); await page.bringToFront();
  if ((await get('/api/auth/status')).authentication_required) {
    await page.getByLabel('인증 토큰').fill(process.env.HWP_TEST_APP_TOKEN || 'alice-' + 'a'.repeat(32));
    const login = page.waitForResponse(r => new URL(r.url()).pathname === '/api/auth/login' && r.request().method() === 'POST');
    await page.getByRole('button', { name: '로그인', exact: true }).click();
    expect((await login).ok()).toBe(true);
  }
  proof.engine = await get('/rhwp/build-info.json');
  expect(proof.engine.coreBuild.patch).toBe('rhwp-0.8.6-empty-fill');
  let document, latestUrl;
  if (values.input) {
    const inputBytes = await readFile(resolve(values.input));
    proof.input_sha256 = hash(inputBytes);
    const upload = page.waitForResponse(r => new URL(r.url()).pathname === '/api/documents' && r.request().method() === 'POST');
    await page.getByLabel('HWPX 업로드').setInputFiles(resolve(values.input));
    const response = await upload; expect(response.ok()).toBe(true); document = await response.json();
    latestUrl = `/api/documents/${document.id}/versions/${document.current_version}/file`;
    await page.getByRole('row').filter({ has: page.locator(`a[href="${latestUrl}"]`) }).locator('td').first().getByRole('button').click();
  } else {
    await page.getByRole('button', { name: '+ 새 문서', exact: true }).click();
    await expect(page.getByRole('status')).toHaveText('저장되지 않은 변경 있음', { timeout: 90000 });
    const h = liveTools({ browser, context, page });
    await h.focusEnd(); await page.keyboard.press('Enter');
    await page.keyboard.insertText('보존 확인: 회의 장소는 제1회의실입니다. 담당 부서는 운영팀입니다.');
    await page.keyboard.press('Enter');
    await page.keyboard.insertText('배경 수정 확인: 채우기 없음인 문단은 흰 종이 위에 읽을 수 있어야 합니다.');
    await page.getByRole('button', { name: '저장', exact: true }).click();
    await expect(page.getByRole('status')).toHaveText('저장 완료');
    latestUrl = await page.getByRole('link', { name: '저장본 다운로드' }).getAttribute('href');
    document = await get(latestUrl.split('/versions/')[0]);
    proof.initial_version = document.current_version;
    const initial = await context.request.get(latestUrl); expect(initial.ok()).toBe(true);
    const initialBytes = await initial.body(); proof.initial_sha256 = hash(initialBytes);
    proof.initial_manifest = await h.inspect(initialBytes);
    await writeFile(resolve(out, 'product-initial.hwpx'), initialBytes, { flag: 'wx' });
    await h.focusEnd(); await page.keyboard.insertText(' 추가 편집도 함께 보존합니다.');
    await page.getByRole('button', { name: '저장', exact: true }).click();
    await expect(page.getByRole('status')).toHaveText('저장 완료');
    latestUrl = await page.getByRole('link', { name: '저장본 다운로드' }).getAttribute('href');
    document = await get(latestUrl.split('/versions/')[0]);
  }
  await expect(page.getByRole('status')).toHaveText('저장 완료');
  proof.document = document;
  const h = liveTools({ browser, context, page });
  const download = await context.request.get(latestUrl); expect(download.ok()).toBe(true);
  const bytes = await download.body(); proof.sha256 = hash(bytes);
  if (values.input) expect(proof.sha256).toBe(proof.input_sha256);
  await writeFile(resolve(out, `${phase}-download.hwpx`), bytes, { flag: 'wx' });
  proof.manifest = await h.inspect(bytes);
  expect(proof.manifest.paragraphs.map(p => p.text)).toEqual(values.input
    ? expectedText.concat('한컴 수정 확인: 흰 배경에서 수정하고 저장했습니다.') : expectedText);
  if (!values.input) {
    expect(proof.manifest.paragraphs.slice(0, 2)).toEqual(proof.initial_manifest.paragraphs.slice(0, 2));
    expect(proof.manifest.binary_resources).toEqual(proof.initial_manifest.binary_resources);
  }
  const header = strFromU8(unzipSync(bytes)['Contents/header.xml']);
  proof.brushes = [...header.matchAll(/<hc:winBrush\b[^>]*\/>/g)].map(m => m[0]);
    expect(proof.brushes.every(b => /faceColor="#FFFFFFFF"/.test(b))).toBe(true);
    expect(header).toContain('shadeColor="#FFFFFFFF"');
    expect(header).not.toContain('shadeColor="none"');
  proof.versions = await get(`/api/documents/${document.id}/versions`);
  expect(proof.versions).toHaveLength(values.input ? 1 : 2);
  if (!values.input) {
    expect(document.current_version).not.toBe(proof.initial_version);
    const old = await context.request.get(`/api/documents/${document.id}/versions/${proof.initial_version}/file`);
    expect(old.ok()).toBe(true); expect(hash(await old.body())).toBe(proof.initial_sha256);
  }
  proof.stages.product_save_download = 'passed'; await record();
  await page.screenshot({ path: resolve(out, `${phase}-before-reopen.png`) });
  await page.getByRole('button', { name: '← 문서 목록', exact: true }).click();
  await page.getByRole('row').filter({ has: page.locator(`a[href="${latestUrl}"]`) }).locator('td').first().getByRole('button').click();
  await expect(page.getByRole('status')).toHaveText('저장 완료');
  h.setFrame(page.frames().find(f => f.url().includes('/rhwp/')));
  proof.reopened_manifest = await h.inspect(await h.exported());
  expect(proof.reopened_manifest.paragraphs.map(p => p.text)).toEqual(proof.manifest.paragraphs.map(p => p.text));
  if (values.input) expect(proof.manifest.paragraphs.at(-1).text).toBe('한컴 수정 확인: 흰 배경에서 수정하고 저장했습니다.');
  await page.screenshot({ path: resolve(out, `${phase}-reopened.png`) });
  proof.stages.web_reopen = 'passed'; proof.completed_at = new Date().toISOString(); await record();
  console.log(JSON.stringify({ document_id: document.id, previous_version: proof.initial_version,
    version_id: document.current_version, sha256: proof.sha256, stages: proof.stages }));
} catch (error) { proof.failure = String(error); await record(); console.error(String(error)); process.exitCode = 1; }
console.log('합성 시험 결과 창을 유지합니다. 이 시험은 실제 AI 호출 시험이 아닙니다.');
await new Promise(resolve => browser.once('disconnected', resolve));
