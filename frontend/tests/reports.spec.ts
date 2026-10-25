import { test, expect, type Page } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";
import { expectNativeTitleRules } from "./report-format-proof";

async function legacyReport(page: Page) {
  await expect(page.getByLabel("문서 양식", { exact: true })).toBeEnabled();
  await page.getByLabel("문서 양식", { exact: true }).selectOption("");
  await page.getByText("요청 예시", { exact: true }).click();
  await page.getByText("양식 관리·연결 정보", { exact: true }).click();
}

test("REAL rhwp + MOCK report: prompt, approval, exact commands, save retry and reopen", async ({ page, request }, info) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== "1" || !!process.env.HWP_REPORT_FIXTURES, "Requires isolated mock report server; never implicitly starts Codex.");
  const documentsBefore = (await (await request.get("/api/documents")).json()).length;
  const sent: any[] = [], saves: any[] = [];
  await page.route("**/api/report-tasks", async route => {
    if (route.request().method() !== "POST") return route.continue();
    sent.push(route.request().postDataJSON());
    if (sent.length === 1) { expect((await route.fetch()).ok()).toBeTruthy(); return route.abort("failed"); }
    return route.continue();
  });
  await page.route("**/api/report-tasks/*/preview/save", async route => {
    saves.push(route.request().postDataJSON());
    if (saves.length === 1) { const response = await route.fetch(); expect(response.ok(), await response.text()).toBeTruthy(); return route.abort("failed"); }
    return route.continue();
  });
  try {
    await page.goto("/"); await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
    await legacyReport(page);
    await expect(page.getByRole("button", { name: "문서작성 기준 열기", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "금융권 해킹 대응", exact: true }).click();
    await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
    await page.getByRole("button", { name: "같은 요청 접수 재확인", exact: true }).click();
    await expect(page.getByRole("heading", { name: "금융권 해킹 대응 보고서 — 합성 시험", exact: true })).toBeVisible();
    expect(sent).toHaveLength(2); expect(sent[1]).toEqual(sent[0]);
    await page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true }).click();
    await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeVisible({ timeout: 60000 });
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(await (await request.get("/api/documents")).json()).toHaveLength(documentsBefore);
    const previewFrame = page.frames().find(f => f.url().includes("/rhwp/"))!;
    await expect(page.getByLabel("한글 초안 편집기", { exact: true })).toBeVisible();
    await previewFrame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
    await page.keyboard.press("Control+End");
    await page.keyboard.insertText(" 미리보기 직접편집 확인");
    await expect.poll(() => previewFrame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toContain("미리보기 직접편집 확인");
    await page.screenshot({ path: info.outputPath("report-preview.png"), fullPage: true });
    await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeVisible({ timeout: 60000 });
    await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).click();
    await expect(page.getByRole("button", { name: "같은 보고서 저장 재확인", exact: true })).toBeVisible({ timeout: 60000 });
    await page.getByRole("button", { name: "같은 보고서 저장 재확인", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    expect(saves).toHaveLength(2); expect(saves[1]).toEqual(saves[0]); // Same frozen edited output and save key.
    const url = (await page.getByRole("link", { name: "저장본 다운로드" }).getAttribute("href"))!;
    const data = await (await request.get(url)).body();
    const xml = strFromU8(unzipSync(data)["Contents/section0.xml"]);
    const header = strFromU8(unzipSync(data)["Contents/header.xml"]);
    const formatting = await page.evaluate(({ header, xml }) => {
      const parser = new DOMParser(), h = parser.parseFromString(header, "application/xml"), s = parser.parseFromString(xml, "application/xml");
      const HH = "http://www.hancom.co.kr/hwpml/2011/head", HP = "http://www.hancom.co.kr/hwpml/2011/paragraph", HC = "http://www.hancom.co.kr/hwpml/2011/core";
      const paras = [...s.documentElement.children];
      const paraStyle = (p: Element) => [...h.getElementsByTagNameNS(HH, "paraPr")].find(e => e.getAttribute("id") === p.getAttribute("paraPrIDRef"))!;
      const summary = paras.find(p => p.textContent?.trim().startsWith("□ (요약)"))!;
      const title = paraStyle(paras[1]), body = paraStyle(summary);
      const borderId = title.getElementsByTagNameNS(HH, "border")[0].getAttribute("borderFillIDRef");
      const border = [...h.getElementsByTagNameNS(HH, "borderFill")].find(e => e.getAttribute("id") === borderId)!;
      return { top: border.getElementsByTagNameNS(HH, "topBorder")[0].getAttribute("color"), bottom: border.getElementsByTagNameNS(HH, "bottomBorder")[0].getAttribute("color"),
        left: body.getElementsByTagNameNS(HC, "left")[0].getAttribute("value"),
        intent: body.getElementsByTagNameNS(HC, "intent")[0].getAttribute("value"),
        line: body.getElementsByTagNameNS(HH, "lineSpacing")[0].getAttribute("value") };
    }, { header, xml });
    expect(formatting).toEqual({ top: "#437FC1", bottom: "#437FC1", left: "0", intent: "-2250", line: "160" });
    expect(xml).toContain("경영진 결정 사항"); expect(xml).toContain("미리보기 직접편집 확인"); expect(xml).not.toContain("REPORT_SLOT_");
    expect(await (await request.get(url.split("/versions/")[0] + "/versions")).json()).toHaveLength(1);
    await writeFile(info.outputPath("report.hwpx"), data);
    // A clean save opens the regular editor automatically.
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    await page.screenshot({ path: info.outputPath("report-reopened.png"), fullPage: true });
    await page.getByRole('button', { name: '← 문서 목록', exact: true }).click();
    await page.getByRole('button', { name: '보고서 작성', exact: true }).click();
    await expect(page.getByLabel('보고서 작성 요청', { exact:true })).toBeEditable();
    await page.getByRole('button', { name: '최근 보고서', exact: true }).click();
    await page.locator('.report-history button').filter({ hasText:'금융권 해킹 대응 보고서 — 합성 시험' }).first().click();
    await expect(page.getByRole('status')).toHaveText('저장 완료'); // A saved request opens the real document, not raw draft text.
    await writeFile(info.outputPath("report-proof.json"), JSON.stringify({ actual_model_calls: 0, model: "mock", engine: "real rhwp", human_approval: false,
      automated_approval: true, same_generation_retry: true, same_output_retry: true, versions: 1 }, null, 2));
  } finally { await page.unrouteAll({ behavior: "wait" }); }
});

