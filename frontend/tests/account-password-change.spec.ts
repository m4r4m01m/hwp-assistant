import { test, expect, type Page } from "@playwright/test";

// Synthetic credentials stay out of screenshots, video and traces.
test.use({ trace: "off", video: "off", screenshot: "off" });
async function fixture(page: Page, enabled = true) {
  await page.route("**/password-test.html*", route => route.fulfill({ contentType: "text/html", body: '<html lang="ko"><meta charset="utf-8"><div id="root"></div><script type="module" src="/tests/fixtures/account-password-change.tsx"></script></html>' }));
  await page.goto(`/password-test.html?enabled=${enabled}`);
}
async function fill(page: Page, confirm = "synthetic-next-password") {
  await page.getByLabel("현재 비밀번호", { exact: true }).fill("synthetic-current-password");
  await page.getByLabel("새 비밀번호", { exact: true }).fill("synthetic-next-password");
  await page.getByLabel("새 비밀번호 확인", { exact: true }).fill(confirm);
}

test("password change validates confirmation, locks submission, resets secrets on success", async ({ page }) => {
  let requests = 0, release: (() => void) | undefined;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/auth/password-change", async route => { requests++; await pending; await route.fulfill({ json: { user_id: "synthetic-member" } }); });
  await fixture(page);
  await page.getByRole("button", { name: "비밀번호 변경", exact: true }).click();
  await fill(page, "synthetic-mismatch");
  await page.getByRole("button", { name: "변경", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("일치하지 않습니다");
  expect(requests).toBe(0);
  await page.getByLabel("새 비밀번호 확인", { exact: true }).fill("synthetic-next-password");
  await page.getByRole("button", { name: "변경", exact: true }).click();
  await expect(page.getByRole("button", { name: "취소", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeVisible();
  release!();
  await expect(page.getByRole("status")).toContainText("비밀번호를 변경했습니다");
  expect(requests).toBe(1);
  await page.getByRole("button", { name: "닫기", exact: true }).click();
  await page.getByRole("button", { name: "비밀번호 변경", exact: true }).click();
  await expect(page.getByLabel("현재 비밀번호", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("새 비밀번호", { exact: true })).toHaveValue("");
});

test("password change errors keep the form and explain unknown completion", async ({ page }) => {
  await page.route("**/api/auth/password-change", route => route.fulfill({ status: 503, json: { detail: "연결을 확인하세요." } }));
  await fixture(page);
  await page.getByRole("button", { name: "비밀번호 변경", exact: true }).click();
  await fill(page);
  await page.getByRole("button", { name: "변경", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("새 비밀번호로 다시 로그인");
  await page.getByRole("button", { name: "취소", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("password change is hidden without personal password capability", async ({ page }) => {
  await fixture(page, false);
  await expect(page.getByRole("button", { name: "비밀번호 변경", exact: true })).toHaveCount(0);
});
