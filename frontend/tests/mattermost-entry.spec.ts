import { test, expect, type Page } from "@playwright/test";

// Synthetic HTTP/editor fixtures. No Mattermost messages, model calls or HWPX writes.
test.use({ trace: "off", video: "off", screenshot: "off" });
const docId = "00000000-0000-4000-8000-000000000001";
const versionId = "00000000-0000-4000-8000-000000000002";
const taskId = "00000000-0000-4000-8000-000000000003";
const otherTask = "00000000-0000-4000-8000-000000000004";
const linkToken = "synthetic_link_".repeat(4);
const report = { id: taskId, input: { prompt: "매터모스트에서 요청한 합성 초안", research: false }, status: "ready", approval: null,
  created_at: 1, draft_sha256: "a".repeat(64), execution: {}, draft: { title: "연결한 합성 보고서", summary: "검토용 합성 요약", sources: [], notes: [], limitations: [], emphasis: [],
    sections: ["개요", "현황", "계획"].map(heading => ({ heading, paragraphs: ["□ 합성 내용"], tables: [], diagrams: [] })) } };

async function setup(page: Page, options: { login?: boolean; reportDenied?: boolean; user?: string; collaborative?: boolean } = {}) {
  let authenticated = !options.login, linked = false;
  const requests: { method: string; path: string; body: any }[] = [];
  await page.route("**/rhwp/build-info.json", route => route.fulfill({ status: 503, body: "synthetic unavailable" }));
  await page.route("**/src/editor.ts*", route => route.fulfill({ contentType: "text/javascript", body: `export class EditorAdapter {
    static async mount() { window.__entryMounts = (window.__entryMounts || 0) + 1; return { load: async () => {}, destroy() {}, sdk: { pageCount: async () => 1, getPageSvg: async () => '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><text y="30">합성 저장본</text></svg>' } }; }
  }` }));
  await page.route("**/api/**", route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    requests.push({ method: request.method(), path, body: request.postData() ? (() => { try { return request.postDataJSON(); } catch { return null; } })() : null });
    const respond = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (path === "/auth/me") return respond(authenticated ? { user_id: options.user || "reviewer", authentication_required: true, kind: "personal", auth_context: "synthetic-login-context" } : { detail: "로그인이 필요합니다." }, authenticated ? 200 : 401);
    if (path === "/auth/status") return respond({ authentication_required: true, accounts_enabled: true, registration_enabled: false, guest_enabled: false, token_login_enabled: false });
    if (path === "/auth/password-login") { authenticated = true; return respond({ user_id: options.user || "reviewer" }); }
    if (path === "/mattermost/link") {
      if (request.method() === "POST") { linked = true; return respond({ linked }); }
      if (request.method() === "DELETE") { linked = false; return respond({ linked }); }
      return respond({ enabled: true, linked });
    }
    if (["/documents", "/workspaces", "/folders"].includes(path)) return respond([]);
    if (path === `/documents/${docId}`) return respond({ id: docId, name: "합성 검색 결과.hwpx", current_version: otherTask, role: "owner", created_at: 1 });
    if (path === `/documents/${docId}/session` && request.method() === "GET") return respond({ active: !!options.collaborative, editor: options.collaborative ? "other-editor" : null, mode: options.collaborative ? "collaborative" : "exclusive" });
    if (path === `/documents/${docId}/versions/${versionId}/file`) return route.fulfill({ body: "synthetic saved snapshot" });
    if (path === `/documents/${docId}/edit-target`) return respond({ detail: "synthetic edit target reached" }, 409);
    if (path === "/report-tasks") return respond([]);
    if (path === `/report-tasks/${taskId}`) return respond(options.reportDenied ? { detail: "보고서 작업을 찾을 수 없습니다." } : report, options.reportDenied ? 404 : 200);
    if (path === "/report-templates") return respond({ items: [{ id: "standard-business", revision: 1, values: { name: "합성 업무보고", description: "", writing_criteria: [] } }], default_id: "standard-business" });
    if (path === "/visual-standards") return respond({ items: [] });
    if (path === "/capabilities") return respond({ features: { reports: { configured: true, reachable: true, allowed: true } } });
    if (path === "/ai-connection") return respond({ deployment_mode: "server", can_configure: false, needs_setup: false, features: [], codex: { installed: true, auth_state: "unchecked", message: "" } });
    return respond({ detail: "Unexpected synthetic request" }, 404);
  });
  return requests;
}
async function login(page: Page) {
  await page.getByRole("button", { name: /내 계정으로 로그인/ }).click();
  await page.getByLabel("아이디", { exact: true }).fill("reviewer");
  await page.getByLabel("비밀번호", { exact: true }).fill("synthetic-password");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
}

test("BOT link waits through login and opens only its saved document version", async ({ page }) => {
  const requests = await setup(page, { login: true });
  await page.goto(`/?document=${docId}&version=${versionId}`);
  await expect(page.getByRole("heading", { name: "HWPX 문서함 로그인", exact: true })).toBeVisible();
  expect(requests.some(item => item.path === `/documents/${docId}`)).toBe(false);
  await login(page);
  await expect(page.getByText("읽기 전용 · 저장본", { exact: true })).toBeVisible();
  await expect(page.locator(".session-viewer-page")).toContainText("합성 저장본");
  expect(requests.some(item => item.path === `/documents/${docId}/versions/${versionId}/file`)).toBe(true);
  expect(requests.filter(item => item.method !== "GET").map(item => item.path)).toEqual(["/auth/password-login"]);
  await page.evaluate(id => { location.hash = `mattermost-link=${"synthetic_".repeat(5)}`; history.pushState(null, "", `?report=${id}`); window.dispatchEvent(new PopStateEvent("popstate")); }, taskId);
  await expect(page.getByText("읽기 전용 · 저장본", { exact: true })).toBeVisible();
  expect(requests.some(item => item.path === `/report-tasks/${taskId}`)).toBe(false);
});

