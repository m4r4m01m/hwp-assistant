import { test, expect, type Page } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

test.skip(process.env.HWP_E2E_ASSISTANT_TABLE_LAYOUT !== "1", "Requires isolated synthetic table-layout inference");
test.setTimeout(120000);
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (window !== window.top) return;
    const interval = window.setInterval.bind(window);
    window.setInterval = ((handler: TimerHandler, delay?: number, ...args: any[]) =>
      interval(delay === 20000 ? () => {} : handler, delay, ...args)) as typeof window.setInterval;
  });
});
async function bytes(page: Page) {
  const host = await page.locator(".block-editor-slot.active iframe").elementHandle();
  const frame = await host!.contentFrame();
  return Buffer.from(await frame!.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
}
async function texts(page: Page, raw: Buffer) {
  return page.evaluate(xml => Array.from(new DOMParser().parseFromString(xml, "application/xml").documentElement.children)
    .map(p => Array.from(p.getElementsByTagNameNS("*", "t")).map(t => t.textContent).join("")), strFromU8(unzipSync(raw)["Contents/section0.xml"]));
}

for (const tableOnly of [false, true]) test(`REAL rhwp + MOCK AI: ${tableOnly ? "table only" : "table and prose"} preview, apply, save and reopen`, async ({ page, request }, info) => {
  const fixture = await request.get("/api/test/assistant-table-fixture");
  expect(fixture.ok(), await fixture.text()).toBeTruthy();
  const original = await fixture.body();
  const name = `표 배치 합성 검수 ${Date.now()}.hwpx`;
  await page.goto("/");
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({ name, mimeType: "application/hwp+zip", buffer: original });
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name, exact: true }) });
  const originalUrl = (await row.getByRole("link", { name: "다운로드", exact: true }).getAttribute("href"))!;
  await row.getByRole("button", { name, exact: true }).click();
  await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  const before = await bytes(page);
  await page.getByRole("button", { name: "문서 AI 대화 열기", exact: true }).click();
  await page.getByLabel("대화 이름", { exact: true }).fill("표 배치와 문장 축약");
  await page.getByRole("button", { name: "AI와 대화 시작", exact: true }).click();
  await page.getByLabel("AI에게 요청", { exact: true }).fill(`${tableOnly ? "표만" : "문단도 많이 줄이고 표도"} 상위 기호 반만큼 들여쓰고 표 제목 아래 2mm 간격을 넣어줘.`);
  const inspection = page.waitForRequest(r => r.url().endsWith("/assistant-layout-context"));
  const accepted = page.waitForResponse(r => /\/threads\/[^/]+\/assistant-turns$/.test(r.url()) && r.request().method() === "POST");
  await page.getByRole("button", { name: "보내기", exact: true }).click();
  const read = await inspection, response = await accepted;
  expect(response.ok(), await response.text()).toBeTruthy();
  const input = response.request().postDataJSON(), turnId = (await response.json()).id;
  expect(input.rewrite_context.table_targets).toHaveLength(1);
  const comparison = page.getByRole("dialog", { name: "문서 전체 수정 전후 비교", exact: true });
  await expect(comparison.getByRole("button", { name: "문서에 적용 · 저장 전", exact: true })).toBeEnabled({ timeout: 90000 });
  await expect(comparison.getByRole("img", { name: "수정 후 실제 문서", exact: true })).toBeVisible();
  await expect(comparison).toContainText("표 1개 배치 변경");
  await expect(comparison.getByLabel("수정한 문단").locator("option:checked")).toContainText("표 · 시스템별 조치 계획");
  expect(await bytes(page)).toEqual(before);
  const turn = await (await request.get(`/api/assistant-turns/${turnId}`)).json();
  expect(turn.document_rewrites).toHaveLength(tableOnly ? 0 : 3);
  expect(turn.table_layout).toEqual({ parent_half: true, caption_gap_mm: 2 });
  await page.screenshot({ path: info.outputPath("table-layout-comparison.png"), fullPage: true });
  await comparison.getByRole("button", { name: "문서에 적용 · 저장 전", exact: true }).click();
  await expect(comparison).toHaveCount(0);
  const applied = await bytes(page), beforeText = await texts(page, before), afterText = await texts(page, applied);
  const replacements = new Map<number, string>(turn.document_rewrites.map((p: any) => [p.paragraph, p.replacement]));
  expect(afterText).toEqual(beforeText.map((value, index) => replacements.get(index) ?? value));
  const layout = await request.post(read.url(), { headers: { "X-Edit-Session": read.headers()["x-edit-session"] },
    data: { base_version: input.base_version, source_base64: applied.toString("base64") } });
  expect(layout.ok(), await layout.text()).toBeTruthy();
  const table = (await layout.json()).table_targets[0];
  expect(table.left_mm).toBe(table.parent_half_mm); expect(table.caption_gap_mm).toBe(2);
  expect(table.width_mm).toBeLessThan(input.rewrite_context.table_targets[0].width_mm);
  expect(await (await request.get(originalUrl)).body()).toEqual(original);
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료");
  const versions = await (await request.get(originalUrl.split("/versions/")[0] + "/versions")).json();
  expect(versions).toHaveLength(2);
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  await page.getByRole("button", { name, exact: true }).click();
  await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  expect(await texts(page, await bytes(page))).toEqual(afterText);
  await writeFile(info.outputPath("proof.json"), JSON.stringify({ actual_model_calls: 0, real_engine: true, tableOnly, table,
    paragraphs_changed: turn.document_rewrites.length, original_preserved: true, saved_versions: versions.length, reopened: true }, null, 2));
});
