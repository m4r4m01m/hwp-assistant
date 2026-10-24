import { test, expect, type Locator } from "@playwright/test";

async function expectExpandedNavigation(nav: Locator) {
  await expect(nav).toHaveAttribute("data-mode", "expanded");
  const box = (await nav.boundingBox())!;
  expect(box.width).toBe(220);
  const overflow = await nav.evaluate(element => ({ client: element.clientWidth, scroll: element.scrollWidth }));
  expect(overflow.scroll).toBeLessThanOrEqual(overflow.client);
  const modes = nav.getByRole("group", { name: "사이드바 표시", exact: true }).getByRole("button");
  await expect(modes).toHaveCount(3);
  for (const button of await modes.all()) {
    const mode = (await button.boundingBox())!;
    expect(mode.x).toBeGreaterThanOrEqual(box.x);
    expect(mode.x + mode.width).toBeLessThanOrEqual(box.x + box.width);
  }
  const rows = nav.locator(".workspace-nav-route, .editor-back");
  expect(await rows.count()).toBeGreaterThan(0);
  for (const row of await rows.all()) {
    const icon = (await row.locator(":scope > svg").boundingBox())!;
    const label = (await row.locator(":scope > span").first().boundingBox())!;
    expect(label.x).toBeGreaterThan(icon.x + icon.width);
    expect(Math.abs(label.y + label.height / 2 - icon.y - icon.height / 2)).toBeLessThan(2);
  }
}