test("BOT report link takes precedence over restored task and pauses at review", async ({ page }) => {
  const requests = await setup(page);
  await page.addInitScript(id => sessionStorage.setItem("hwp-report-task", id), otherTask);
  await page.goto(`/?report=${taskId}`);
  await expect(page.getByLabel("초안 내용 검토")).toBeVisible();
  await expect(page.getByRole("heading", { name: "연결한 합성 보고서", exact: true })).toBeVisible();
  expect(requests.some(item => item.path === `/report-tasks/${otherTask}`)).toBe(false);
  await page.getByLabel("보완 의견", { exact: true }).fill("보존해야 하는 미제출 의견");
  await page.evaluate(id => { history.pushState(null, "", `?report=${id}`); window.dispatchEvent(new PopStateEvent("popstate")); }, otherTask);
  await expect(page.getByLabel("보완 의견", { exact: true })).toHaveValue("보존해야 하는 미제출 의견");
  expect(requests.some(item => item.path === `/report-tasks/${otherTask}`)).toBe(false);
  expect(requests.filter(item => item.method !== "GET")).toEqual([]);
  expect(await page.evaluate(() => (window as any).__entryMounts || 0)).toBe(0);
});

test("BOT inaccessible report shows an ownership error without falling back to another request", async ({ page }) => {
  const requests = await setup(page, { reportDenied: true });
  await page.addInitScript(id => sessionStorage.setItem("hwp-report-task", id), otherTask);
  await page.goto(`/?report=${taskId}`);
  await expect(page.getByRole("alert")).toContainText("요청한 계정으로 로그인했는지 확인");
  await expect(page.getByLabel("보고서 작성 요청", { exact: true })).toHaveCount(0);
  expect(requests.some(item => item.path === `/report-tasks/${otherTask}`)).toBe(false);
  expect(requests.filter(item => item.method !== "GET")).toEqual([]);
});

test("BOT account link scrubs token before login, requires explicit confirmation, and stays out of storage", async ({ page }) => {
  const requests = await setup(page, { login: true });
  await page.goto(`/#mattermost-link=${linkToken}`);
  await expect(page.getByRole("heading", { name: "HWPX 문서함 로그인", exact: true })).toBeVisible();
  expect(page.url()).not.toContain(linkToken);
  await login(page);
  await expect(page.getByRole("button", { name: "이 계정으로 연결", exact: true })).toBeEnabled();
  expect(requests.filter(item => item.path === "/mattermost/link" && item.method === "POST")).toEqual([]);
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain(linkToken);
  await page.getByRole("button", { name: "이 계정으로 연결", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("계정을 연결했습니다");
  expect(requests.filter(item => item.path === "/mattermost/link" && item.method === "POST").map(item => item.body)).toEqual([{ token: linkToken }]);
  await page.getByRole("button", { name: "문서함으로", exact: true }).click();
  await page.getByLabel("설정 메뉴", { exact: true }).click();
  await page.getByRole("button", { name: "매터모스트 연결", exact: true }).click();
  await page.getByRole("button", { name: "연결 해제", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("연결을 해제했습니다");
});

test("BOT invalid entry is inert and guest cannot confirm a link", async ({ page }) => {
  const requests = await setup(page, { user: "guest" });
  await page.goto(`/?document=invalid#mattermost-link=${linkToken}`);
  await expect(page.getByRole("alert")).toContainText("올바르지 않은 문서함 링크");
  expect(page.url()).not.toContain(linkToken);
  expect(requests.some(item => item.path === "/documents/invalid")).toBe(false);
  await page.goto(`/#mattermost-link=${linkToken}`);
  await expect(page.getByRole("alert")).toContainText("승인된 개인 계정");
  await expect(page.getByRole("button", { name: "이 계정으로 연결", exact: true })).toHaveCount(0);
  expect(requests.filter(item => item.method !== "GET")).toEqual([]);
});


test("BOT saved link never autojoins active editing and explicit edit uses a work-copy entry", async ({ page }) => {
  const requests = await setup(page, { collaborative: true });
  await page.goto(`/?document=${docId}&version=${versionId}`);
  await expect(page.locator(".session-viewer-page")).toContainText("합성 저장본");
  await expect(page.getByText("읽기 전용 · 저장본", { exact: true })).toBeVisible();
  expect(requests.filter(item => item.method !== "GET")).toEqual([]);
  await page.getByRole("button", { name: "최신 저장본 편집", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("synthetic edit target reached");
  expect(requests.filter(item => item.method === "POST").map(item => item.path)).toEqual([`/documents/${docId}/edit-target`]);
  expect(requests.some(item => item.method === "POST" && /\/(session|coedit)$/.test(item.path))).toBe(false);
});