test("REAL rhwp + RECORDED report: long-title formatted preview without premature save", async ({ page, request }, info) => {
  test.skip(!process.env.HWP_REPORT_FIXTURES, "Explicit recorded report fixture; no new model call.");
  const rendered: any[] = [];
  await page.route("**/api/report-tasks/*/preview/render", async route => {
    rendered.push(route.request().postDataJSON());
    if (rendered.length === 1) { expect((await route.fetch()).ok()).toBeTruthy(); return route.abort("failed"); }
    return route.continue();
  });
  await page.goto("/"); await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await legacyReport(page);
  await page.getByRole("button", { name: "금융권 해킹 대응", exact: true }).click();
  const accepted = page.waitForResponse(r => r.url().endsWith('/api/report-tasks') && r.request().method() === 'POST');
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  const job = await (await accepted).json();
  await page.getByRole('button', { name: '이 내용으로 문서 만들기', exact: true }).click();
  await page.getByRole('button', { name: '한글 초안 준비 재시도', exact: true }).click({ timeout: 60000 });
  await expect(page.getByRole('button', { name: '초안 확정·서버 저장', exact: true })).toBeVisible({ timeout: 60000 });
  await expect(page.getByRole('alert')).toHaveCount(0);
  expect(rendered).toHaveLength(1); // Lost render response is recovered from the frozen server preview.
  expect(await (await request.get('/api/documents')).json()).toHaveLength(0);
  const preview = await (await request.get(`/api/report-tasks/${job.id}/preview`)).json();
  const data = Buffer.from(preview.layout_base64, 'base64');
  const section = strFromU8(unzipSync(data)['Contents/section0.xml']);
  expect(section).toContain('lineBreak'); expect(section).not.toContain('REPORT_SLOT_');
  await expect(page.getByText(/\d+쪽 · 한글 서식 미리보기/)).toBeVisible();
  const reportPages = Number((await page.getByText(/\d+쪽 · 한글 서식 미리보기/).textContent())!.split('쪽')[0]);
  expect(reportPages).toBeGreaterThan(0);
  expect(preview.outline_policy).toBe('short-items-v1');
  expect(preview.plan.filter((p: any) => p.text.startsWith('□ ')).length).toBeGreaterThan(6);
  await page.getByRole('button', { name: '최근 보고서', exact: true }).click();
  await expect(page.locator('.report-history li').first()).toContainText('초안 준비');
  const history = await page.locator('.report-history').boundingBox(), historyButton = await page.locator('.report-history button').first().boundingBox();
  expect(historyButton!.x + historyButton!.width).toBeLessThanOrEqual(history!.x + history!.width + 1);
  await page.screenshot({ path: info.outputPath('actual-report-formatted-preview.png'), fullPage: true });
  await writeFile(info.outputPath('actual-report-formatted-preview.hwpx'), data);
  await page.getByRole('button', { name: '초안 확정·서버 저장', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('저장 완료');
  const href = (await page.getByRole('link', { name: '저장본 다운로드', exact: true }).getAttribute('href'))!;
  const saved = await (await request.get(href)).body();
  await writeFile(info.outputPath('actual-report-saved.hwpx'), saved);
  // A clean save opens the regular editor automatically.
  await expect(page.getByRole('status')).toHaveText('저장 완료');
  await writeFile(info.outputPath('preview-proof.json'), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    source: 'recorded user-generated report', pages: reportPages, render_response_loss_recovered: true, document_created_only_on_save: true, human_approval: false }, null, 2));
});

