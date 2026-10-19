import { test, expect } from '@playwright/test';
import { writeFile } from 'node:fs/promises';
import { unzipSync, strFromU8 } from 'fflate';

test('REAL rhwp + RECORDED rich report: actual Codex visuals render and export without loss', async ({ page, request }, info) => {
  test.skip(!process.env.HWP_REPORT_FIXTURES, 'Requires explicitly recorded Codex result; no new model call.');
  const before = (await (await request.get('/api/documents')).json()).length;
  await page.goto('/'); await page.getByRole('button', { name: '보고서 작성', exact: true }).click();
  await expect(page.getByLabel('문서 서식 표준', { exact: true })).toBeEnabled();
  await page.getByLabel('문서 서식 표준', { exact: true }).selectOption(''); // Preserve the recorded legacy visual contract.
  await page.getByLabel('보고서 작성 요청', { exact: true }).fill('가상 금융회사 합성 보안 대응 훈련 보고서를 작성해줘.');
  const posted = page.waitForResponse(r => r.url().endsWith('/api/report-tasks') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Codex로 보고서 초안 만들기', exact: true }).click();
  const id = (await (await posted).json()).id;
  await page.getByRole('button', { name: '이 내용으로 문서 만들기', exact: true }).click();
  await expect(page.getByRole('button', { name: '초안 확정·서버 저장', exact: true })).toBeVisible({ timeout: 90000 });
  await expect(page.getByRole('alert')).toHaveCount(0);
  const task = await (await request.get(`/api/report-tasks/${id}`)).json();
  expect(task.execution.delivery).toBe('recorded-result-replay');
  expect(task.draft.sections.flatMap((s: any) => s.tables || []).length).toBeGreaterThan(0);
  const preview = await (await request.get(`/api/report-tasks/${id}/preview`)).json();
  const frame = page.frames().find(f => f.url().includes('/rhwp/'))!;
  const exported = await frame.evaluate(() => {
    const output = (window as any).hwpAssistantBridge.exportSnapshot();
    return { bytes: [...output.bytes], loss: output.contentLoss };
  });
  expect(exported.loss.count).toBe(0);
  const bytes = Buffer.from(exported.bytes), section = strFromU8(unzipSync(bytes)['Contents/section0.xml']);
  const logicalText = await page.evaluate(xml => {
    const root = new DOMParser().parseFromString(xml, 'application/xml');
    return [...root.getElementsByTagNameNS('http://www.hancom.co.kr/hwpml/2011/paragraph', 'p')].map(p =>
      [...p.children].filter(c => c.localName === 'run').flatMap(r => [...r.children].filter(c => c.localName === 't')).map(t => t.textContent || '').join('')).filter(Boolean);
  }, section);
  expect(logicalText).toEqual(preview.plan.map((p: any) => p.text));
  // The native editor virtualizes page canvases. Capture the painted pool
  // without scrolling/re-indexing it or treating its size as physical pages.
  const canvases = await frame.locator('#scroll-container canvas').elementHandles();
  const count = canvases.length;
  for (let i = 0; i < count; i++) {
    const png = await canvases[i].evaluate(c => (c as HTMLCanvasElement).toDataURL('image/png'));
    await writeFile(info.outputPath(`actual-page-${i + 1}.png`), Buffer.from(png.split(',')[1], 'base64'));
  }
  await writeFile(info.outputPath('actual-rich-preview.hwpx'), bytes);
  expect(await (await request.get('/api/documents')).json()).toHaveLength(before);
  await writeFile(info.outputPath('actual-rich-proof.json'), JSON.stringify({ actual_model_calls_this_browser_run: 0,
    delivery: 'recorded-result-replay', real_engine: true, human_approval: false, document_saved: false,
    painted_canvas_count: count, exact_commands: preview.plan.length, loss: exported.loss, logical_text_preserved: true }, null, 2));
});

test('REAL rhwp + MOCK report: streamed table diagram emphasis, edited save retry and reopen', async ({ page, request }, info) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== '1' || !!process.env.HWP_REPORT_FIXTURES, 'Synthetic child only; no model call.');
  let id = ''; const saves: any[] = [];
  const before = (await (await request.get('/api/documents')).json()).length;
  await page.route('**/api/report-tasks/*/preview/save', async route => {
    saves.push(route.request().postDataJSON());
    if (saves.length === 1) { const response = await route.fetch(); expect(response.ok(), await response.text()).toBeTruthy(); return route.abort('failed'); }
    return route.continue();
  });
  try {
    await page.goto('/'); await page.getByRole('button', { name: '보고서 작성', exact: true }).click();
    await expect(page.getByLabel('문서 서식 표준', { exact: true })).toBeEnabled();
    await page.getByLabel('문서 서식 표준', { exact: true }).selectOption(''); // Keep the existing summary/emphasis/TOC assertions on their original standard.
    await page.getByLabel('보고서 작성 요청', { exact: true }).fill('[STREAM] [RICH] 표와 흐름도 및 강조가 있는 합성 보고서 시험');
    const posted = page.waitForResponse(r => r.url().endsWith('/api/report-tasks') && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Codex로 보고서 초안 만들기', exact: true }).click(); id = (await (await posted).json()).id;
    const paper = page.getByLabel('생성 중인 보고서', { exact: true });
    await expect(paper.locator('strong').filter({ hasText: '(요약)' })).toBeVisible();
    expect((await request.post(`/api/test/report-stream/${id}/release`)).ok()).toBeTruthy();
    await expect(paper.getByTestId('report-content-table')).toContainText('접근 권한 점검');
    await expect(paper.getByTestId('report-content-diagram')).toContainText('복구 및 검증');
    await expect(paper.getByTestId('report-content-table').locator('strong.report-emphasis-blue')).toHaveText('접근 권한 점검');
    expect((await (await request.get(`/api/report-tasks/${id}`)).json()).status).toBe('running');
    expect(await (await request.get('/api/documents')).json()).toHaveLength(before);
    await page.screenshot({ path: info.outputPath('rich-stream.png'), fullPage: true });
    expect((await request.post(`/api/test/report-stream/${id}/finish`)).ok()).toBeTruthy();
    await page.getByRole('button', { name: '이 내용으로 문서 만들기', exact: true }).click();
    await expect(page.getByRole('button', { name: '초안 확정·서버 저장', exact: true })).toBeVisible({ timeout: 90000 });
    await expect(page.getByRole('alert')).toHaveCount(0);
    expect(await (await request.get('/api/documents')).json()).toHaveLength(before);
    const frozen = await (await request.get(`/api/report-tasks/${id}/preview`)).json();
    expect(frozen.presentation_policy).toBe('tables-flow-emphasis-v1');
    await writeFile(info.outputPath('rich-preview.hwpx'), Buffer.from(frozen.layout_base64, 'base64'));
    const frame = page.frames().find(f => f.url().includes('/rhwp/'))!;
    await page.screenshot({ path: info.outputPath('rich-hwpx.png'), fullPage: true });
    const canvases = await frame.locator('#scroll-container canvas').elementHandles();
    for (let i = 0; i < canvases.length; i++) {
      const png = await canvases[i].evaluate(canvas => (canvas as HTMLCanvasElement).toDataURL('image/png'));
      await writeFile(info.outputPath(`rich-page-${i + 1}.png`), Buffer.from(png.split(',')[1], 'base64'));
    }
    await frame.getByRole('textbox', { name: '문서 편집 입력', exact: true }).focus();
    await page.keyboard.press('Control+End'); await page.keyboard.insertText(' 수동 편집 보존 확인');
    await expect.poll(() => frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toContain('수동 편집 보존 확인');
    await page.getByRole('button', { name: '초안 확정·서버 저장', exact: true }).click();
    await page.getByRole('button', { name: '같은 보고서 저장 재확인', exact: true }).click({ timeout: 60000 });
    await expect(page.getByRole('status')).toHaveText('HWPX 서버 저장 완료');
    expect(saves).toHaveLength(2); expect(saves[1]).toEqual(saves[0]);
    const href = (await page.getByRole('link', { name: '완성 보고서 다운로드', exact: true }).getAttribute('href'))!;
    const saved = await (await request.get(href)).body(); const zip = unzipSync(saved);
    const xml = strFromU8(zip['Contents/section0.xml']), header = strFromU8(zip['Contents/header.xml']);
    const evidence = await page.evaluate(({ xml, header }) => {
      const parser = new DOMParser(), root = parser.parseFromString(xml, 'application/xml'), h = parser.parseFromString(header, 'application/xml');
      const HP = 'http://www.hancom.co.kr/hwpml/2011/paragraph', HH = 'http://www.hancom.co.kr/hwpml/2011/head';
      const chars = [...h.getElementsByTagNameNS(HH, 'charPr')];
      const runs = [...root.getElementsByTagNameNS(HP, 'run')].flatMap(run => {
        const text = [...run.children].filter(c => c.localName === 't').map(t => t.textContent || '').join('');
        const style = chars.find(c => c.getAttribute('id') === run.getAttribute('charPrIDRef'))!;
        return text ? [{ text, bold: !!style.getElementsByTagNameNS(HH, 'bold').length, color: style.getAttribute('textColor') }] : [];
      });
      const tables = [...root.getElementsByTagNameNS(HP, 'tbl')];
      const styles = [...h.getElementsByTagNameNS(HH, 'style')];
      const tocTables = tables.filter(table => {
        let host = table.parentElement;
        while (host && !(host.localName === 'p' && host.parentElement === root.documentElement)) host = host.parentElement;
        return styles.find(style => style.getAttribute('id') === host?.getAttribute('styleIDRef'))?.getAttribute('name')?.startsWith('HWP Assistant TOC box:');
      });
      const bodyTables = tables.filter(table => !tocTables.includes(table));
      return { tables: bodyTables.length, tocTables: tocTables.length,
        cells: bodyTables.flatMap(table => [...table.getElementsByTagNameNS(HP, 'tc')].map(c => [...c.getElementsByTagNameNS(HP, 't')].map(t => t.textContent).join(''))),
        runs };
    }, { xml, header });
    expect(evidence.tables).toBe(2); expect(evidence.tocTables).toBe(1); expect(evidence.cells).toHaveLength(19);
    expect(evidence.cells).toContain('접근 권한 점검'); expect(evidence.cells).toContain('복구 및 검증');
    expect(evidence.runs).toContainEqual({ text: '(요약)', bold: true, color: '#000000' });
    expect(evidence.runs).toContainEqual({ text: '접근 권한 점검', bold: true, color: '#245F9E' });
    expect(evidence.runs).toContainEqual({ text: '30일 내', bold: false, color: '#245F9E' });
    expect(xml).toContain('수동 편집 보존 확인'); expect(xml).not.toContain('REPORT_SLOT_');
    expect(await (await request.get(href.split('/versions/')[0] + '/versions')).json()).toHaveLength(1);
    await writeFile(info.outputPath('rich-saved.hwpx'), saved);
    await page.getByRole('button', { name: '보고서 편집하기', exact: true }).click();
    await expect(page.getByRole('status')).toHaveText('저장 완료');
    await expect(page.getByRole('alert')).toHaveCount(0);
    await page.screenshot({ path: info.outputPath('rich-reopened.png'), fullPage: true });
    await writeFile(info.outputPath('rich-proof.json'), JSON.stringify({ real_engine: true, actual_model_calls: 0, human_approval: false,
      automated_approval: true, live_visuals_before_completion: true, no_premature_document: true, saved_versions: 1,
      identical_save_retry: true, exact_commands: frozen.plan.length, ...evidence }, null, 2));
  } finally {
    if (id) { await request.post(`/api/test/report-stream/${id}/release`); await request.post(`/api/test/report-stream/${id}/finish`); }
    await page.unrouteAll({ behavior: 'wait' });
  }
});
