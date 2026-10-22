import { test, expect, type Page } from "@playwright/test";
import { startConversation } from "./conversation-helpers";
import { readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";

const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
async function openSample(page: Page) {
  const source = await readFile(new URL("../../samples/paragraph-and-table.hwpx", import.meta.url));
  const name = `편집 화면 검증 ${Date.now()} 긴 문서명에서도 저장과 도구가 가려지지 않는지 확인.hwpx`;
  await page.goto("/");
  await page.getByRole("button", { name: "사이드바 아이콘만", exact: true }).click();
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({ name, mimeType: "application/hwp+zip", buffer: source });
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name, exact: true }) });
  const savedUrl = (await row.getByRole("link", { name: "다운로드", exact: true }).getAttribute("href"))!;
  await page.getByRole("button", { name, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  const frame = page.frames().find(candidate => candidate.url().includes("/rhwp/"))!;
  return { name, source, savedUrl, frame };
}

async function expectFullHeightEditor(page: Page) {
  const viewport = page.viewportSize()!;
  const frame = (await page.locator(".block-editor-slot.active iframe").boundingBox())!;
  const side = page.locator(".editor-row > aside:visible");
  const panel = await side.count() ? (await side.boundingBox())! : null;
  const left = panel ? panel.x + panel.width : 64;
  if (panel) { expect(panel.x).toBe(64); expect(panel.y).toBe(0); expect(panel.height).toBe(viewport.height); }
  expect(frame.x).toBeCloseTo(left, 1);
  expect(frame.y).toBe(0);
  expect(frame.width).toBeCloseTo(viewport.width - left, 1);
  expect(frame.height).toBe(viewport.height);
  await page.evaluate(() => window.scrollTo(100000, 100000));
  const outer = await page.evaluate(() => ({
    x: window.scrollX, y: window.scrollY,
    width: document.documentElement.scrollWidth,
    height: document.documentElement.scrollHeight,
    bodyHeight: document.body.scrollHeight,
  }));
  expect(outer).toEqual({ x: 0, y: 0, width: viewport.width, height: viewport.height, bodyHeight: viewport.height });
}

test("REAL rhwp workspace: side rail gives the editor full height and keeps file actions in the viewport", async ({ page, request }, info) => {
  await page.setViewportSize({ width: 862, height: 692 });
  const state = await openSample(page);
  const initial = await state.frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]);
  await expectFullHeightEditor(page);
  await expect(page.getByRole("button", { name: "저장", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "저장본 다운로드", exact: true })).toHaveAttribute("href", state.savedUrl);
  await page.getByLabel("파일 작업", { exact: true }).click();
  await expect(page.getByRole("button", { name: "다른 이름으로 저장", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "다른 이름으로 저장", exact: true })).toBeHidden();
  await expect(page.getByLabel("파일 작업", { exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "다른 이름으로 저장", exact: true })).toBeVisible();
  await page.getByLabel("편집 안내 및 진단", { exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".editor-menu[open]")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "다른 이름으로 저장", exact: true })).toBeHidden();
  await expect(page.locator(".editor-help-content")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByLabel("파일 작업", { exact: true }).click();
  const recovery = page.waitForEvent("download");
  await page.getByRole("button", { name: "복구용 다운로드", exact: true }).click();
  const download = await recovery;
  expect(hash(await readFile((await download.path())!))).toBe(hash(Uint8Array.from(initial)));
  await page.getByLabel("파일 작업", { exact: true }).click();
  await page.getByRole("button", { name: "버전 기록", exact: true }).click();
  await expect(page.locator(".modal")).toContainText("버전 기록");
  await page.locator(".modal").getByRole("button", { name: "닫기", exact: true }).click();
  await page.getByLabel("파일 작업", { exact: true }).click();
  page.once("dialog", dialog => dialog.accept(`화면 검증 사본 ${Date.now()}.hwpx`));
  const copyResult = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/documents");
  await page.getByRole("button", { name: "다른 이름으로 저장", exact: true }).click();
  expect((await copyResult).ok()).toBeTruthy();
  await page.getByLabel("문서 정보", { exact: true }).click();
  await expect(page.locator(".editor-document strong")).toHaveText(state.name);
  await expect(page.locator(".editor-document")).toContainText("저장 완료");
  await expect(page.getByRole("link", { name: "저장본 다운로드", exact: true })).toHaveAttribute("href", state.savedUrl);
  expect(await (await request.get(state.savedUrl)).body()).toEqual(state.source);
  expect(await (await request.get(state.savedUrl.split("/versions/")[0] + "/versions")).json()).toHaveLength(1);
  await page.getByLabel("편집 안내 및 진단", { exact: true }).click();
  await expect(page.locator(".editor-help-content")).toContainText("20초 간격 자동 저장");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.locator(".editor-help-content")).toBeHidden();
  await expectFullHeightEditor(page);
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  await page.getByLabel("편집 안내 및 진단", { exact: true }).click();
  await state.frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await expect(page.locator(".editor-help-content")).toBeHidden();
  await page.screenshot({ path: info.outputPath("workspace-862.png"), fullPage: true });
  await page.setViewportSize({ width: 400, height: 760 });
  await expectFullHeightEditor(page);
  await expect(page.getByRole("button", { name: "저장", exact: true })).toBeInViewport();
  await page.getByLabel("파일 작업", { exact: true }).click();
  await expect(page.getByRole("button", { name: "복구용 다운로드", exact: true })).toBeInViewport();
  await page.mouse.move(25, 150);
  await page.mouse.wheel(0, 5000);
  await expectFullHeightEditor(page);
  await page.screenshot({ path: info.outputPath("workspace-400.png"), fullPage: true });
});

