import { test, expect, type Page } from "@playwright/test";

// Credentials are synthetic. Account/password forms must not produce artifacts.
test.use({ trace: "off", video: "off", screenshot: "off" });
const pending = { user_id: "applicant", name: "합성 신청자", kind: "member", is_admin: false, is_active: false,
  approval_status: "pending", status: "pending", revision: "synthetic-revision-pending", created_at: 1793000000, last_login_at: null };
const admin = { user_id: "admin", name: "관리자", kind: "member", is_admin: true, is_active: true,
  approval_status: "approved", status: "approved", revision: "synthetic-revision-admin", created_at: 1793000000, last_login_at: 1793001000 };
const guest = { ...admin, user_id: "guest", name: "체험", kind: "guest", is_admin: false, revision: "synthetic-revision-guest" };

async function fixture(page: Page, options: { isAdmin?: boolean; denyAdmin?: boolean } = {}) {
  const writes: { path: string; method: string; body: any }[] = [], reads: string[] = [];
  let applicant = { ...pending }, policy = "allowlist", notification = { configured: true, pending: 1, failed: 2 };
  await page.route("**/api/**", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    const reply = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (path.endsWith("/auth/me")) return reply({ user_id: options.isAdmin === false ? "member" : "admin", authentication_required: true, is_admin: options.isAdmin !== false });
    if (path.endsWith("/documents")) return reply([]);
    if (path.endsWith("/capabilities")) return reply({ features: {} });
    if (path.includes("/admin/")) {
      if (options.denyAdmin) return reply({ detail: "관리자 권한이 필요합니다." }, 403);
      if (request.method() !== "GET") writes.push({ path, method: request.method(), body: request.postDataJSON() });
      else reads.push(url.pathname + url.search);
      if (path.endsWith("/admin/accounts")) {
        const all = [applicant, admin, guest], status = url.searchParams.get("status");
        const items = status ? all.filter(row => row.status === status) : all;
        return reply({ items, total: items.length });
      }
      if (path.endsWith("/applicant/approve")) { applicant = { ...applicant, is_active: true, approval_status: "approved", status: "approved", revision: "approved-revision" }; return reply(applicant); }
      if (path.endsWith("/applicant/reject")) { applicant = { ...applicant, approval_status: "rejected", status: "rejected", revision: "rejected-revision" }; return reply(applicant); }
      if (path.endsWith("/password")) return reply({ ok: true });
      if (path.endsWith("/admin/settings")) { if (request.method() === "PATCH") policy = request.postDataJSON().codex_account_access; return reply({ codex_account_access: policy, mattermost: notification }); }
      if (path.endsWith("/admin/notifications/retry")) { notification = { configured: true, pending: 3, failed: 0 }; return reply({ queued: 2 }); }
      if (path.endsWith("/admin/audit")) return reply({ items: [{ id: 41, created_at: 1793000000, actor_id: url.searchParams.get("actor") || "guest", action: "document.modify", target_type: "document", target_id: "synthetic-document", document_id: "synthetic-document", version_id: "synthetic-version", document_name: "검수용 문서.hwpx" }], next_before: null });
    }
    return reply({ detail: "unexpected synthetic path" }, 404);
  });
  await page.route("**/rhwp/build-info.json", route => route.fulfill({ status: 503, body: "offline fixture" }));
  return { writes, reads };
}

async function openAdmin(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "계정 관리", exact: true }).click();
  await expect(page.getByRole("heading", { name: "계정 관리", exact: true })).toBeVisible();
}

test("ADMIN UI: only admin sees management entry; stale admin authorization is rejected without rendering account data", async ({ page }) => {
  const first = await fixture(page, { isAdmin: false });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "계정 관리", exact: true })).toHaveCount(0);
  expect(first.reads).toEqual([]);
  await page.unroute("**/api/**");
  const denied = await fixture(page, { denyAdmin: true });
  await openAdmin(page);
  await expect(page.getByRole("heading", { name: "관리자 권한이 필요합니다", exact: true })).toBeVisible();
  await expect(page.getByText("합성 신청자", { exact: true })).toHaveCount(0);
  expect(denied.writes).toEqual([]);
});