test("REAL rhwp + MOCK report: live stages, short items and formatted preview after refresh", async ({ page, request }, info) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== '1' || !!process.env.HWP_REPORT_FIXTURES, 'Isolated mock only; no Codex call.');
  let releaseWriting!: () => void, releaseLayout!: () => void;
  const writing = new Promise<void>(r => { releaseWriting = r; }), layout = new Promise<void>(r => { releaseLayout = r; });
  let taskId = '', prepares = 0;
  const before = (await (await request.get('/api/documents')).json()).length;
  await page.route('**/api/report-tasks', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    const response = await route.fetch(), value = await response.json(); taskId = value.id;
    await route.fulfill({ response, json: { ...value, status: 'running', draft: null, draft_sha256: null } });
  });
  await page.route(/\/api\/report-tasks\/[^/]+$/, async route => {
    if (route.request().method() === 'GET') await writing;
    await route.continue();
  });
  await page.route('**/api/report-tasks/*/preview', async route => { if (route.request().method() === 'POST') prepares++; await route.continue(); });
  await page.route('**/api/report-tasks/*/preview/render', async route => {
    const response = await route.fetch(); expect(response.ok()).toBeTruthy();
    await layout; await route.fulfill({ response });
  });
  try {
    const shell = await page.goto('/');
    expect(shell!.headers()['cache-control']).toContain('no-cache');
    await page.getByRole('button', { name: '보고서 작성', exact: true }).click();
    await legacyReport(page);
    await page.getByRole('button', { name: '금융권 해킹 대응', exact: true }).click();
    await page.getByRole('button', { name: '초안 작성하기', exact: true }).click();
    await expect(page.getByText('Codex 응답 준비 중…', { exact: true })).toBeVisible();
    expect(await (await request.get('/api/documents')).json()).toHaveLength(before);
    releaseWriting();
    await page.getByRole('button', { name: '이 내용으로 문서 만들기', exact: true }).click();
    await expect(page.locator('.report-steps[data-stage=applying]')).toBeVisible();
    await expect(page.getByTestId('applied-report-paragraph').first()).toBeVisible();
    await expect(page.locator('.report-steps[data-stage=formatting]')).toBeVisible({ timeout: 60000 });
    await expect(page.getByLabel('작성 중인 문단 미리보기', { exact: true }).locator('.report-draft-content')).toBeVisible();
    await page.screenshot({ path: info.outputPath('live-formatting.png'), fullPage: true });
    expect(await (await request.get('/api/documents')).json()).toHaveLength(before);
    releaseLayout();
    // Automatic TOC now performs native layout passes after rich formatting.
    await expect(page.getByRole('button', { name: '초안 확정·서버 저장', exact: true })).toBeEnabled({ timeout: 90000 });
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.locator('.formatted-report-surface')).toHaveAttribute('data-ready', 'true');
    const preview = await (await request.get(`/api/report-tasks/${taskId}/preview`)).json();
    expect(preview.toc_policy).toBe('report-native-toc-v1');
    expect(preview.toc_verified).toBe(true);
    const complete = await (await request.get(`/api/report-tasks/${taskId}`)).json();
    await expectNativeTitleRules(page, complete.draft.title);
    page.on('dialog', dialog => dialog.accept()); // Test-owned unsaved preview; no user browser affected.
    await page.reload(); await page.getByRole('button', { name: '보고서 작성', exact: true }).click();
    await expect(page.getByRole('button', { name: '초안 확정·서버 저장', exact: true })).toBeVisible({ timeout: 60000 });
    await expect(page.getByRole('alert')).toHaveCount(0);
    expect(prepares).toBe(1);
    expect((await (await request.get(`/api/report-tasks/${taskId}/preview`)).json()).id).toBe(preview.id);
    const rect = await page.locator('.report-editor-slot.active iframe').boundingBox();
    expect(rect!.x).toBeGreaterThanOrEqual(0); expect(rect!.width).toBeGreaterThan(300); expect(rect!.height).toBeGreaterThan(600);
    expect(await (await request.get('/api/documents')).json()).toHaveLength(before);
    await page.screenshot({ path: info.outputPath('restored-formatted-preview.png'), fullPage: true });
    await writeFile(info.outputPath('live-proof.json'), JSON.stringify({ actual_model_calls:0, real_engine:true, human_approval:false,
      stages_observed:['writing','applying','formatting','ready'], toc_verified:preview.toc_verified,
      restored_preview_id:preview.id, prepare_posts:prepares, premature_documents:0 }, null, 2));
  } finally { releaseWriting(); releaseLayout(); await page.unrouteAll({ behavior:'wait' }); }
});

