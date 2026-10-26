import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("REAL engine restore retries a lost download without publishing twice or changing the open original", async ({ page, request }) => {
  test.setTimeout(180000);
  const bytes = await readFile(new URL("../../samples/paragraph-and-table.hwpx", import.meta.url));
  const name = `복원 재열기 검증 ${crypto.randomUUID()}.hwpx`;
  const created = await request.post(`/api/documents?name=${encodeURIComponent(name)}&document_id=${crypto.randomUUID()}`, { data: bytes, headers: { "Idempotency-Key": crypto.randomUUID() } });
  expect(created.ok()).toBeTruthy();
  const document = await created.json();
  const lease = await (await request.post(`/api/documents/${document.id}/session`)).json();
  const saved = await request.post(`/api/documents/${document.id}/versions?base_version=${document.current_version}&cause=web-edit`, { data: bytes, headers: { "X-Edit-Session": lease.token, "Idempotency-Key": crypto.randomUUID() } });
  expect(saved.ok()).toBeTruthy();
  const second = await saved.json();
  await request.delete(`/api/documents/${document.id}/session`, { headers: { "X-Edit-Session": lease.token } });
  await page.goto("/");
  await page.getByRole("button", { name, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  const handle = await page.locator(".block-editor-slot.active iframe").elementHandle(), original = await handle!.contentFrame();
  const before = await original!.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes));
  let fail = true, posts = 0;
  page.on("request", req => { if (req.method() === "POST" && req.url().includes("/restore?")) posts++; });
  await page.route(`**/api/documents/${document.id}/versions/*/file`, async route => {
    const url = route.request().url();
    if (fail && !url.includes(document.current_version) && !url.includes(second.id)) { fail = false; await route.abort("failed"); }
    else await route.continue();
  });
  await page.getByLabel("파일 작업", { exact: true }).click();
  await page.getByRole("button", { name: "버전 기록", exact: true }).click();
  page.on("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "이 버전 복원" }).last().click();
  await expect(page.getByRole("dialog").getByRole("alert")).toBeVisible();
  expect(await original!.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes))).toEqual(before);
  expect(posts).toBe(1);
  await page.getByRole("button", { name: "이 버전 복원" }).last().click();
  await expect(page.getByText(`${name}의 v1에서 복원한 새 버전을 저장했습니다.`)).toBeVisible({ timeout: 90000 });
  expect(posts).toBe(1);
  const versions = await (await request.get(`/api/documents/${document.id}/versions`)).json();
  expect(versions).toHaveLength(3);
  expect(versions[0].restore_source.version_id).toBe(document.current_version);
  const restored = await request.get(`/api/documents/${document.id}/versions/${versions[0].id}/file`);
  expect(await restored.body()).toEqual(bytes);
  await page.getByRole("button", { name: "닫기", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
});