test("REAL rhwp workspace: one visible panel preserves unsent conversation and writing context across tools", async ({ page }, info) => {
  await page.setViewportSize({ width: 862, height: 692 });
  const state = await openSample(page);
  const before = await state.frame.evaluate(() => ({ state: (window as any).hwpAssistantBridge.documentState(),
    bytes: Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[] }));
  await page.getByRole("button", { name: "문서 AI 대화 열기", exact: true }).click();
  await startConversation(page, "합성 화면 검증 대화");
  await page.getByText("추가 작성 정보 · 선택 사항", { exact: true }).click();
  await page.getByRole("textbox", { name: "작성 목적", exact: true }).fill("아직 저장하지 않은 화면 검증 입력");
  await page.getByRole("textbox", { name: "대화 메시지", exact: true }).fill("아직 전송하지 않은 대화 입력");
  await page.getByLabel("추가 도구", { exact: true }).click();
  await page.getByRole("button", { name: "AI 검토 열기", exact: true }).click();
  await expect(page.getByLabel("문단 AI 검토 패널", { exact: true })).toBeVisible();
  await expect(page.locator(".editor-row > aside:visible")).toHaveCount(1);
  await page.getByRole("button", { name: "문서 AI 대화 열기", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "작성 목적", exact: true })).toHaveValue("아직 저장하지 않은 화면 검증 입력");
  await expect(page.getByRole("textbox", { name: "대화 메시지", exact: true })).toHaveValue("아직 전송하지 않은 대화 입력");
  await page.getByRole("button", { name: "문서 AI 대화 닫기", exact: true }).click();
  await page.getByRole("alertdialog", { name: "미저장 입력 확인", exact: true }).getByRole("button", { name: "계속 편집", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "작성 목적", exact: true })).toHaveValue("아직 저장하지 않은 화면 검증 입력");
  await expect(page.getByLabel("문단 AI 검토 패널", { exact: true })).toHaveCount(0);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "작성 정보 편집 취소", exact: true }).click();
  // A message alone must also keep the close guard, even after context is reset.
  await page.getByRole("button", { name: "문서 AI 대화 닫기", exact: true }).click();
  await page.getByRole("alertdialog", { name: "미저장 입력 확인", exact: true }).getByRole("button", { name: "계속 편집", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "대화 메시지", exact: true })).toHaveValue("아직 전송하지 않은 대화 입력");
  for (const button of ["AI 검토 열기", "목차 관리", "블록 삽입 열기", "문서 AI 대화 열기"]) {
    if (button === "AI 검토 열기") await page.getByLabel("추가 도구", { exact: true }).click();
    await page.getByRole("button", { name: button, exact: true }).click();
    await expect(page.locator(".editor-row > aside:visible")).toHaveCount(1);
    await expectFullHeightEditor(page);
    const visiblePanel = (await page.locator(".editor-row > aside:visible").boundingBox())!;
    expect(visiblePanel.y).toBe(0);
    expect(visiblePanel.height).toBe(692);
    expect(visiblePanel.x).toBe(64);
  }
  await page.setViewportSize({ width: 1366, height: 768 });
  await expectFullHeightEditor(page);
  expect((await page.locator(".editor-row > aside:visible").boundingBox())!.height).toBe(768);
  await page.setViewportSize({ width: 400, height: 760 });
  await expectFullHeightEditor(page);
  await expect(page.getByRole("textbox", { name: "작성 목적", exact: true })).toHaveValue("");
  await expect(page.getByRole("textbox", { name: "대화 메시지", exact: true })).toHaveValue("아직 전송하지 않은 대화 입력");
  await page.setViewportSize({ width: 862, height: 692 });
  const after = await state.frame.evaluate(() => ({ state: (window as any).hwpAssistantBridge.documentState(),
    bytes: Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[] }));
  expect(after.state).toEqual(before.state);
  expect(hash(Uint8Array.from(after.bytes))).toBe(hash(Uint8Array.from(before.bytes)));
  await page.screenshot({ path: info.outputPath("workspace-single-panel.png"), fullPage: true });
  await writeFile(info.outputPath("workspace-proof.json"), JSON.stringify({ engine: "real rhwp", actual_model_calls: 0,
    panels_visible: 1, unsent_message_preserved_across_tools: true, unsaved_context_preserved_when_cancelled: true, document_unchanged: true,
    editor_top: 0, editor_height: 692, rail_width: 64, outer_scroll: { x: 0, y: 0 },
    sha256: hash(Uint8Array.from(after.bytes)), viewport: { width: 862, height: 692 } }, null, 2));
});
