import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

test("HTTPS subpath login, real Studio and immutable comparison renderer", async ({ page, context }) => {
  const base = process.env.HWP_DEPLOY_TEST_URL || "https://localhost:24443/hwp-assistant/";
  const envFile = process.env.HWP_DEPLOY_ENV_FILE;
  if (!envFile) throw Error("Set HWP_DEPLOY_ENV_FILE to the private CI env file");
  const line = readFileSync(envFile, "utf8").split(/\r?\n/).find(s => s.startsWith("HWP_AUTH_TOKENS="));
  const users = JSON.parse(line!.split("=").slice(1).join("=").replace(/^'|'$/g, ""));
  const token = Object.values(users)[0] as string;
  const receipt = JSON.parse(readFileSync(resolve("../.deploy/smoke-receipt.json"), "utf8"));
  const escaped: string[] = [], errors: string[] = [];
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.origin === new URL(base).origin && /^\/(?:api(?:\/|$)|rhwp\/|assets\/|comparison-|compare-renderer)/.test(url.pathname))
      escaped.push(url.pathname);
  });
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(base);
  await page.getByLabel("인증 토큰").fill(token);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
  const cookie = (await context.cookies()).find(value => value.name === "hwp_session");
  expect(cookie?.path).toBe("/hwp-assistant/api");
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.secure).toBe(true);
  expect(cookie?.value).not.toBe(token);

  await page.getByRole("button", { name: "deployment-smoke-synthetic.hwpx", exact: true }).click();
  const currentDocument = page.getByLabel("현재 문서와 저장 상태", { exact: true });
  await expect(currentDocument).toContainText("deployment-smoke-synthetic.hwpx");
  // The bridge is installed before WASM and the requested document are ready.
  // Wait for the application's completed load, not merely the bridge object.
  await expect(currentDocument.getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  await expect(page.getByRole("link", { name: "저장본 다운로드", exact: true }))
    .toHaveAttribute("href", `${new URL(base).pathname}api/documents/${receipt.id}/versions/${receipt.version}/file`);
  await expect.poll(() => page.frames().some(frame => frame.url().includes("/hwp-assistant/rhwp/index.html"))).toBe(true);
  const frame = page.frames().find(value => value.url().includes("/hwp-assistant/rhwp/index.html"))!;
  await frame.waitForFunction(() => !!(window as any).hwpAssistantBridge);
  const bytes = await frame.evaluate(() => {
    const result = (window as any).hwpAssistantBridge.exportSnapshot();
    return { length: result.bytes.length, losses: result.contentLoss.count };
  });
  expect(bytes.length).toBeGreaterThan(0);
  expect(bytes.losses).toBe(0);

  const response = await page.request.get(`${base}api/documents/${receipt.id}/versions/${receipt.version}/file`);
  expect(response.ok()).toBe(true);
  const stored = [...await response.body()];
  await page.goto(base + "compare-renderer.html");
  const result = await page.evaluate(async ({ bytes, hash }) => {
    const renderer = (window as any).hwpComparisonRenderer;
    await renderer.ready;
    const fontsBefore = performance.getEntriesByType("resource").filter(entry => entry.name.includes("/rhwp/fonts/")).length;
    const opened = await renderer.open(new Uint8Array(bytes), hash);
    await renderer.render(0);
    const svg = document.querySelector("#page svg");
    return {
      ...opened, fontsBefore,
      fontsAfter: performance.getEntriesByType("resource").filter(entry => entry.name.includes("/rhwp/fonts/")).length,
      fontErrors: [...document.fonts].filter(font => font.status === "error").length,
      visible: !!svg && getComputedStyle(svg).visibility === "visible" && svg.getBoundingClientRect().width > 0,
    };
  }, { bytes: stored, hash: receipt.sha256 });
  expect(result.pages).toBeGreaterThan(0);
  expect(result.fontsBefore).toBe(0);
  expect(result.fontsAfter).toBeGreaterThan(0);
  expect(result.fontsAfter).toBeLessThan(14);
  expect(result.fontErrors).toBe(0);
  expect(result.visible).toBe(true);
  expect(escaped).toEqual([]);
  expect(errors).toEqual([]);
  await page.goto(base);
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(page.getByRole("heading", { name: "HWPX 문서함 로그인" })).toBeVisible();
});
