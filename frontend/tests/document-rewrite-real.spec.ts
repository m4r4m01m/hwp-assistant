import { test, expect, type Page, type Frame } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

// Actual editor, API/DB, exact native commands and server HWPX preservation
// verification. Only inference is a deterministic, opt-in synthetic fixture.
test.skip(process.env.HWP_E2E_DOCUMENT_REWRITE !== "1", "Requires the isolated document-rewrite inference fixture");
test.setTimeout(180000);
const digest = (value: Uint8Array) => createHash("sha256").update(value).digest("hex");
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (window !== window.top) return;
    const interval = window.setInterval.bind(window);
    window.setInterval = ((handler: TimerHandler, delay?: number, ...args: any[]) =>
      interval(delay === 20000 ? () => {} : handler, delay, ...args)) as typeof window.setInterval;
  });
});
async function frame(page: Page): Promise<Frame> {
  const handle = await page.locator(".block-editor-slot.active iframe").elementHandle();
  return (await handle!.contentFrame())!;
}
async function bytes(page: Page) {
  return Buffer.from(await (await frame(page)).evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
}
async function texts(page: Page, raw: Uint8Array) {
  const xml = strFromU8(unzipSync(raw)["Contents/section0.xml"]);
  return page.evaluate(value => Array.from(new DOMParser().parseFromString(value, "application/xml").documentElement.children)
    .map(p => Array.from(p.getElementsByTagNameNS("*", "t")).map(t => t.textContent).join("")), xml);
}
async function open(page: Page) {
  const original = await (await page.request.get("/api/test/document-rewrite-fixture")).body();
  const name = `전체 문장 수정 합성 ${Date.now()}.hwpx`;
  await page.goto("/");
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({ name, mimeType: "application/hwp+zip", buffer: original });
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name, exact: true }) });
  const originalUrl = (await row.getByRole("link", { name: "다운로드", exact: true }).getAttribute("href"))!;
  await row.getByRole("button", { name, exact: true }).click();
  await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  const frozen = await bytes(page);
  await page.getByRole("button", { name: "문서 AI 대화 열기", exact: true }).click();
  await page.getByLabel("대화 이름", { exact: true }).fill("본문 전체 간결화");
  await page.getByRole("button", { name: "AI와 대화 시작", exact: true }).click();
  const assistant = page.getByRole("region", { name: "문서와 AI 대화", exact: true });
  await expect(assistant.getByLabel("AI 수정 범위", { exact: true })).toHaveValue("document");
  await assistant.getByLabel("AI에게 요청", { exact: true }).fill("전체 문단 문장을 짧고 간결한 보고서 문체로 다듬어줘.");
  await expect(page.getByRole("region", { name: "문단 수정 후보 기록", exact: true })).toBeHidden();
  await page.getByRole("button", { name: "메모", exact: true }).click();
  await page.getByLabel("대화 메시지", { exact: true }).fill("미저장 합성 메모");
  await page.getByRole("button", { name: "AI 대화", exact: true }).click();
  await expect(assistant.getByLabel("AI에게 요청", { exact: true })).toHaveValue("전체 문단 문장을 짧고 간결한 보고서 문체로 다듬어줘.");
  await expect(page.getByRole("region", { name: "작성 정보·검토 메모", exact: true })).toBeHidden();
  await page.screenshot({ path: test.info().outputPath("compact-assistant.png"), fullPage: true });
  // Clear the intentionally unsaved memo before the later navigation check.
  await page.getByRole("button", { name: /^메모/ }).click();
  await expect(page.getByLabel("대화 메시지", { exact: true })).toHaveValue("미저장 합성 메모");
  await page.getByLabel("대화 메시지", { exact: true }).fill("");
  await page.getByRole("button", { name: "AI 대화", exact: true }).click();
  return { name, original, originalUrl, frozen, documentUrl: originalUrl.split("/versions/")[0], assistant };
}