for (const [name, prompt] of [["vendor", "금융회사 외부 업체·클라우드 보안 위험 보고서를 만들어줘."], ["customer", "금융권 고객정보 유출 사고의 고객 보호 대응 보고서를 만들어줘."]]) {
  test(`REAL rhwp + RECORDED Codex report: ${name} approved output and reopen`, async ({ page, request }, info) => {
    test.skip(!process.env.HWP_REPORT_FIXTURES, "Explicitly recorded actual Codex output; no fresh model calls in this browser test.");
    await page.goto("/"); await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
    await legacyReport(page);
    await page.getByLabel("보고서 작성 요청", { exact: true }).fill(prompt);
    const posted = page.waitForResponse(r => r.url().endsWith("/api/report-tasks") && r.request().method() === "POST");
    await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
    const accepted = await (await posted).json();
    await page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true }).click();
    await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeVisible({ timeout: 60000 });
    const task = await (await request.get(`/api/report-tasks/${accepted.id}`)).json();
    expect(task.execution.delivery).toBe("recorded-result-replay");
    await page.screenshot({ path: info.outputPath(`${name}-preview.png`), fullPage: true });
    await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeVisible({ timeout: 60000 });
    await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).click();
    await expect(page.getByRole("link", { name: "저장본 다운로드", exact: true })).toBeVisible({ timeout: 60000 });
    const href = (await page.getByRole("link", { name: "저장본 다운로드", exact: true }).getAttribute("href"))!;
    const bytes = await (await request.get(href)).body();
    const xml = strFromU8(unzipSync(bytes)["Contents/section0.xml"]);
    expect(xml).not.toContain("REPORT_SLOT_"); expect(xml).toContain(task.draft.title);
    await writeFile(info.outputPath(`${name}.hwpx`), bytes);
    const approved = await (await request.get(`/api/report-tasks/${accepted.id}/approved`)).json();
    await writeFile(info.outputPath(`${name}-proof.json`), JSON.stringify({ draft: task.draft, original_execution: task.execution,
      fresh_model_calls: 0, delivery: "recorded-result-replay", engine: "real rhwp", human_approval: false,
      automated_approval: true, exact_commands: (await (await request.get(`/api/report-tasks/${accepted.id}/preview`)).json()).plan.length, output_sha256: approved.output_sha256, saved_version: approved.saved_version }, null, 2));
    // A clean save opens the regular editor automatically.
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    await page.screenshot({ path: info.outputPath(`${name}-reopened.png`), fullPage: true });
  });
}
