import { test, expect, type Page, type Route } from "@playwright/test";

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

const ownDocument = { id: "owned-doc", name: "내 합성 문서.hwpx", current_version: "v1", created_at: 2, role: "owner", can_trash: true };
const sharedDocument = { id: "shared-doc", name: "공유받은 합성 문서.hwpx", current_version: "v2", created_at: 1, role: "editor", can_trash: false };

async function mockLibrary(page: Page, shared = false) {
  let deleted = false, deletions = 0, restores = 0;
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/status") return respond(route, { accounts_enabled: false });
    if (path === "/api/auth/me") return respond(route, { user_id: shared ? "guest" : "synthetic-owner",
      authentication_required: false, library_access_mode: shared ? "shared" : "private" });
    if (path === "/api/capabilities") return respond(route, { features: {} });
    if (path === "/api/documents") return respond(route, shared ? deleted ? [] : [ownDocument] : [ownDocument, sharedDocument]);
    if (path === "/api/documents/owned-doc" && route.request().method() === "DELETE") {
      deletions++; deleted = true; return respond(route, { id: ownDocument.id, trash_id: "synthetic-receipt" });
    }
    if (path === "/api/document-trash") return respond(route, deleted ? [{ ...ownDocument, trash_id: "synthetic-receipt", deleted_at: 3 }] : []);
    if (path === "/api/document-trash/synthetic-receipt/restore") {
      restores++; deleted = false; return respond(route, ownDocument);
    }
    return respond(route, { detail: `Unexpected synthetic request: ${path}` }, 404);
  });
  await page.route("**/rhwp/build-info.json", route => route.fulfill({ status: 503, body: "offline synthetic fixture" }));
  return { deletions: () => deletions, restores: () => restores };
}

test("MOCK library trash: direct delete is visible with private permission guidance and fits narrow layouts", async ({ page }, info) => {
  const calls = await mockLibrary(page);
  await page.goto("/");
  const allowed = page.getByRole("button", { name: `${ownDocument.name} 삭제`, exact: true });
  const denied = page.getByRole("button", { name: `${sharedDocument.name} 삭제`, exact: true });
  await expect(allowed).toBeVisible(); await expect(allowed).toBeEnabled();
  await expect(denied).toBeVisible(); await expect(denied).toBeDisabled();
  await expect(denied).toHaveAttribute("aria-description", "개인 문서함에서는 문서 소유자만 삭제할 수 있습니다.");
  await expect(denied.locator("..")).toHaveAttribute("title", "개인 문서함에서는 문서 소유자만 삭제할 수 있습니다.");
  for (const width of [1024, 760]) {
    await page.setViewportSize({ width, height: 900 });
    const rect = (await allowed.boundingBox())!;
    expect(rect.x).toBeGreaterThanOrEqual(0);
    expect(rect.x + rect.width).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    const table = page.locator(".library-table-wrap"), tableBounds = (await table.boundingBox())!;
    expect(await table.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    for (const action of await page.locator(".library-document-actions button").all()) {
      const actionBounds = (await action.boundingBox())!;
      expect(actionBounds.x + actionBounds.width).toBeLessThanOrEqual(tableBounds.x + tableBounds.width);
    }
  }
  await page.screenshot({ path: info.outputPath("direct-delete-760.png") });
  await page.getByRole("button", { name: `${ownDocument.name} 더보기`, exact: true }).click();
  await expect(page.getByRole("group", { name: `${ownDocument.name} 추가 작업` }).getByRole("button", { name: /삭제|휴지통/ })).toHaveCount(0);
  await allowed.click();
  const dialog = page.getByRole("dialog", { name: "휴지통으로 이동", exact: true });
  await expect(dialog).toBeVisible(); expect(calls.deletions()).toBe(0);
  await dialog.getByRole("button", { name: "취소", exact: true }).click();
  await expect(dialog).toHaveCount(0); expect(calls.deletions()).toBe(0);
});

test("MOCK library trash: shared delete confirmation and restore explain the common library", async ({ page }) => {
  const calls = await mockLibrary(page, true);
  await page.goto("/");
  const remove = page.getByRole("button", { name: `${ownDocument.name} 삭제`, exact: true });
  await remove.click();
  const confirmation = page.getByRole("dialog", { name: "휴지통으로 이동", exact: true });
  await expect(confirmation).toContainText("모든 공유 사용자가 열 수 없습니다");
  await expect(confirmation).toContainText("원본과 버전 기록은 보존");
  expect(calls.deletions()).toBe(0);
  await confirmation.getByRole("button", { name: "휴지통으로 이동", exact: true }).click();
  await expect(remove).toHaveCount(0); expect(calls.deletions()).toBe(1);
  await page.getByRole("button", { name: "휴지통", exact: true }).click();
  const trash = page.getByRole("dialog", { name: "휴지통", exact: true });
  await expect(trash).toContainText("공용 문서함에서 삭제한 문서가 표시됩니다");
  await expect(trash).toContainText("공유 사용자 모두에게 문서가 다시 표시됩니다");
  await expect(trash).not.toContainText("내가 소유한 문서만");
  await trash.getByRole("button", { name: `${ownDocument.name} 복구`, exact: true }).click();
  await expect(trash.getByRole("status")).toContainText("복구했습니다"); expect(calls.restores()).toBe(1);
  await trash.getByRole("button", { name: "휴지통 닫기", exact: true }).click();
  await expect(remove).toBeVisible(); await expect(remove).toBeEnabled();
});
