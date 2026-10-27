import { test, expect, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { randomUUID, randomBytes, createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { checkCoedit } from "./coedit-check";

const base = "https://localhost:24443/hwp-assistant/";

function createDisposableAdministrator(envPath: string, user: string, password: string) {
  // This helper must never run against a home/self-hosted deployment. The only
  // accepted stack is the existing disposable PoC workflow's generated env.
  if (process.env.GITHUB_ACTIONS !== "true" || process.env.RUNNER_ENVIRONMENT !== "github-hosted"
      || process.env.HWP_ACCOUNT_DEPLOY_TEST !== "1" || envPath !== process.env.HWP_ENV_FILE
      || !/^\/tmp\/hwp-poc-[0-9]+\/production\.env$/.test(envPath)) {
    throw new Error("Account bootstrap is restricted to the disposable hosted PoC stack");
  }
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const script = [
    "import json,os,sys",
    "from app.account_admin import maintain",
    "from app.db import make_engine",
    "value=json.load(sys.stdin)",
    "engine=make_engine(os.environ['DATABASE_URL'])",
    "try:",
    "    maintain(engine,'create',value['user'],password=value['password'],name='Synthetic CI administrator',employee=value['employee'],role='admin')",
    "except Exception:",
    "    raise SystemExit('Disposable administrator fixture failed') from None",
    "finally:",
    "    engine.dispose()",
  ].join("\n");
  try {
    execFileSync("docker", ["compose", "--env-file", envPath, "-f", "compose.deploy.yaml", "-f", "compose.ci-gateway.yaml",
      "exec", "-T", "api", "python", "-c", script], {
      cwd: root, timeout: 30000, stdio: ["pipe", "ignore", "ignore"],
      // Secrets travel only through stdin, never command arguments, stdout,
      // artifacts, traces, environment additions, or a committed password.
      input: JSON.stringify({ user, password, employee: "TEST-ADMIN-" + randomUUID().slice(0, 8) }),
    });
  } catch {
    throw new Error("Disposable administrator fixture initialization failed");
  }
}

async function savedFile(page: Page) {
  const href = await page.getByRole("link", { name: "저장본 다운로드", exact: true }).getAttribute("href");
  expect(href).toBeTruthy();
  const response = await page.request.get(new URL(href!, base).href);
  expect(response.ok()).toBe(true);
  const bytes = await response.body();
  expect(bytes.length).toBeGreaterThan(0);
  return { href: href!, sha256: createHash("sha256").update(bytes).digest("hex") };
}

async function reopen(page: Page, expected: { href: string; sha256: string }) {
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  const response = await page.request.get(base + "api/documents");
  expect(response.ok()).toBe(true);
  const documents = await response.json();
  const doc = documents.find((item: { id: string }) => expected.href.includes(`/documents/${item.id}/`));
  expect(doc).toBeTruthy();
  await page.getByRole("button", { name: doc.name, exact: true }).click();
  await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  expect(await savedFile(page)).toEqual(expected);
  const frame = page.frames().find(frame => frame.url().includes("/rhwp/index.html"))!;
  const evidence = await frame.evaluate(() => {
    const snapshot = (window as any).hwpAssistantBridge.exportSnapshot();
    return { length: snapshot.bytes.length, losses: snapshot.contentLoss.count };
  });
  expect(evidence.length).toBeGreaterThan(0);
  expect(evidence.losses).toBe(0);
}

// No routes, fake engine, trace or video. Synthetic credentials remain private
// in RUNNER_TEMP; only non-secret assertions and an editor screenshot are uploaded.
test("ACCOUNTS: real PostgreSQL approval, isolated documents, save/reopen and warm WASM reuse", async ({ page, context, request }, info) => {
  test.skip(process.env.HWP_ACCOUNT_DEPLOY_TEST !== "1", "Explicit disposable account integration only");
  const envPath = process.env.HWP_DEPLOY_ENV_FILE!;
  const administrator = "ciadmin-" + randomUUID().slice(0, 8);
  const adminPassword = randomBytes(24).toString("hex");
  createDisposableAdministrator(envPath, administrator, adminPassword);
  const env = readFileSync(envPath, "utf8");
  const password = env.split(/\r?\n/).find(line => line.startsWith("HWP_GUEST_PASSWORD="))!.split("=").slice(1).join("=");
  const warm: string[] = [];
  page.on("request", request => { if (request.url().endsWith(".wasm")) warm.push(request.url()); });
  await page.goto(base);
  await expect(page.getByRole("button", { name: /guest로 체험/ })).toBeVisible();
  await expect(page.locator(".engine-warmup")).toContainText("다운로드 완료", { timeout: 90000 });
  expect(warm.length).toBe(1);
  await expect(page.locator("iframe")).toHaveCount(0);
  await page.getByRole("button", { name: /guest로 체험/ }).click();
  await page.getByLabel("비밀번호", { exact: true }).fill(password);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("button", { name: "빈 문서 만들기", exact: true }).click();
  const save = page.getByRole("button", { name: "저장", exact: true });
  await expect(save).toBeEnabled({ timeout: 90000 });
  await save.click();
  await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료");
  const frame = page.frames().find(frame => frame.url().includes("/rhwp/index.html"))!;
  const entries = await frame.evaluate(() => performance.getEntriesByType("resource").filter(entry => entry.name.endsWith(".wasm")).map(entry => ({
    name: entry.name, transferSize: (entry as PerformanceResourceTiming).transferSize, encodedBodySize: (entry as PerformanceResourceTiming).encodedBodySize })));
  expect(entries.length).toBeGreaterThan(0);
  expect(entries.every(entry => entry.name === warm[0])).toBe(true);
  expect(entries.every(entry => entry.transferSize === 0)).toBe(true);
  const guest = await savedFile(page);
  await page.screenshot({ path: info.outputPath("guest-real-editor.png") });
  await reopen(page, guest);
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(page.getByRole("button", { name: /계정 만들기/ })).toBeVisible();
  expect((await page.request.get(new URL(guest.href, base).href)).status()).toBe(401);
  await page.getByRole("button", { name: /계정 만들기/ }).click();
  const user = "ci-" + randomUUID().slice(0, 8);
  await page.getByLabel("아이디", { exact: true }).fill(user);
  await page.getByLabel("비밀번호", { exact: true }).fill(password);
  await page.getByLabel("비밀번호 확인", { exact: true }).fill(password);
  await page.getByLabel("이름", { exact: true }).fill("합성 검수자");
  await page.getByLabel("사번", { exact: true }).fill("TEST-" + randomUUID().slice(0, 8));
  await page.getByRole("button", { name: "가입 승인 요청", exact: true }).click();
  await expect(page.getByRole("heading", { name: "가입 승인을 기다리고 있습니다", exact: true })).toBeVisible();
  expect((await page.request.get(base + "api/auth/me")).status()).toBe(401);
  expect((await page.request.get(base + "api/documents")).status()).toBe(401);
  const pendingLogin = await page.request.post(base + "api/auth/password-login", { data: { username: user, password } });
  expect(pendingLogin.status()).toBe(403);
  expect((await context.cookies()).some(cookie => cookie.name === "hwp_session")).toBe(false);

  // Independent API cookie jar: approving never logs the applicant browser in
  // as admin or shares administrator credentials with the restart receipt.
  const adminLogin = await request.post(base + "api/auth/password-login", { data: { username: administrator, password: adminPassword } });
  expect(adminLogin.status()).toBe(200);
  const adminMe = await request.get(base + "api/auth/me");
  expect((await adminMe.json()).is_admin).toBe(true);
  const pendingAccounts = await request.get(base + "api/admin/accounts?status=pending");
  expect(pendingAccounts.status()).toBe(200);
  const pending = (await pendingAccounts.json()).items.find((item: { user_id: string }) => item.user_id === user);
  expect(pending).toBeTruthy();
  const approval = await request.post(base + `api/admin/accounts/${user}/approve`, { data: { expected_revision: pending.revision } });
  expect(approval.status()).toBe(200);
  expect((await approval.json()).approval_status).toBe("approved");
  expect((await page.request.get(base + "api/auth/me")).status()).toBe(401);
  expect((await request.post(base + "api/auth/logout")).status()).toBe(200);

  await page.getByRole("button", { name: "로그인 화면으로", exact: true }).click();
  await expect(page.getByLabel("아이디", { exact: true })).toHaveValue(user);
  await page.getByLabel("비밀번호", { exact: true }).fill(password);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
  const me = await page.request.get(base + "api/auth/me");
  const identity = await me.json();
  expect(identity.user_id).toBe(user);
  expect(identity.is_admin).toBe(false);
  expect(identity.library_access_mode).toBe("private");
  expect((await page.request.get(base + "api/admin/accounts")).status()).toBe(403);
  expect((await page.request.get(base + "api/documents")).ok()).toBe(true);
  expect(await (await page.request.get(base + "api/documents")).json()).toEqual([]);
  expect((await page.request.get(new URL(guest.href, base).href)).status()).toBe(404);
  await page.getByRole("button", { name: "빈 문서 만들기", exact: true }).click();
  await expect(save).toBeEnabled({ timeout: 90000 });
  await save.click();
  await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료");
  const member = await savedFile(page);
  expect(member.href).not.toBe(guest.href);
  await reopen(page, member);
  await checkCoedit(page, context, base);
  const cookie = (await context.cookies()).find(cookie => cookie.name === "hwp_session")!;
  expect(cookie.httpOnly && cookie.secure).toBe(true);
  expect(cookie.path).toBe("/hwp-assistant/api");
  // Private restart receipt is deliberately NOT an uploaded artifact.
  writeFileSync(process.env.HWP_ACCOUNT_RECEIPT!, JSON.stringify({ cookie: cookie.value, user, guest, member, approval_required: true, approved: true }), { mode: 0o600 });
  writeFileSync(info.outputPath("accounts-proof.json"), JSON.stringify({ real_engine: true, real_postgres: true,
    model_calls: 0, signup: true, pending_access_denied: true, administrator_approval: true, member_admin_access_denied: true, guest_document_isolation: true, guest_and_member_reopened: true, wasm: entries }, null, 2));
});
