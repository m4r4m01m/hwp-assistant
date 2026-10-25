import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { openTokenLogin } from "../test-support/auth";

// Only synthetic credentials; do not retain password-entry traces regardless
// of defaults inherited from the full editor test configuration.
test.use({ trace: "off", video: "off", screenshot: "off" });

for (const accountsEnabled of [true, false]) {
  test(`ACCOUNT regression: delayed policy resolves before legacy login (accounts=${accountsEnabled})`, async ({ page }) => {
    let release!: () => void, requested!: () => void;
    const policyGate = new Promise<void>(resolve => { release = resolve; });
    const policyRequested = new Promise<void>(resolve => { requested = resolve; });
    let authenticated = false;
    const writes: unknown[] = [];
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/auth/me")) return route.fulfill({ status: authenticated ? 200 : 401,
        json: authenticated ? { user_id: "synthetic-reviewer", authentication_required: true } : { detail: "login" } });
      if (path.endsWith("/auth/status")) {
        requested();
        await policyGate;
        return route.fulfill({ json: { authentication_required: true, accounts_enabled: accountsEnabled,
          registration_enabled: true, guest_enabled: true } });
      }
      if (path.endsWith("/auth/login")) {
        writes.push(route.request().postDataJSON()); authenticated = true;
        return route.fulfill({ json: { user_id: "synthetic-reviewer" } });
      }
      if (path.endsWith("/documents")) return route.fulfill({ json: [] });
      if (path.endsWith("/capabilities")) return route.fulfill({ json: { features: {} } });
      return route.fulfill({ status: 404, json: { detail: "unexpected synthetic path" } });
    });
    await page.route("**/rhwp/build-info.json", route => route.fulfill({ status: 503, body: "offline fixture" }));
    await page.goto("/");
    await policyRequested;
    const opening = openTokenLogin(page);
    try {
      await expect(page.getByText("문서함 연결 중…", { exact: true })).toBeVisible();
      await expect(page.getByRole("heading", { name: "HWPX 문서함 로그인", exact: true })).toHaveCount(0);
      expect(writes).toEqual([]);
    } finally { release(); }
    const input = await opening;
    await input.fill("synthetic-token-not-a-real-credential");
    await page.getByRole("button", { name: "로그인", exact: true }).click();
    await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
    expect(writes).toEqual([{ token: "synthetic-token-not-a-real-credential" }]);
  });
}

test("ACCOUNT regression: browser SRI rejects corrupt warmup bytes, retry recovers without blocking login", async ({ page }) => {
  const correct = Buffer.from("synthetic verified wasm bytes");
  const hash = createHash("sha256").update(correct).digest("hex");
  let corrupt = true, authenticated = false, transfers = 0;
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ status: authenticated ? 200 : 401,
      json: authenticated ? { user_id: "guest", authentication_required: true } : { detail: "login" } });
    if (path.endsWith("/auth/status")) return route.fulfill({ json: {
      authentication_required: true, accounts_enabled: true, registration_enabled: true, guest_enabled: true } });
    if (path.endsWith("/auth/password-login")) {
      authenticated = true;
      return route.fulfill({ json: { user_id: "guest" } });
    }
    if (path.endsWith("/documents")) return route.fulfill({ json: [] });
    if (path.endsWith("/capabilities")) return route.fulfill({ json: { features: {} } });
    return route.fulfill({ status: 404, json: { detail: "unexpected synthetic path" } });
  });
  await page.route("**/rhwp/build-info.json", route => route.fulfill({ json: {
    publicBase: "/rhwp/", bundle: { "assets/core-abcdefgh.wasm": hash } } }));
  await page.route("**/rhwp/assets/core-abcdefgh.wasm", route => {
    transfers++;
    return route.fulfill({ contentType: "application/wasm", body: corrupt ? Buffer.from("tampered bytes") : correct });
  });
  await page.goto("/");
  await expect(page.locator(".engine-warmup")).toContainText("미리 받지 못했습니다");
  await expect(page.locator("iframe")).toHaveCount(0);
  await page.getByRole("button", { name: /guest로 체험/ }).click();
  await page.getByLabel("비밀번호", { exact: true }).fill("synthetic-password");
  await expect(page.getByRole("button", { name: "로그인", exact: true })).toBeEnabled();
  corrupt = false;
  await page.getByRole("button", { name: "편집 자원 미리 받기", exact: true }).click();
  await expect(page.locator(".engine-warmup")).toContainText("다운로드 완료");
  expect(transfers).toBe(2);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain("synthetic-password");
});
