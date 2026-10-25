import { test, expect, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

const sha = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
async function engine(page: Page) {
  const handle = await page.locator(".report-editor-slot.active iframe").elementHandle();
  const frame = await handle?.contentFrame();
  if (!frame) throw new Error("활성 보고서 편집기를 찾지 못했습니다.");
  return { handle: handle!, frame };
}
async function expectViewport(page: Page, withEditor: boolean, panelName?: string) {
  const viewport = page.viewportSize()!;
  await page.evaluate(() => window.scrollTo(100000, 100000));
  expect(await page.evaluate(() => ({
    x: window.scrollX, y: window.scrollY,
    width: document.documentElement.scrollWidth,
    height: document.documentElement.scrollHeight,
    bodyHeight: document.body.scrollHeight,
  }))).toEqual({ x: 0, y: 0, width: viewport.width, height: viewport.height, bodyHeight: viewport.height });
  const canvas = (await page.locator(".report-workspace-canvas").boundingBox())!;
  const navigationElement = page.getByRole("navigation", { name: "보고서 작업 도구", exact: true, includeHidden: true });
  const hiddenNavigation = await navigationElement.getAttribute("data-mode") === "hidden";
  if (hiddenNavigation) await expect(navigationElement).toBeHidden();
  else await expect(navigationElement).toBeVisible();
  const navigation = await navigationElement.evaluate(element => {
    const { x, y, width, height } = element.getBoundingClientRect();
    return { x, y, width, height };
  });
  if (hiddenNavigation) expect(navigation.width).toBe(0);
  let left = navigation.x + navigation.width;
  let panelBounds: typeof canvas | null = null;
  if (panelName) {
    const panel = page.getByRole("complementary", { name: panelName, exact: true });
    await expect(panel).toBeVisible();
    const bounds = (await panel.boundingBox())!;
    panelBounds = bounds;
    expect(bounds.x).toBeCloseTo(navigation.x + navigation.width, 1);
    expect(bounds.y).toBe(0);
    expect(bounds.height).toBe(viewport.height);
    expect(bounds.width).toBeGreaterThan(0);
    expect(bounds.width).toBeLessThan(viewport.width - navigation.width);
    left = bounds.x + bounds.width;
    await expect(page.locator(".report-workspace-drawer:visible")).toHaveCount(1);
  } else await expect(page.locator(".report-workspace-drawer:visible")).toHaveCount(0);
  expect(canvas.x).toBeCloseTo(left, 1);
  expect(canvas.y).toBe(0);
  expect(canvas.width).toBeCloseTo(viewport.width - left, 1);
  expect(canvas.height).toBe(viewport.height);
  if (withEditor) expect(await page.locator(".report-editor-slot.active iframe").boundingBox()).toEqual(canvas);
  return { viewport, navigation, panel: panelName || null, panel_bounds: panelBounds, canvas };
}

test("REAL rhwp + MOCK report workspace: full height editor, local drawers and preserved save/reopen", async ({ page, request }, info) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== "1" || !!process.env.HWP_REPORT_FIXTURES,
    "Synthetic report generation; actual rhwp layout and save, no model call.");
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await page.getByRole("button", { name: "사이드바 펼침", exact: true }).click();
  const requestedNavWidth = (await page.getByRole("navigation", { name: "보고서 작업 도구", exact: true }).boundingBox())!.width;
  await expect(page.getByLabel("문서 양식", { exact: true })).toBeEnabled();
  await page.getByLabel("문서 양식", { exact: true }).selectOption(""); // Exercise the existing legacy TOC contract explicitly.
  const prompt = "[RICH] 전체 높이 보고서 편집 화면과 도구 전환 보존 확인";
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill(prompt);
  await page.getByLabel("공개 웹 자료를 검색하고 출처 포함").uncheck();
  await expectViewport(page, false, "보고서 작성 요청 도구");
  await page.getByRole("button", { name: "작성 요청 도구 닫기", exact: true }).click();
  await expectViewport(page, false);
  await page.getByRole("button", { name: "작성 요청", exact: true }).click();
  await expect(page.getByLabel("보고서 작성 요청", { exact: true })).toHaveValue(prompt);
  await expect(page.getByLabel("공개 웹 자료를 검색하고 출처 포함")).not.toBeChecked();
  const posted = page.waitForResponse(response => response.url().endsWith("/api/report-tasks") && response.request().method() === "POST");
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  const id = (await (await posted).json()).id as string;
  await page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true }).click({ timeout: 90000 });
  await expect(page.locator(".formatted-report-surface")).toHaveAttribute("data-ready", "true", { timeout: 90000 });
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeEnabled();
  expect((await page.getByRole("navigation", { name: "보고서 작업 도구", exact: true }).boundingBox())!.width).toBe(requestedNavWidth);
  await expectViewport(page, true);
  const initial = await engine(page);
  const output = async () => Uint8Array.from(await initial.frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  const before = await output();
  await initial.frame.getByRole("textbox", { name: "문서 편집 입력", exact: true }).focus();
  await page.keyboard.press("Control+End");
  const edited = " 화면 도구 전환 뒤에도 보존하는 편집 내용";
  await page.keyboard.insertText(edited);
  await expect.poll(async () => strFromU8(unzipSync(await output())["Contents/section0.xml"])).toContain(edited);
  const editedHash = sha(await output());
  expect(editedHash).not.toBe(sha(before));
  const layoutEvidence: Awaited<ReturnType<typeof expectViewport>>[] = [];
  const expectPreserved = async () => {
    expect(await page.locator(".report-editor-slot.active iframe").evaluate((current, original) => current === original, initial.handle)).toBe(true);
    expect(sha(await output())).toBe(editedHash);
  };
  for (const mode of ["사이드바 아이콘만", "사이드바 숨기기", "사이드바 열기"]) {
    if (mode === "사이드바 열기") {
      await page.mouse.move(2, page.viewportSize()!.height / 2);
      await expect(page.getByRole("button", { name: mode, exact: true })).toHaveCSS("opacity", "1");
    }
    await page.getByRole("button", { name: mode, exact: true }).click();
    layoutEvidence.push(await expectViewport(page, true));
    await expectPreserved();
  }
  const panels = [
    { button: "작성 요청", name: "보고서 작성 요청 도구", close: "작성 요청 도구 닫기" },
    { button: "결과·출처", name: "보고서 결과와 출처", close: "보고서 결과와 출처 닫기" },
    { button: "초안 상태와 진행 정보", name: "초안 상태와 진행 정보", close: "초안 상태 닫기" },
  ];
  for (const viewport of [{ width: 1366, height: 900 }, { width: 800, height: 760 }]) {
    await page.setViewportSize(viewport);
    layoutEvidence.push(await expectViewport(page, true));
    for (const panel of panels) {
      await page.getByRole("button", { name: panel.button, exact: true }).click();
      layoutEvidence.push(await expectViewport(page, true, panel.name));
      if (panel.button === "작성 요청") {
        await expect(page.getByLabel("보고서 작성 요청", { exact: true })).toHaveValue(prompt);
        await expect(page.getByLabel("공개 웹 자료를 검색하고 출처 포함")).not.toBeChecked();
        const drawer = page.getByRole("complementary", { name: panel.name, exact: true });
        const inputs = drawer.locator(".report-input");
        await inputs.evaluate(node => { node.scrollTop = node.scrollHeight; });
        expect(await inputs.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
        await expect(drawer.getByRole("button", { name: "초안 작성하기", exact: true })).toBeInViewport();
        await expectViewport(page, true, panel.name);
      }
      if (panel.button === "초안 상태와 진행 정보")
        await expect(page.getByLabel("초안 목차 상태", { exact: true })).toContainText("갱신 필요");
      await expectPreserved();
      await page.screenshot({ path: info.outputPath(`report-${viewport.width}-${panel.button === "작성 요청" ? "request" : panel.button === "결과·출처" ? "content" : "status"}-reflow.png`), fullPage: true });
      await page.getByRole("button", { name: panel.close, exact: true }).click();
      layoutEvidence.push(await expectViewport(page, true));
      await expectPreserved();
    }
    // Switching panels must use the same sidebar track, rather than stack a
    // second panel over the native editor or remount its document iframe.
    for (const panel of panels) {
      await page.getByRole("button", { name: panel.button, exact: true }).click();
      layoutEvidence.push(await expectViewport(page, true, panel.name));
      await expectPreserved();
    }
    await page.getByRole("button", { name: "초안 상태 닫기", exact: true }).click();
    layoutEvidence.push(await expectViewport(page, true));
    await expectPreserved();
  }
  await page.screenshot({ path: info.outputPath("report-editor-full-height.png"), fullPage: true });
  await page.setViewportSize({ width: 400, height: 760 });
  layoutEvidence.push(await expectViewport(page, true));
  await expectPreserved();
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.getByRole("button", { name: "목차 쪽수 갱신", exact: true }).click();
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeEnabled({ timeout: 90000 });
  await expect(page.getByLabel("초안 목차 상태", { exact: true })).toContainText("검증 완료");
  await expectViewport(page, true);
  const current = await engine(page);
  const bytes = Uint8Array.from(await current.frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const href = (await page.getByRole("link", { name: "저장본 다운로드", exact: true }).getAttribute("href"))!;
  expect(sha(await (await request.get(href)).body())).toBe(sha(bytes));
  // A clean save opens the regular editor automatically.
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const reopened = page.frames().find(candidate => candidate.url().includes("/rhwp/"))!;
  expect(sha(Uint8Array.from(await reopened.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[])))).toBe(sha(bytes));
  await writeFile(info.outputPath("report-workspace-proof.json"), JSON.stringify({
    real_engine: true, actual_model_calls: 0, synthetic_generation: true, human_review: false,
    full_height_viewports: ["1366x900", "800x760", "400x760"], outer_scroll: 0,
    sidebar_reflow_viewports: ["1366x900", "800x760"], panels: panels.map(panel => panel.name), layout_evidence: layoutEvidence,
    iframe_preserved_across_tools: true, prompt_preserved: true,
    explicit_sidebar_width_preserved_after_layout: requestedNavWidth,
    sidebar_modes_preserve_unsaved_document: true,
    manual_edit_preserved_across_tools: true, manual_edit_sha256: editedHash,
    saved_sha256: sha(bytes), report_task_created: !!id,
  }, null, 2));
});