test("REAL rhwp + MOCK AI: whole-document exact changes preserve bold leads, titles, table and note; explicit apply/save/reopen", async ({ page, request }, info) => {
  const source = await open(page);
  const accepted = page.waitForResponse(response => response.request().method() === "POST" && /\/assistant-turns$/.test(response.url()));
  await source.assistant.getByRole("button", { name: "보내기", exact: true }).click();
  const response = await accepted;
  expect(response.ok(), await response.text()).toBeTruthy();
  const input = response.request().postDataJSON(), turnId = (await response.json()).id;
  expect(input.rewrite_context.paragraphs).toHaveLength(4);
  expect(input.rewrite_context.paragraphs.slice(0, 2).every((p: any) => p.fixed_prefix && p.text.startsWith(p.fixed_prefix))).toBe(true);
  expect(input.rewrite_context.paragraphs.slice(2).every((p: any) => p.protected_text.length === 2)).toBe(true);
  expect(input.rewrite_context.paragraphs.every((p: any) => p.rendered_lines > 0)).toBe(true);
  expect(input.rewrite_context.source_sha256).toBe(digest(source.frozen));
  const preview = page.getByRole("dialog", { name: "문서 전체 수정 전후 비교", exact: true });
  await expect(preview.getByRole("img", { name: "수정 전 실제 문서", exact: true })).toBeVisible({ timeout: 90000 });
  await expect(preview.getByRole("img", { name: "수정 후 실제 문서", exact: true })).toBeVisible();
  await expect(preview.getByRole("button", { name: "문서에 적용 · 저장 전", exact: true })).toBeEnabled();
  expect(await bytes(page)).toEqual(source.frozen);
  expect(await (await request.get(source.documentUrl + "/versions")).json()).toHaveLength(1);
  const turn = await (await request.get(`/api/assistant-turns/${turnId}`)).json();
  expect(turn.execution.actual_model_calls).toBe(0);
  expect(turn.document_rewrites).toHaveLength(4);
  await preview.getByLabel("수정한 문단", { exact: true }).selectOption("2");
  await expect(preview.getByText("AS-IS · 수정 전 2쪽", { exact: true })).toBeVisible();
  await expect(preview.locator(".rewrite-preview-text")).toContainText("실제");
  await expect(preview.locator(".rewrite-text-pair")).toContainText("취약 버전주1)");
  await page.screenshot({ path: info.outputPath("whole-document-before-after.png"), fullPage: true });
  await preview.getByRole("button", { name: "문서에 적용 · 저장 전", exact: true }).click();
  await expect(preview).toHaveCount(0);
  const applied = await bytes(page), before = await texts(page, source.frozen), after = await texts(page, applied);
  const changed = new Map<number, string>(turn.document_rewrites.map((p: any) => [p.paragraph, p.replacement]));
  expect(after).toEqual(before.map((value, index) => changed.get(index) ?? value));
  expect(after[3]).toContain("30개"); expect(after[4]).toContain("10월 30일");
  expect(after[6]).toBe("주1) 본문 수치와 담당 부서는 합성 검수 자료임.");
  expect(await (await request.get(source.originalUrl)).body()).toEqual(source.original);
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료");
  expect(await (await request.get(source.documentUrl + "/versions")).json()).toHaveLength(2);
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  await page.getByRole("row").filter({ has: page.getByRole("button", { name: source.name, exact: true }) }).getByRole("button", { name: source.name, exact: true }).click();
  await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  expect(await texts(page, await bytes(page))).toEqual(after);
  expect(await (await request.get(source.originalUrl)).body()).toEqual(source.original);
  await writeFile(info.outputPath("whole-document-proof.json"), JSON.stringify({ engine: "real rhwp", actual_model_calls: 0,
    changed_paragraphs: 4, bold_leads_preserved: true, inline_emphasis_superscript_preserved: true, title_table_note_preserved: true, original_blob_preserved: true,
    explicit_preview_apply_and_save: true, reopened: true, source_sha256: digest(source.frozen), output_sha256: digest(applied) }, null, 2));
});

test("REAL rhwp + MOCK AI: a later unsaved edit blocks whole-document preview without discarding the edit", async ({ page, request }) => {
  const source = await open(page);
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let fetched!: () => void;
  const accepted = new Promise<void>(resolve => { fetched = resolve; });
  await page.route("**/api/threads/*/assistant-turns", async route => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await route.fetch(); fetched(); await held;
    return route.fulfill({ response });
  });
  await source.assistant.getByRole("button", { name: "보내기", exact: true }).click();
  await accepted;
  await (await frame(page)).getByRole("textbox", { name: "문서 편집 입력", exact: true }).focus();
  await page.keyboard.press("Control+End"); await page.keyboard.insertText(" 후속 미저장 설명");
  const edited = await bytes(page);
  expect(edited).not.toEqual(source.frozen);
  release();
  const preview = page.getByRole("dialog", { name: "문서 전체 수정 전후 비교", exact: true });
  await expect(preview.getByRole("alert")).toContainText(/바뀌었|다릅니다|현재 문서/, { timeout: 30000 });
  await expect(preview.getByRole("button", { name: "문서에 적용 · 저장 전", exact: true })).toBeDisabled();
  await preview.getByRole("button", { name: "비교 닫기", exact: true }).click();
  expect(await bytes(page)).toEqual(edited);
  expect(await (await request.get(source.documentUrl + "/versions")).json()).toHaveLength(1);
  expect(await (await request.get(source.originalUrl)).body()).toEqual(source.original);
});
