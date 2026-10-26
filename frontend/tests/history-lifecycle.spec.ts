import { test, expect } from "@playwright/test";
const first = { id: "v1", document_id: "document", base_version: null, size: 123, sha256: "a".repeat(64), created_at: 1, cause: "upload", author: null };
const second = { ...first, id: "v2", base_version: "v1", created_at: 2, cause: "web-edit" };
test("structure details open next to their version, show progress and ignore stale replies", async ({ page }) => {
  await page.route("**/api/documents/document/versions", route => route.fulfill({ json: [second, first] }));
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/versions/v2/changes", async route => { await delayed; await route.fulfill({ json: { version: second, recorded: true, entries: [{ path: "Contents/section0.xml", before_sha256: "a", after_sha256: "b" }] } }); });
  await page.route("**/versions/v1/changes", route => route.fulfill({ json: { version: first, recorded: false, entries: [] } }));
  await page.goto("/tests/fixtures/version-history.html");
  await page.getByRole("button", { name: "내부 구조 상세" }).first().click();
  await expect(page.getByText("변경한 파일을 확인하는 중…")).toBeVisible();
  await page.getByRole("button", { name: "내부 구조 상세" }).last().click();
  release();
  await expect(page.getByText("v1 내부 구조 상세", { exact: true })).toBeVisible();
  await expect(page.getByText("이 버전에는 내부 변경 기록이 없습니다.", { exact: false })).toBeVisible();
  await expect(page.getByText("Contents/section0.xml")).toHaveCount(0);
});
test("restoration stays in history and identifies the source version", async ({ page }) => {
  let restored = false;
  const third = { ...first, id: "v3", base_version: "v2", created_at: 3, cause: "restore", restore_source: { version_id: "v1", created_at: 1, author: null } };
  await page.route("**/api/documents/document/versions", route => route.fulfill({ json: restored ? [third, second, first] : [second, first] }));
  await page.route("**/versions/v1/restore", async route => { restored = true; await route.fulfill({ json: third }); });
  page.on("dialog", dialog => dialog.accept());
  await page.goto("/tests/fixtures/version-history.html");
  await page.getByRole("button", { name: "이 버전 복원" }).last().click();
  await expect(page.getByText("합성 보고서.hwpx의 v1에서 복원한 새 버전을 저장했습니다.")).toBeVisible();
  await expect(page.getByText("이 문서의 v1에서 복원", { exact: false })).toBeVisible();
  await expect(page.locator('[data-version-id="v3"]')).toContainText("현재");
});

test("pending restoration cannot close its session and a failed response keeps the request id", async ({ page }) => {
  const keys: string[] = [];
  let release!: () => void;
  const delayed = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/documents/document/versions", route => route.fulfill({ json: [second, first] }));
  await page.route("**/versions/v1/restore", async route => {
    keys.push(route.request().headers()["idempotency-key"]);
    if (keys.length === 1) {
      await delayed;
      return route.fulfill({ status: 503, json: { detail: "합성 일시 오류" } });
    }
    return route.fulfill({ json: { ...second, id: "v3", cause: "restore" } });
  });
  page.on("dialog", dialog => dialog.accept());
  await page.goto("/tests/fixtures/version-history.html");
  await page.getByRole("button", { name: "이 버전 복원" }).last().click();
  await expect(page.getByRole("button", { name: "닫기", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeVisible();
  release();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "이 버전 복원" }).last().click();
  await expect(page.getByText("합성 보고서.hwpx의 v1에서 복원한 새 버전을 저장했습니다.")).toBeVisible();
  expect(keys).toHaveLength(2); expect(keys[0]).toBeTruthy(); expect(keys[1]).toBe(keys[0]);
});
