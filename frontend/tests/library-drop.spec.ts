import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

async function transfer(page: Page, files: { name: string; bytes: number[] }[]) {
  return page.evaluateHandle(values => {
    const data = new DataTransfer();
    for (const value of values) data.items.add(new File([Uint8Array.from(value.bytes)], value.name, { type: "application/hwp+zip" }));
    return data;
  }, files);
}

test("LIBRARY file drop: creates a separate original-preserving document and rejects invalid or multiple files", async ({ page, request }) => {
  const source = await readFile(new URL("../../samples/paragraph-and-table.hwpx", import.meta.url));
  const name = `드롭 원본 보존 ${Date.now()}.hwpx`;
  await page.goto("/");
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({ name, mimeType: "application/hwp+zip", buffer: source });
  const rows = page.getByRole("row").filter({ has: page.getByRole("button", { name, exact: true }) });
  await expect(rows).toHaveCount(1);
  const originalUrl = (await rows.getByRole("link", { name: "다운로드", exact: true }).getAttribute("href"))!;
  const zone = page.locator(".library-drop-target");
  const data = await transfer(page, [{ name, bytes: Array.from(source) }]);
  await zone.dispatchEvent("dragenter", { dataTransfer: data });
  await expect(zone).toHaveClass(/is-file-dragging/);
  await page.getByRole("region", { name: "HWPX 파일 놓기", exact: true }).dispatchEvent("dragenter", { dataTransfer: data });
  await page.getByRole("region", { name: "HWPX 파일 놓기", exact: true }).dispatchEvent("dragleave", { dataTransfer: data });
  await expect(zone).toHaveClass(/is-file-dragging/);
  const prevented = await zone.evaluate((element, value) => !element.dispatchEvent(new DragEvent("drop", {
    bubbles: true, cancelable: true, dataTransfer: value,
  })), data);
  expect(prevented).toBe(true);
  await expect(zone).not.toHaveClass(/is-file-dragging/);
  await expect(rows).toHaveCount(2);
  const urls = await rows.getByRole("link", { name: "다운로드", exact: true }).evaluateAll(links => links.map(link => link.getAttribute("href")!));
  expect(new Set(urls).size).toBe(2);
  for (const url of urls) {
    expect(await (await request.get(url)).body()).toEqual(source);
    expect(await (await request.get(url.split("/versions/")[0] + "/versions")).json()).toHaveLength(1);
  }
  expect(await (await request.get(originalUrl)).body()).toEqual(source);
  const pageUrl = page.url();
  const uploads: string[] = [];
  page.on("request", value => { if (value.method() === "POST" && new URL(value.url()).pathname === "/api/documents") uploads.push(value.url()); });
  const invalid = await transfer(page, [{ name: "형식 오류.hwp", bytes: [1, 2, 3] }]);
  await zone.dispatchEvent("drop", { dataTransfer: invalid });
  await expect(page.getByRole("alert")).toContainText("HWPX 파일만 업로드");
  const multiple = await transfer(page, [{ name: "첫째.hwpx", bytes: Array.from(source) }, { name: "둘째.hwpx", bytes: Array.from(source) }]);
  await zone.dispatchEvent("drop", { dataTransfer: multiple });
  await expect(page.getByRole("alert")).toContainText("한 번에 HWPX 파일 한 개씩");
  expect(uploads).toHaveLength(0);
  expect(page.url()).toBe(pageUrl);
  await expect(rows).toHaveCount(2);
  await zone.dispatchEvent("dragenter", { dataTransfer: data });
  await page.keyboard.press("Escape");
  await expect(zone).not.toHaveClass(/is-file-dragging/);
  const outsidePrevented = await page.locator(".workspace-nav").evaluate((element, value) => !element.dispatchEvent(new DragEvent("drop", {
    bubbles: true, cancelable: true, dataTransfer: value,
  })), data);
  expect(outsidePrevented).toBe(true);
  expect(uploads).toHaveLength(0);
  await data.dispose(); await invalid.dispose(); await multiple.dispose();
});

test("LIBRARY file drop: lost upload response retries the same bytes and IDs without a second document", async ({ page, request }) => {
  const source = await readFile(new URL("../../samples/paragraph-and-table.hwpx", import.meta.url));
  const name = `드롭 응답 유실 ${Date.now()}.hwpx`;
  await page.goto("/");
  const sent: { id: string | null; key: string | undefined; bytes: Buffer | null }[] = [];
  await page.route("**/api/documents?*", async route => {
    if (route.request().method() !== "POST") return route.continue();
    sent.push({ id: new URL(route.request().url()).searchParams.get("document_id"), key: route.request().headers()["idempotency-key"], bytes: route.request().postDataBuffer() });
    const result = await route.fetch();
    expect(result.ok()).toBeTruthy();
    if (sent.length === 1) return route.abort("failed");
    return route.fulfill({ response: result });
  });
  const data = await transfer(page, [{ name, bytes: Array.from(source) }]);
  await page.locator(".library-drop-target").dispatchEvent("drop", { dataTransfer: data });
  await expect(page.getByRole("button", { name: "업로드 결과 재확인", exact: true })).toBeEnabled();
  await expect(page.getByLabel("HWPX 업로드", { exact: true })).toBeDisabled();
  await page.locator(".library-drop-target").dispatchEvent("drop", { dataTransfer: data });
  await expect(page.getByRole("alert")).toContainText("먼저 보관한 파일의 업로드 결과를 재확인");
  expect(sent).toHaveLength(1);
  await page.getByRole("button", { name: "업로드 결과 재확인", exact: true }).click();
  const uploadSuccess = `${name} 파일을 새 문서로 저장했습니다.`;
  await expect(page.getByRole("status").filter({ hasText: uploadSuccess })).toHaveText(uploadSuccess);
  expect(sent).toHaveLength(2);
  expect(sent[1]).toEqual(sent[0]);
  expect(sent[0].bytes).toEqual(source);
  const documents = await (await request.get(`/api/documents?q=${encodeURIComponent(name)}`)).json();
  expect(documents).toHaveLength(1);
  expect(documents[0].id).toBe(sent[0].id);
  expect(await (await request.get(`/api/documents/${documents[0].id}/versions`)).json()).toHaveLength(1);
  await expect(page.getByLabel("HWPX 업로드", { exact: true })).toBeEnabled();
  await data.dispose();
});
