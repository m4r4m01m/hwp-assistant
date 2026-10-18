// Manual acceptance harness, adapted from .cache/codex-flow/verify.mjs.
// Never clicks a candidate, submits an approval, or writes document XML/DB records.
import { chromium, expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import { unzipSync, strFromU8 } from '../frontend/node_modules/fflate/esm/index.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { parseArgs } from 'node:util';
import { continueHumanVerification } from './verify-codex-human-continuation.mjs';
import { liveTools } from './verify-codex-human-tools.mjs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const { values } = parseArgs({ options: {
  url: { type: 'string', default: 'http://127.0.0.1:15174' },
  out: { type: 'string', default: '.cache/codex-human-2026-09-27' },
  provider: { type: 'string', default: 'codex' },
  user: { type: 'string', default: 'alice' },
  help: { type: 'boolean', default: false },
} });
if (values.help) {
  console.log('node scripts/verify-codex-human.mjs [--provider codex|llm] [--user alice|local] --url <loopback app> --out <new directory>');
  console.log('Visible Windows Chrome; one configured provider request; human clicks the product candidate button.');
  console.log('Default: isolated alice account and Codex. HWP_TEST_APP_TOKEN supplies an authenticated account token.');
  console.log('Keep this terminal and browser open. Enter/Resume/files are never approval. Output directories cannot be reused.');
  process.exit(0);
}
if (!['codex', 'llm'].includes(values.provider)) throw Error('provider must be codex or llm');
if (values.provider === 'llm' && values.out === '.cache/codex-human-2026-09-27') throw Error('Choose a new --out for the local model verification.');
const providerLabel = values.provider === 'llm' ? '로컬·사내 모델' : '실제 Codex';
const requestButton = values.provider === 'llm' ? '로컬·사내 모델로 후보 2개 요청' : '실제 Codex로 후보 2개 요청';
const root = fileURLToPath(new URL('../', import.meta.url));
const out = path.resolve(root, values.out);
const baseURL = new URL(values.url).origin;
if (!['127.0.0.1', 'localhost'].includes(new URL(baseURL).hostname)) throw Error('This synthetic test requires a loopback server.');
await mkdir(path.dirname(out), { recursive: true });
// Reserve a new directory before opening a browser or requesting a model.
// A folder whose proof.json was moved must not overwrite its remaining evidence.
await mkdir(out);
const proofPath = path.join(out, 'proof.json');
const proof = {
  kind: 'headed Windows browser, configured provider, HUMAN candidate approval only',
  provider: values.provider, expected_user: values.user,
  started_at: new Date().toISOString(), harness_pid: process.pid, url: baseURL,
  phase: 'starting', model_request_count: 0, ui_clicks: [], network: [], checks: {},
  stages: { approval: 'not executed', apply: 'not executed', save: 'not executed', reopen: 'not executed' },
};
// Refuse a repeat invocation before launching a browser or calling AI.
await writeFile(proofPath, JSON.stringify(proof, null, 2), { flag: 'wx' });
let writes = Promise.resolve();
const record = () => {
  const content = JSON.stringify(proof, null, 2) + '\n';
  writes = writes.then(() => writeFile(proofPath, content));
  return writes;
};
const hash = data => createHash('sha256').update(data).digest('hex');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, context, page, frame;
const closed = () => !browser?.isConnected() || page?.isClosed();
async function announce(phase, message) {
  proof.phase = phase; await record(); console.log(message);
  if (page && !page.isClosed()) await page.evaluate(({ phase, message }) => {
    document.title = `HWP Assistant · 사용자 승인 시험 · ${phase}`;
    let banner = document.getElementById('human-test-banner');
    if (!banner) {
      banner = document.createElement('div'); banner.id = 'human-test-banner';
      banner.style.cssText = 'position:fixed;left:12px;bottom:8px;max-width:54vw;padding:9px;background:#102d45;color:white;border-radius:5px;z-index:9999;font:13px sans-serif;pointer-events:none';
      document.body.append(banner);
    }
    banner.textContent = message;
  }, { phase, message });
}
function visibleWindows() {
  if (process.platform !== 'win32') throw Error('Run this manual harness in your Windows PowerShell session.');
  // Read-only OS evidence for this harness's Chrome children; no window manipulation.
  const script = `
    Add-Type -TypeDefinition 'using System;using System.Runtime.InteropServices;public class HwpWindowProbe{[DllImport("user32.dll")]public static extern bool IsWindowVisible(IntPtr hWnd);[DllImport("user32.dll")]public static extern bool IsIconic(IntPtr hWnd);}'
    $all = Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name
    $ids = @(${process.pid})
    do { $next = @($all | Where-Object { $_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids }); $ids += @($next.ProcessId) } while ($next.Count -gt 0)
    $rows = @($all | Where-Object { $_.ProcessId -in $ids -and $_.Name -eq 'chrome.exe' } | ForEach-Object {
      $p = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
      if ($p.MainWindowHandle -ne 0) { [pscustomobject]@{pid=$p.Id;session=$p.SessionId;visible=[HwpWindowProbe]::IsWindowVisible($p.MainWindowHandle);minimized=[HwpWindowProbe]::IsIconic($p.MainWindowHandle)} }
    })
    [pscustomobject]@{runner_session=[Diagnostics.Process]::GetCurrentProcess().SessionId;windows=$rows} | ConvertTo-Json -Compress -Depth 4
  `;
  const state = JSON.parse(execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], { encoding: 'utf8', windowsHide: true }));
  if (!state.windows.some(w => w.visible && !w.minimized && w.session === state.runner_session && w.session !== 0))
    throw Error('No visible Chrome window in the interactive Windows session. Run the printed command in your own PowerShell; no AI request was made.');
  return state;
}
async function get(url) {
  const r = await context.request.get(url);
  if (!r.ok()) throw Error(`GET ${url}: ${r.status()}`);
  return r.json();
}
const snapshot = () => frame.evaluate(() => window.hwpAssistantBridge.snapshot());
async function exported() {
  const result = await frame.evaluate(() => {
    const s = window.hwpAssistantBridge.exportSnapshot();
    return { bytes: Array.from(s.bytes), loss: s.contentLoss.count, revision: s.revision, epoch: s.epoch };
  });
  expect(result.loss).toBe(0);
  return Buffer.from(result.bytes);
}
async function inspect(bytes) {
  // Read-only ZIP/XML inspection; never constructs or modifies an output HWPX.
  const files = unzipSync(bytes);
  const sections = Object.keys(files).filter(n => /^Contents\/section\d+\.xml$/.test(n)).sort();
  const paragraphs = await page.evaluate(sections => sections.flatMap(({ name, xml }) => {
    const tree = new DOMParser().parseFromString(xml, 'application/xml');
    if (tree.querySelector('parsererror')) throw Error('HWPX XML parse error');
    return Array.from(tree.documentElement.children).filter(e => e.localName === 'p').map((p, index) => ({
      section: Number(name.match(/section(\d+)/)[1]), paragraph: index,
      text: Array.from(p.getElementsByTagNameNS('*', 't')).map(t => t.textContent).join(''),
      xml: new XMLSerializer().serializeToString(p),
    }));
  }), sections.map(name => ({ name, xml: strFromU8(files[name]) })));
  return { paragraphs: paragraphs.map(p => ({ ...p, xml: undefined, structure_sha256: hash(p.xml) })),
    binary_resources: Object.fromEntries(Object.entries(files).filter(([n]) => !n.endsWith('/') && !/\.(xml|hpf)$/i.test(n) && !n.startsWith('Preview/')).map(([n, bytes]) => [n, hash(bytes)])) };
}
const isTarget = (p, target) => p.section === target.section && p.paragraph === target.paragraph;
function preserved(before, after, target) {
  expect(after.paragraphs.filter(p => !isTarget(p, target))).toEqual(before.paragraphs.filter(p => !isTarget(p, target)));
  expect(after.binary_resources).toEqual(before.binary_resources);
}
async function focusEnd() {
  await frame.locator('#scroll-container canvas').first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press('Control+End');
}