test("ADMIN UI: approval is explicit, fenced and refreshes pending list; account cleanup preserves records", async ({ page }) => {
  const state = await fixture(page);
  await openAdmin(page);
  await expect(page.getByText("합성 신청자", { exact: true })).toBeVisible();
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("button", { name: "applicant 승인", exact: true }).click();
  expect(state.writes).toEqual([]);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "applicant 승인", exact: true }).click();
  await expect(page.locator(".account-admin-notice")).toContainText("가입 승인했습니다");
  await expect(page.getByText("해당 상태의 계정이 없습니다.", { exact: true })).toBeVisible();
  expect(state.writes).toEqual([{ path: "/api/admin/accounts/applicant/approve", method: "POST", body: { expected_revision: pending.revision } }]);
  await page.getByLabel("계정 상태", { exact: true }).selectOption("");
  await expect(page.getByRole("button", { name: "admin 비활성화", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: /삭제/ })).toHaveCount(0);
  await expect(page.getByText(/비활성화해도 기존 문서와 수정 이력은 보존됩니다/)).toBeVisible();
});

test("ADMIN UI: guest password confirmation never stores credentials, AI policy and actor-filtered audit remain separate", async ({ page }) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 400, height: 800 });
  await openAdmin(page);
  await page.getByRole("button", { name: "사이드바 펼침", exact: true }).click();
  await expect.poll(async () => (await page.getByRole("heading", { name: "계정 관리", exact: true }).boundingBox())!.height).toBeLessThan(70);
  await expect(page.getByRole("navigation", { name: "작업 공간", exact: true })).toHaveAttribute("data-mode", "expanded");
  await page.getByRole("button", { name: "사이드바 아이콘만", exact: true }).click();
  await page.getByLabel("계정 상태", { exact: true }).selectOption("");
  await page.getByRole("button", { name: "guest 비밀번호 변경", exact: true }).click();
  await page.getByLabel("새 비밀번호", { exact: true }).fill("synthetic-new-password");
  await page.getByLabel("새 비밀번호 확인", { exact: true }).fill("synthetic-different-password");
  await page.getByRole("button", { name: "비밀번호 변경 저장", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("일치하지 않습니다");
  expect(state.writes).toEqual([]);
  await page.getByLabel("새 비밀번호 확인", { exact: true }).fill("synthetic-new-password");
  await page.getByRole("button", { name: "비밀번호 변경 저장", exact: true }).click();
  await expect(page.locator(".account-admin-notice")).toContainText("기존 로그인은 종료됩니다");
  expect(state.writes[0]).toEqual({ path: "/api/admin/accounts/guest/password", method: "POST", body: { password: "synthetic-new-password", expected_revision: guest.revision } });
  await expect(page.getByLabel("새 비밀번호", { exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => JSON.stringify({ local: localStorage, session: sessionStorage }))).not.toContain("synthetic-new-password");
  await page.getByRole("tab", { name: "AI 이용 권한", exact: true }).click();
  await page.getByRole("radio", { name: /guest와 승인된 모든 계정 허용/ }).check();
  await page.getByRole("button", { name: "AI 권한 설정 저장", exact: true }).click();
  await expect(page.locator(".account-admin-notice")).toContainText("AI 이용 권한 설정을 저장했습니다");
  expect(state.writes[1]).toEqual({ path: "/api/admin/settings", method: "PATCH", body: { codex_account_access: "approved_accounts" } });
  await expect(page.getByRole("region", { name: "가입 요청 알림", exact: true })).toContainText("전송 실패 2건");
  await page.getByRole("button", { name: "실패한 알림 다시 보내기", exact: true }).click();
  await expect(page.locator(".account-admin-notice")).toContainText("재전송");
  await expect(page.getByRole("region", { name: "가입 요청 알림", exact: true })).toContainText("전송 실패 0건");
  expect(state.writes[2]).toEqual({ path: "/api/admin/notifications/retry", method: "POST", body: {} });
  await page.getByRole("tab", { name: "감사 로그", exact: true }).click();
  await expect(page.getByText("검수용 문서.hwpx", { exact: true })).toBeVisible();
  await page.getByLabel("계정 아이디", { exact: true }).fill("applicant");
  await page.getByRole("button", { name: "기록 조회", exact: true }).click();
  await expect(page.locator(".account-audit-list")).toContainText("applicant");
  expect(state.reads.some(url => url.includes("actor=applicant"))).toBe(true);
  const overflow = await page.locator("main.account-admin").evaluate(element => element.scrollWidth > element.clientWidth);
  expect(overflow).toBe(false);
});
