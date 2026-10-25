import { expect, type Page } from "@playwright/test";

/** Wait for /auth/me and /auth/status before choosing the login UI.
 * isVisible() alone is an immediate snapshot, not a readiness wait.
 * Shared by real deployment tests and delayed-response regression tests.
 */
export async function openTokenLogin(page: Page) {
  await expect(page.getByRole("heading", { name: "HWPX 문서함 로그인", exact: true })).toBeVisible();
  const input = page.getByLabel("인증 토큰", { exact: true });
  if (!(await input.isVisible())) {
    await page.getByText("기존 인증 토큰으로 로그인", { exact: true }).click();
    await page.getByRole("button", { name: "토큰 입력 화면", exact: true }).click();
  }
  await expect(input).toBeVisible();
  return input;
}
