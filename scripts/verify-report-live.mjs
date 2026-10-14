// Explicit, opt-in live product test. Uses the existing Codex login and public research.
import { chromium, expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { createHash } from 'node:crypto';
const { values } = parseArgs({ options: { url: { type: 'string' }, out: { type: 'string' }, 'allow-codex': { type: 'boolean' } } });
if (!values['allow-codex'] || !values.out || !values.url || !['127.0.0.1', 'localhost'].includes(new URL(values.url).hostname))
  throw Error('Requires explicit --allow-codex, --url loopback and a new --out directory.');
const out = resolve(values.out); await mkdir(out); // Never overwrite earlier evidence.
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1500, height: 1000 } });
let taskId;
try {
  await page.goto(values.url);
  await page.getByRole('button', { name: '보고서 작성', exact: true }).click();
  await page.getByLabel('보고서 작성 요청', { exact: true }).fill('2026년 10월 6일 기준 금융권 해킹 문제에 대한 경영진 보고서를 만들어줘. 국내 공개 사고 사례와 경영진이 결정할 사항, 책임자가 포함된 30·60·90일 대응 계획을 공식 출처와 함께 제시해줘. 간결한 3개 절로 작성하고 사실과 분석·제안을 구분해줘.');
  await page.screenshot({ path: resolve(out, 'prompt.png'), fullPage: true });
  const posted = page.waitForResponse(r => r.url().endsWith('/api/report-tasks') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Codex로 보고서 초안 만들기', exact: true }).click();
  const accepted = await (await posted).json(); taskId = accepted.id;
  await writeFile(resolve(out, 'accepted.json'), JSON.stringify(accepted, null, 2));
  await expect(page.getByRole('button', { name: '초안 승인·HWPX 만들기', exact: true })).toBeVisible({ timeout: 280000 });
  const draft = await (await page.request.get(values.url + '/api/report-tasks/' + taskId)).json();
  await writeFile(resolve(out, 'draft.json'), JSON.stringify(draft, null, 2));
  await page.screenshot({ path: resolve(out, 'draft.png'), fullPage: true });
  // This is automated test approval, never a claim that a person approved the document.
  await page.getByRole('button', { name: '초안 승인·HWPX 만들기', exact: true }).click();
  await expect(page.getByRole('link', { name: '완성 보고서 다운로드', exact: true })).toBeVisible({ timeout: 90000 });
  const href = await page.getByRole('link', { name: '완성 보고서 다운로드', exact: true }).getAttribute('href');
  const response = await page.request.get(new URL(href, values.url).href); expect(response.ok()).toBeTruthy();
  const bytes = await response.body(); await writeFile(resolve(out, 'financial-cyber-executive.hwpx'), bytes);
  const final = await (await page.request.get(values.url + '/api/report-tasks/' + taskId)).json();
  const approved = await (await page.request.get(values.url + '/api/report-tasks/' + taskId + '/approved')).json();
  await writeFile(resolve(out, 'proof.json'), JSON.stringify({ task_id: taskId, executor: final.execution, input: final.input,
    model: 'actual Codex', engine: 'real rhwp', automated_approval: true, human_approval: false,
    saved_version: final.approval.saved_version, exact_commands: approved.plan.length,
    output_sha256: createHash('sha256').update(bytes).digest('hex'), save_binding: approved.save_binding }, null, 2));
  await page.getByRole('button', { name: '보고서 편집하기', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('저장 완료');
  await page.screenshot({ path: resolve(out, 'reopened.png'), fullPage: true });
  console.log(JSON.stringify({ passed: true, taskId, output: out, actual_model_calls: 1, human_approval: false }));
} catch (error) {
  await page.screenshot({ path: resolve(out, 'failure.png'), fullPage: true }).catch(() => {});
  await writeFile(resolve(out, 'failure.json'), JSON.stringify({ error: String(error), taskId }, null, 2));
  throw error;
} finally { await browser.close(); }
