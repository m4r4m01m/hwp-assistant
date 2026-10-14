import { test, expect } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

test('REAL rhwp + MOCK report stream: received text before completion, reload and formatted handoff', async ({ page, request }, info) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== '1' || !!process.env.HWP_REPORT_FIXTURES, 'Synthetic child events only; never starts a model.');
  let id = '', posts = 0;
  const before = (await (await request.get('/api/documents')).json()).length;
  page.on('request', r => { if (r.url().endsWith('/api/report-tasks') && r.method() === 'POST') posts++; });
  try {
    await page.goto('/'); await page.getByRole('button', { name:'보고서 작성', exact:true }).click();
    await page.getByLabel('보고서 작성 요청', { exact:true }).fill('[STREAM] 금융권 보안 보고서 합성 실시간 시험');
    const posted = page.waitForResponse(r => r.url().endsWith('/api/report-tasks') && r.request().method() === 'POST');
    await page.getByRole('button', { name:'Codex로 보고서 초안 만들기', exact:true }).click();
    id = (await (await posted).json()).id;
    const paper = page.getByLabel('생성 중인 보고서', { exact:true });
    await expect(paper.getByTestId('stream-report-paragraph').first()).toContainText('금융권 해킹 대응 보고서');
    await expect(paper.getByTestId('stream-report-paragraph').last()).toContainText('□ (요약)');
    await expect(paper).not.toContainText('외부 업체 위험');
    expect((await (await request.get(`/api/report-tasks/${id}`)).json()).status).toBe('running');
    expect(await (await request.get('/api/documents')).json()).toHaveLength(before);
    await expect(page.getByRole('button', { name:'초안 확정·서버 저장', exact:true })).toHaveCount(0);
    await page.screenshot({ path:info.outputPath('received-before-completion.png'), fullPage:true });
    await page.reload(); await page.getByRole('button', { name:'보고서 작성', exact:true }).click();
    await expect(paper).toContainText('□ (요약)'); expect(posts).toBe(1);
    expect((await request.post(`/api/test/report-stream/${id}/release`)).ok()).toBeTruthy();
    await expect(paper).toContainText('외부 업체 위험');
    await expect(paper.getByTestId('stream-report-paragraph').first()).toHaveCSS('border-top-color','rgb(67, 127, 193)');
    const inProgress = await (await request.get(`/api/report-tasks/${id}`)).json();
    expect(inProgress.status).toBe('running'); expect(inProgress.draft).toBeNull();
    await page.screenshot({ path:info.outputPath('body-growing-before-completion.png'), fullPage:true });
    expect((await request.post(`/api/test/report-stream/${id}/finish`)).ok()).toBeTruthy();
    await expect(page.getByRole('button', { name:'초안 확정·서버 저장', exact:true })).toBeVisible({ timeout:60000 });
    await expect(page.locator('.formatted-report-surface')).toHaveAttribute('data-ready','true');
    await expect(page.getByRole('alert')).toHaveCount(0);
    expect(await (await request.get('/api/documents')).json()).toHaveLength(before);
    const task = await (await request.get(`/api/report-tasks/${id}`)).json();
    expect(task.execution.timings.first_text_ms).toBeLessThan(task.execution.timings.generation_ms);
    await page.screenshot({ path:info.outputPath('stream-to-editable-hwpx.png'), fullPage:true });
    await writeFile(info.outputPath('stream-proof.json'), JSON.stringify({ model:'synthetic stdio events', actual_model_calls:0,
      real_engine:true, human_approval:false, generation_posts:posts, partial_before_completion:true,
      restored_without_new_request:true, no_premature_document:true, timings:task.execution.timings },null,2));
  } finally {
    if (id) { await request.post(`/api/test/report-stream/${id}/release`); await request.post(`/api/test/report-stream/${id}/finish`); }
  }
});

test('MOCK report stream: cancel keeps partial text and new request is independent', async ({ page, request }) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== '1' || !!process.env.HWP_REPORT_FIXTURES, 'Synthetic stream only.');
  let id='';
  try {
    await page.goto('/'); await page.getByRole('button', { name:'보고서 작성', exact:true }).click();
    await page.getByLabel('보고서 작성 요청', { exact:true }).fill('[STREAM] 중단 보존 합성 보고서');
    const posted = page.waitForResponse(r => r.url().endsWith('/api/report-tasks') && r.request().method() === 'POST');
    await page.getByRole('button', { name:'Codex로 보고서 초안 만들기', exact:true }).click(); id=(await (await posted).json()).id;
    await expect(page.getByTestId('stream-report-paragraph').first()).toBeVisible();
    await page.getByRole('button', { name:'결과 수신 중단', exact:true }).click();
    await expect(page.getByText('작성 중인 내용 보존', { exact:true })).toBeVisible();
    await expect(page.getByTestId('stream-report-paragraph').first()).toBeVisible();
    expect((await (await request.get(`/api/report-tasks/${id}`)).json()).status).toBe('cancelled');
    await page.getByLabel('보고서 작성 요청', { exact:true }).fill('새 독립 보고서 요청 합성 시험');
    await page.getByRole('button', { name:'Codex로 보고서 초안 만들기', exact:true }).click();
    await expect(page.getByRole('button', { name:'초안 확정·서버 저장', exact:true })).toBeVisible({timeout:60000});
    expect((await (await request.get(`/api/report-tasks/${id}`)).json()).status).toBe('cancelled');
  } finally {
    if(id) { await request.post(`/api/test/report-stream/${id}/release`); await request.post(`/api/test/report-stream/${id}/finish`); }
  }
});