try {
  browser = await chromium.launch({ channel: 'chrome', headless: false, args: ['--start-maximized'] });
  context = await browser.newContext({ baseURL, viewport: null, acceptDownloads: true });
  page = await context.newPage(); page.setDefaultTimeout(30000);
  await page.goto('/'); await page.bringToFront();
  proof.visible_window = visibleWindows();
  await announce('preparing_synthetic_document', '수동 승인 시험 준비 중입니다. 자동 입력이 끝날 때까지 기다려 주세요.');
  if ((await get('/api/auth/status')).authentication_required) {
    await expect(page.getByLabel('인증 토큰')).toBeVisible();
    await page.getByLabel('인증 토큰').fill(process.env.HWP_TEST_APP_TOKEN || 'alice-' + 'a'.repeat(32));
    const loginResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/auth/login' && r.request().method() === 'POST');
    await page.getByRole('button', { name: '로그인', exact: true }).click();
    expect((await loginResponse).status()).toBe(200);
  }
  expect((await get('/api/auth/me')).user_id).toBe(values.user);
  const configured = await get('/api/rewrite-provider');
  expect(configured.provider).toBe(values.provider);
  expect(configured.configured && configured.allowed).toBe(true);
  proof.provider_configuration = configured;
  if (values.provider === 'llm') {
    const models = await get('/api/ai/models');
    expect(models.configured_model).toBe(configured.model);
    expect(models.model_available).toBe(true);
    if (models.configured_model === 'hwp-synthetic-fixture') throw Error('This human acceptance harness requires an actual configured model, not the automated fixture.');
    proof.model_verification = models;
    await record();
  }
  // Observe only specific synthetic task traffic. No headers, tokens, or whole delta bodies are logged.
  page.on('response', response => { void (async () => {
    const request = response.request(), pathname = new URL(response.url()).pathname;
    if (request.method() !== 'POST' || !proof.task_id) return;
    let event;
    if (pathname === `/api/rewrite-tasks/${proof.task_id}/approve`)
      event = { kind: 'approval', requested: request.postDataJSON() };
    else if (/^\/api\/applications\/[^/]+\/receipt$/.test(pathname))
      event = { kind: 'receipt', application_id: pathname.split('/')[3], requested: request.postDataJSON() };
    else if (pathname === `/api/documents/${proof.document?.id}/versions`)
      event = { kind: 'save', base_version: new URL(response.url()).searchParams.get('base_version') };
    if (event) {
      event.status = response.status();
      const result = await response.json();
      event.response = event.kind === 'approval' && response.ok() ? { id: result.id, candidate_id: result.candidate_id, approved_by: result.approved_by } : result;
      proof.network.push(event); await record();
    }
  })().catch(error => { proof.observer_error = String(error); }); });
  await page.exposeFunction('hwpManualCandidateClick', async event => {
    proof.ui_clicks.push(event); await record();
    console.log(`사용자 UI 선택 감지: 후보 ${event.candidate_id} (아직 저장 성공을 의미하지 않음)`);
  });
  await page.evaluate(() => document.addEventListener('click', event => {
    const button = event.target.closest?.('button');
    const candidate = button?.closest('[data-candidate-id]');
    const task = candidate?.closest('[data-task-id]');
    if (button && candidate && task && /승인·적용·저장/.test(button.textContent))
      window.hwpManualCandidateClick({ task_id: task.dataset.taskId, candidate_id: candidate.dataset.candidateId,
        trusted: event.isTrusted, at: new Date().toISOString() });
  }, true));
  await context.route('**/api/threads/*/rewrite-tasks', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    if (proof.model_request_count >= 1) {
      console.error('모델 재호출 차단: 이 시험은 1회만 허용합니다.');
      return route.abort('blockedbyclient');
    }
    proof.model_request_count++; await record(); await route.continue();
  });
  await page.getByRole('button', { name: '+ 새 문서', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('저장되지 않은 변경 있음', { timeout: 90000 });
  frame = page.frames().find(f => f.url().includes('/rhwp/'));
  const nonTarget = '보존 확인: 회의 장소는 제1회의실입니다.';
  const extraNonTarget = ' 담당 부서는 운영팀입니다.';
  const original = values.provider === 'llm' ? '김민수는 10월 9일까지 30만원을 쓰지 않는다.' : '회의 자료는 금요일까지 준비합니다.';
  const extra = values.provider === 'llm' ? ' 지급 승인은 하지 않았다.' : ' 담당자는 준비가 끝나면 팀에 알려 주면 좋겠습니다.';
  await focusEnd(); await page.keyboard.press('Enter'); await page.keyboard.insertText(nonTarget);
  await page.keyboard.press('Enter'); await page.keyboard.insertText(original);
  await page.getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('저장 완료');
  const originalUrl = await page.getByRole('link', { name: '저장본 다운로드' }).getAttribute('href');
  const docPath = originalUrl.split('/versions/')[0];
  proof.document = await get(docPath);
  const originalResponse = await context.request.get(originalUrl); expect(originalResponse.status()).toBe(200);
  const originalBytes = await originalResponse.body();
  proof.original_sha256 = hash(originalBytes); proof.original_manifest = await inspect(originalBytes);
  await page.getByRole('button', { name: '← 문서 목록', exact: true }).click();
  await page.getByRole('row').filter({ has: page.locator(`a[href="${originalUrl}"]`) }).locator('td').first().getByRole('button').click();
  await expect(page.getByRole('status')).toHaveText('저장 완료');
  frame = page.frames().find(f => f.url().includes('/rhwp/'));
  await page.getByRole('button', { name: '대화 · AI 열기', exact: true }).click();
  page.once('dialog', dialog => dialog.accept('사용자 직접 승인 시험 ' + new Date().toISOString()));
  await page.getByRole('button', { name: '공유 대화 만들기', exact: true }).click();
  await expect(page.getByRole('button', { name: requestButton, exact: true })).toBeVisible();
  // Test-only layout makes both choices readable. Product code and editor state are unchanged.
  await page.addStyleTag({ content: '.conversation-panel{width:min(650px,48vw)}.rewrite article h3{margin:8px 0}.rewrite article p{margin:6px 0;line-height:1.45}.rewrite article .text{padding:9px}' });
  await focusEnd(); await page.keyboard.press('Control+Home'); await page.keyboard.press('ArrowDown'); await page.keyboard.press('End');
  expect((await snapshot()).evidence.text).toBe(nonTarget);
  await page.keyboard.insertText(extraNonTarget);
  await page.keyboard.press('Control+End'); expect((await snapshot()).evidence.text).toBe(original);
  await page.keyboard.insertText(extra);
  proof.unsaved_snapshot = await snapshot();
  proof.unsaved_manifest = await inspect(await exported());
  expect(proof.unsaved_manifest.paragraphs.some(p => p.text === nonTarget + extraNonTarget)).toBe(true);
  const responsePromise = page.waitForResponse(r => r.request().method() === 'POST' && /\/rewrite-tasks$/.test(r.url()));
  await page.getByRole('button', { name: requestButton, exact: true }).click();
  const response = await responsePromise;
  if (!response.ok()) throw Error(`Rewrite task request: ${response.status()} ${await response.text()}`);
  proof.task_id = (await response.json()).id;
  proof.conversation_id = (await get(docPath + '/threads'))[0].id;
  await announce('model_running', `${providerLabel} 실행 중입니다. AI 요청은 1회만 수행합니다.`);
  let task;
  const deadline = Date.now() + 280000;
  do {
    task = (await get(`/api/threads/${proof.conversation_id}/rewrite-tasks`)).find(t => t.id === proof.task_id);
    if (task.status !== 'running') break;
    await sleep(1500);
  } while (Date.now() < deadline);
  proof.task = task; await record();
  if (task.status !== 'ready') throw Error(`Rewrite ${task.status}: ${task.error}`);
  expect(task.execution.provider).toBe(values.provider);
  if (values.provider === 'llm') {
    expect(task.execution.model).toBe(configured.model);
    // A review aid, not a semantic truth validator. Human comparison remains mandatory.
    proof.literal_fact_check = task.candidates.map(candidate => ({ candidate_id: candidate.id,
      name: candidate.replacement.includes('김민수'), date: candidate.replacement.includes('10월 9일'),
      amount: /30\s*만\s*원/u.test(candidate.replacement),
      negation_requires_human_review: true, replacement: candidate.replacement }));
  }
  expect(task.snapshot).toEqual(proof.unsaved_snapshot);
  expect(await snapshot()).toEqual(proof.unsaved_snapshot);
  const beforeVersions = await get(docPath + '/versions'); expect(beforeVersions).toHaveLength(1);
  expect(beforeVersions[0].id).toBe(task.base_version);
  const article = page.locator(`[data-task-id="${task.id}"]`);
  await expect(article.getByTestId('as-is')).toHaveText(task.snapshot.evidence.text);
  for (const candidate of task.candidates) await expect(article.getByTestId(`candidate-${candidate.ordinal}`)).toHaveText(candidate.replacement);
  await article.getByTestId('candidate-2').scrollIntoViewIfNeeded();
  proof.checks.before_approval = { unsaved_input_matches: true, editor_unchanged: true, confirmed_version_count: 1,
    non_target_unsaved_edit_present: true, displayed_candidate_ids: task.candidates.map(c => c.id) };
  proof.visible_window_at_comparison = visibleWindows();
  await page.screenshot({ path: path.join(out, 'comparison.png') });
  await record();

  // HUMAN boundary: no locator.click(), approval POST, stdin, pause()/Resume or choice file here.
  await continueHumanVerification({ browser, context, page, proof, proofPath, record, announce, out,
    ...liveTools({ browser, context, page }) });
} catch (error) {
  proof.failure = String(error); await record();
  console.error(String(error)); process.exitCode = 1;
  if (browser?.isConnected()) await announce('blocked_preserving_browser', '검증 중단: 창·미저장 입력·후보를 보존합니다. 터미널 오류를 확인하세요. 자동 재호출/강제 적용은 하지 않습니다.').catch(() => {});
} finally {
  // Neither success nor failure closes a human's review window or loses its buffer.
  if (browser?.isConnected()) {
    console.log('이 터미널을 열어 두세요. 검증 창은 사용자가 직접 닫을 때까지 유지됩니다. Enter/Resume은 후보 승인이 아닙니다.');
    await new Promise(resolve => browser.once('disconnected', resolve));
  }
  if (proof.phase !== 'passed' && !proof.failure) proof.failure = 'Browser closed before full verification';
  proof.browser_closed_at = new Date().toISOString(); await record();
}