for (const viewport of [{ width: 1366, height: 900 }, { width: 400, height: 760 }]) {
  test(`SIDEBAR navigation: shared routes and contained surfaces at ${viewport.width}px`, async ({ page }, info) => {
    await page.setViewportSize(viewport);
    await page.goto("/");
    const nav = page.locator(".workspace-nav");
    await expect(nav).toHaveCount(1);
    await expect(nav.getByRole("button", { name: "문서함", exact: true })).toHaveAttribute("aria-current", "page");
    const expectedNavWidth = viewport.width > 860 ? 220 : 64;
    expect(await nav.boundingBox()).toEqual({ x: 0, y: 0, width: expectedNavWidth, height: viewport.height });
    expect((await page.locator("main").boundingBox())!.x).toBe(expectedNavWidth);
    await expect(page.getByLabel("HWPX 업로드", { exact: true })).toHaveCount(1);
    await nav.getByRole("button", { name: "보고서 작성", exact: true }).click();
    await expect(nav.getByRole("button", { name: "보고서 작성", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByLabel("보고서 작성 요청", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "초안 작성하기", exact: true })).toBeInViewport();
    await page.getByLabel("보고서 작성 요청", { exact: true }).fill("합성 자료 정비 현황 보고서를 작성해 주세요.");
    await expect(page.getByRole("button", { name: "초안 작성하기", exact: true })).toBeEnabled();
    await expect(page.getByRole("link", { name: /기준서.*(원본|사본|다운로드)/ })).toHaveCount(0);
    await page.getByText("양식 관리·연결 정보", { exact: true }).click();
    await page.getByRole("button", { name: "문서작성 기준 열기", exact: true }).click();
    await expect(nav.getByRole("button", { name: "문서 표준 편집", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("tab", { name: "기준서 전체", exact: true })).toBeVisible();
    await nav.getByRole("button", { name: "양식 보관함", exact: true }).click();
    await expect(page.getByRole("heading", { name: "양식 보관함", exact: true })).toBeVisible();
    await expect(nav.getByRole("button", { name: "양식 보관함", exact: true })).toHaveAttribute("aria-current", "page");
    await nav.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
    await expect(nav.getByRole("button", { name: "문서 표준 편집", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("img", { name: "문서 표준 예시 1쪽", exact: true })).toBeVisible({ timeout: 90000 });
    const before = await page.locator(".standard-preview").boundingBox();
    const settingsButton = page.getByRole("button", { name: /문서 표준 설정 (열기|닫기)/ });
    const wasOpen = await settingsButton.getAttribute("aria-expanded") === "true";
    await settingsButton.click();
    await expect(settingsButton).toHaveAttribute("aria-expanded", String(!wasOpen));
    const after = (await page.locator(".standard-preview").boundingBox())!;
    if (viewport.width > 860) expect(wasOpen ? after.width > before!.width : after.width < before!.width).toBeTruthy();
    if (wasOpen) {
      // On a narrow screen the open two-pane layout scrolls horizontally;
      // closing settings must fit the preview, not make its 360px minimum wider.
      await expect(page.getByRole("complementary", { name: "문서 표준 설정", exact: true })).toBeHidden();
      expect(after.x).toBeCloseTo(expectedNavWidth, 1);
      expect(after.width).toBeCloseTo(viewport.width - expectedNavWidth, 1);
      await expect(page.getByRole("img", { name: "문서 표준 예시 1쪽", exact: true })).toBeInViewport({ ratio: 1 });
      await expect(page.getByRole("button", { name: "설정 열기", exact: true })).toBeInViewport();
    }
    if (!wasOpen) {
      const panel = (await page.getByRole("complementary", { name: "문서 표준 설정", exact: true }).boundingBox())!;
      expect(after.x).toBeCloseTo(panel.x + panel.width, 1);
    }
    await page.evaluate(() => window.scrollTo(100000, 100000));
    expect(await page.evaluate(() => ({ x: scrollX, y: scrollY, width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight })))
      .toEqual({ x: 0, y: 0, width: viewport.width, height: viewport.height });
    await page.screenshot({ path: info.outputPath(`sidebar-standard-${viewport.width}.png`), fullPage: true });
    await nav.getByRole("button", { name: "문서함", exact: true }).click();
    await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
    await expect(nav).toHaveCount(1);
  });
}

test("SIDEBAR display mode: persisted choice owns every screen width and can always be restored", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.goto("/");
  const nav = page.locator(".workspace-nav");
  const width = async (expected: number) => {
    await expect.poll(async () => (await nav.boundingBox())?.width).toBe(expected);
    expect(await page.evaluate(() => ({ width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight })))
      .toEqual({ width: 1366, height: 900 });
  };
  await expect(nav).toHaveAttribute("data-mode", "expanded");
  await width(220);
  const documentIcon = await nav.getByRole("button", { name: "문서함", exact: true }).locator("svg").innerHTML();
  await nav.getByRole("button", { name: "사이드바 아이콘만", exact: true }).click();
  await width(64);
  await nav.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await expect(nav).toHaveAttribute("data-mode", "icons");
  await width(64);
  expect((await page.locator(".report-workspace-drawer:not([hidden])").boundingBox())!.x).toBe(64);
  await nav.getByRole("button", { name: "사이드바 펼침", exact: true }).click();
  await width(220);
  expect((await page.locator(".report-workspace-drawer:not([hidden])").boundingBox())!.x).toBe(220);
  await nav.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
  await width(220);
  expect(await nav.getByRole("button", { name: "문서함", exact: true }).locator("svg").innerHTML()).toBe(documentIcon);
  expect((await page.locator(".standard-designer-grid").boundingBox())!.x).toBe(220);
  await nav.getByRole("button", { name: "사이드바 숨기기", exact: true }).click();
  await expect(nav).toHaveAttribute("data-mode", "hidden");
  await width(0);
  expect((await page.locator(".standard-designer-grid").boundingBox())!.x).toBe(0);
  await expect(page.getByRole("button", { name: "사이드바 열기", exact: true })).toBeVisible();
  await page.reload();
  await expect(nav).toHaveAttribute("data-mode", "hidden");
  await width(0);
  await page.getByRole("button", { name: "사이드바 열기", exact: true }).click();
  await width(220);
  await nav.getByRole("button", { name: "양식 보관함", exact: true }).click();
  await width(220);
  await nav.getByRole("button", { name: "문서함", exact: true }).click();
  await page.getByRole("button", { name: "빈 문서 만들기", exact: true }).click();
  await expect(page.locator(".editor-workspace")).toBeVisible();
  await width(220);
  expect((await page.locator(".editor-row").boundingBox())!.x).toBe(220);
  await nav.getByRole("button", { name: "사이드바 아이콘만", exact: true }).click();
  await width(64);
  expect((await page.locator(".editor-row").boundingBox())!.x).toBe(64);
});

test("SIDEBAR narrow desktop: mode controls and route labels remain inside the report and editor rail", async ({ page }, info) => {
  await page.setViewportSize({ width: 876, height: 704 });
  await page.goto("/");
  const nav = page.locator(".workspace-nav");
  await expectExpandedNavigation(nav);
  await nav.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await expect(page.getByLabel("보고서 작성 요청", { exact: true })).toBeVisible();
  await expectExpandedNavigation(nav);
  await page.screenshot({ path: info.outputPath("sidebar-report-876.png"), fullPage: true });
  await nav.getByRole("button", { name: "사이드바 아이콘만", exact: true }).click();
  await expect(nav).toHaveAttribute("data-mode", "icons");
  await nav.getByRole("button", { name: "사이드바 펼침", exact: true }).click();
  await expectExpandedNavigation(nav);
  await nav.getByRole("button", { name: "문서함", exact: true }).click();
  await page.getByRole("button", { name: "빈 문서 만들기", exact: true }).click();
  await expect(page.locator(".editor-workspace")).toBeVisible();
  await expectExpandedNavigation(nav);
  await page.screenshot({ path: info.outputPath("sidebar-editor-876.png"), fullPage: true });
});
