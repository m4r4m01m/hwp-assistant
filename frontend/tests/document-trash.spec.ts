import { test, expect, type Route } from "@playwright/test";

const respond = (route: Route, value: unknown, status = 200) => route.fulfill({ status,
  contentType: "application/json", body: JSON.stringify(value) });

test.beforeEach(async ({ page }) => {
  await page.route("**/document-trash-test.html*", route => route.fulfill({ contentType: "text/html",
    body: '<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/document-trash.tsx"></script></body></html>' }));
});

test("MOCK trash: an uncertain deletion retries the same receipt before closing", async ({ page }) => {
  const keys: string[] = [];
  await page.route("**/api/documents/synthetic-doc", async route => {
    expect(route.request().method()).toBe("DELETE");
    keys.push(route.request().headers()["idempotency-key"]);
    return keys.length === 1 ? respond(route, { detail: "합성 응답 유실" }, 503) : respond(route, { id: "synthetic-doc" });
  });
  await page.goto("/document-trash-test.html?mode=delete");
  const dialog = page.getByRole("dialog", { name: "휴지통으로 이동" });
  await expect(dialog).toContainText("버전 기록은 보존");
  await dialog.getByRole("button", { name: "휴지통으로 이동", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("서버에서 요청을 처리하지 못했습니다");
  await expect(page.getByLabel("목록 갱신 횟수")).toHaveText("0");
  await dialog.getByRole("button", { name: "휴지통으로 이동", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(keys).toHaveLength(2); expect(keys[0]).toBeTruthy(); expect(keys[1]).toBe(keys[0]);
  await expect(page.getByLabel("목록 갱신 횟수")).toHaveText("1");
});

test("MOCK trash: restore failure keeps its row, success refreshes the library", async ({ page }) => {
  let restores = 0;
  await page.route("**/api/document-trash", route => respond(route, [{ id: "synthetic-doc", name: "합성 문서.hwpx",
    current_version: "v1", created_at: 1, role: "owner", trash_id: "receipt-1", deleted_at: 2 }]));
  await page.route("**/api/document-trash/receipt-1/restore", route => {
    expect(route.request().method()).toBe("POST"); restores++;
    return restores === 1 ? respond(route, { detail: "합성 연결 오류" }, 503) : respond(route, { id: "synthetic-doc" });
  });
  await page.goto("/document-trash-test.html");
  const restore = page.getByRole("button", { name: "합성 문서.hwpx 복구" });
  await restore.click();
  await expect(page.getByRole("alert")).toContainText("서버에서 요청을 처리하지 못했습니다");
  await expect(restore).toBeEnabled();
  await restore.click();
  await expect(restore).toHaveCount(0);
  await expect(page.getByRole("dialog").getByRole("status")).toContainText("복구했습니다");
  await expect(page.getByLabel("목록 갱신 횟수")).toHaveText("1");
});
