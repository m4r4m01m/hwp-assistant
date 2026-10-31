import { test, expect, type Page, type Route } from "@playwright/test";
import { prepareScreenshotFont } from "./ui-proof-font";

// HTTP and the document engine are deliberately mocked. These tests cover the
// setup UI lifecycle only; they do not launch Codex or validate native HWPX.
type AuthState = "unchecked" | "chatgpt" | "signed_out" | "unsupported" | "timeout" | "error" | "not_installed";
type Choice = null | "codex" | "later";
const documentRow = { id: "setup-document", name: "보존할 합성 문서.hwpx", current_version: "saved-version", role: "owner", created_at: 1 };
const featureRows = [
  { id: "reports", label: "보고서 작성", provider: "codex", configured: true, allowed: true, message: "Codex 설정됨" },
  { id: "rewrite", label: "문단 수정", provider: "codex", configured: true, allowed: true, message: "Codex 설정됨" },
  { id: "document_assistant", label: "문서 AI 대화", provider: "codex", configured: true, allowed: true, message: "Codex 설정됨" },
  { id: "chat", label: "공유 대화", provider: "llm", configured: false, allowed: true, message: "서버의 모델 연결이 필요합니다." },
  { id: "review", label: "문단 AI 검토", provider: "llm", configured: false, allowed: true, message: "서버의 모델 연결이 필요합니다." },
];
const statusMessages: Record<AuthState, string> = {
  unchecked: "연결 상태를 확인해 주세요.", chatgpt: "ChatGPT 로그인이 확인되었습니다.",
  signed_out: "Codex CLI에서 ChatGPT 로그인 후 다시 확인하세요.", unsupported: "ChatGPT 계정 로그인이 필요합니다.",
  timeout: "로그인 상태 확인 시간이 초과되었습니다. 다시 확인하세요.", error: "Codex 연결을 확인하지 못했습니다.",
  not_installed: "이 PC에서 Codex CLI를 찾지 못했습니다.",
};
const serverError = "서버에서 요청을 처리하지 못했습니다. 잠시 뒤 다시 시도하세요.";
function status(choice: Choice = null, auth: AuthState = "unchecked", server = false) {
  return {
    deployment_mode: server ? "server" : "local", can_configure: !server, needs_setup: !server && choice === null, choice,
    codex: { installed: auth !== "not_installed", auth_state: auth, checked_at: auth === "unchecked" ? null : 1792800000, message: statusMessages[auth] },
    features: featureRows,
  };
}
type Status = ReturnType<typeof status>;
const json = (route: Route, body: unknown, code = 200) => route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(body) });
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}
async function mockedApp(page: Page, initial = status()) {
  const state = {
    value: initial, reads: 0, checks: 0, setups: [] as Choice[], modelCalls: 0, leases: 0, releases: 0,
    check: async (_route: Route): Promise<Status | void> => status(state.value.choice, "chatgpt"),
    setup: async (_route: Route, choice: Choice): Promise<Status | void> => ({ ...state.value, choice, needs_setup: false }),
    read: async (_route: Route): Promise<Status | void> => state.value,
  };
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    if (path === "/ai-connection") { state.reads++; const value = await state.read(route); if (value) await json(route, value); return; }
    if (path === "/ai-connection/check") { state.checks++; const value = await state.check(route); if (value) { state.value = value; await json(route, value).catch(() => {}); } return; }
    if (path === "/ai-connection/setup") { const choice = request.postDataJSON().choice as Choice; state.setups.push(choice); const value = await state.setup(route, choice); if (value) { state.value = value; await json(route, value); } return; }
    if (path === "/auth/me") return json(route, { user_id: "local-test-user", authentication_required: false });
    if (path === "/capabilities") return json(route, { features: Object.fromEntries(["chat", "rewrite", "review", "reports"].map(id => [id, { configured: false, reachable: null, allowed: true, reason_code: "not_configured", message: "합성 시험에서는 AI를 호출하지 않습니다.", supports_stream: false }])) });
    if (path === "/documents") return json(route, [documentRow]);
    if (path === "/documents/setup-document/edit-target") return json(route, { document: documentRow, mode: "direct" });
    if (["/folders", "/workspaces"].includes(path)) return json(route, []);
    if (path === "/documents/setup-document/session") {
      if (request.method() === "POST") { state.leases++; return json(route, { token: "synthetic-lease", document: documentRow }); }
      if (request.method() === "DELETE") state.releases++;
      return json(route, {});
    }
    if (path.endsWith("/file")) return route.fulfill({ contentType: "application/octet-stream", body: "synthetic saved bytes" });
    if (path.endsWith("/versions") || path.endsWith("/proposals") || path.endsWith("/threads") || path.endsWith("/rewrite-tasks") || path.endsWith("/assistant-turns")) return json(route, []);
    if (path === "/document-blocks") return json(route, { blocks: [], standard_reference_sha256: "a".repeat(64), standard_profile_sha256: "b".repeat(64) });
    if (request.method() === "POST") state.modelCalls++;
    return json(route, { detail: `Unmocked test request: ${path}` }, 404);
  });
  return state;
}
const dialog = (page: Page) => page.getByRole("dialog", { name: "AI 연결 설정", exact: true });
const connection = (page: Page) => page.getByRole("button", { name: "AI 연결", exact: true });
async function openSettings(page:Page){const settings=page.locator('.workspace-nav-settings');await expect(settings).toBeVisible();if(!await settings.evaluate(el=>(el as HTMLDetailsElement).open))await page.getByLabel('설정 메뉴',{exact:true}).click();}

const check = (page: Page) => dialog(page).getByRole("button", { name: "연결 상태 확인", exact: true });
const useCodex = (page: Page) => dialog(page).getByRole("button", { name: "이 PC의 Codex 사용", exact: true });
async function library(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
}

test("MOCK AI setup: explicit ChatGPT check is single-flight, Codex choice persists, and feature limits stay visible", async ({ page }, info) => {
  const state = await mockedApp(page), pending = deferred(), savePending = deferred();
  state.check = async () => { await pending.promise; return status(null, "chatgpt"); };
  state.setup = async (_route, choice) => { await savePending.promise; return { ...state.value, choice, needs_setup: false }; };
  await library(page);
  await expect(dialog(page)).toBeVisible();
  await expect(useCodex(page)).toBeDisabled();
  expect(state.checks).toBe(0);
  await check(page).focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await expect.poll(() => state.checks).toBe(1);
  await expect(useCodex(page)).toBeDisabled();
  pending.resolve();
  await expect(useCodex(page)).toBeEnabled();
  await expect(dialog(page)).toContainText(statusMessages.chatgpt);
  await expect(dialog(page)).toContainText("서버의 모델 연결이 필요합니다.");
  await prepareScreenshotFont(page);
  await page.screenshot({ path: info.outputPath("local-codex-verified.png"), fullPage: true });
  await useCodex(page).click();
  await page.keyboard.press("Enter");
  await expect.poll(() => state.setups.length).toBe(1);
  savePending.resolve();
  await expect(dialog(page)).not.toBeVisible();
  expect(state.setups).toEqual(["codex"]);
  await page.reload();
  await openSettings(page); await expect(connection(page)).toBeVisible();
  await expect(dialog(page)).not.toBeVisible();
  await openSettings(page); await connection(page).click();
  await expect(dialog(page)).toBeVisible();
  expect(state.checks).toBe(1);
  expect(state.modelCalls).toBe(0);
});

test("MOCK AI setup: compact keyboard onboarding can defer and remains deferred after reload", async ({ page }, info) => {
  const state = await mockedApp(page);
  await page.setViewportSize({ width: 400, height: 760 });
  await library(page);
  await expect(dialog(page)).toBeVisible();
  const bounds = (await dialog(page).boundingBox())!;
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.y).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(400);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(760);
  await dialog(page).getByRole("button", { name: "나중에 설정", exact: true }).focus();
  await page.keyboard.press("Tab");
  // Chrome may visit browser chrome after the last control of a native dialog.
  // Its document activeElement is then BODY, while background controls remain
  // inert. Verify the native modal boundary and return path, not a custom trap.
  expect(await dialog(page).evaluate(node => node.matches(":modal"))).toBe(true);
  const backgroundDocument = page.getByRole("button", { name: documentRow.name, exact: true });
  await backgroundDocument.evaluate(node => node.focus());
  await expect(backgroundDocument).not.toBeFocused();
  if (!(await dialog(page).evaluate(node => node.contains(document.activeElement)))) await page.keyboard.press("Tab");
  expect(await dialog(page).evaluate(node => node.contains(document.activeElement))).toBe(true);
  await prepareScreenshotFont(page);
  await page.screenshot({ path: info.outputPath("compact-ai-setup.png"), fullPage: true });
  await page.keyboard.press("Escape");
  await expect(dialog(page)).not.toBeVisible();
  expect(state.setups).toEqual(["later"]);
  expect(state.checks).toBe(0);
  await page.reload();
  await openSettings(page); await expect(connection(page)).toBeVisible();
  await expect(dialog(page)).not.toBeVisible();
  await openSettings(page); await connection(page).click();
  await expect(dialog(page)).toBeVisible();
  await dialog(page).getByRole("button", { name: "닫기", exact: true }).click();
  await expect(connection(page)).toBeFocused();
  expect(state.setups).toEqual(["later"]);
  expect(state.modelCalls).toBe(0);
});

test("MOCK AI setup: missing CLI, signed-out account and timeout stay recoverable without saving a false connection", async ({ page }) => {
  const state = await mockedApp(page);
  await library(page);
  for (const auth of ["not_installed", "signed_out", "timeout", "unsupported"] as AuthState[]) {
    state.check = async () => status(null, auth);
    await check(page).click();
    await expect(dialog(page)).toContainText(statusMessages[auth]);
    await expect(useCodex(page)).toBeDisabled();
    await expect(check(page)).toBeEnabled();
  }
  expect(state.setups).toEqual([]);
  state.check = async () => status(null, "chatgpt");
  await check(page).click();
  await expect(useCodex(page)).toBeEnabled();
  expect(state.checks).toBe(5);
  expect(state.modelCalls).toBe(0);
});

test("MOCK AI setup: server mode is information-only and never probes or configures the browser PC", async ({ page }, info) => {
  const state = await mockedApp(page, status(null, "unchecked", true));
  await library(page);
  await openSettings(page); await expect(connection(page)).toBeVisible();
  await expect(dialog(page)).not.toBeVisible();
  await openSettings(page); await connection(page).click();
  await expect(dialog(page)).toContainText("서버의 AI 연결");
  await expect(useCodex(page)).toHaveCount(0);
  await expect(check(page)).toHaveCount(0);
  await expect(dialog(page).getByRole("button", { name: "나중에 설정", exact: true })).toHaveCount(0);
  const before = state.reads;
  await dialog(page).getByRole("button", { name: "상태 새로고침", exact: true }).click();
  await expect.poll(() => state.reads).toBe(before + 1);
  expect(state.checks).toBe(0);
  expect(state.setups).toEqual([]);
  expect(state.modelCalls).toBe(0);
  await prepareScreenshotFont(page);
  await page.screenshot({ path: info.outputPath("server-ai-connection.png"), fullPage: true });
});

test("MOCK AI setup: closed dialog ignores a late check after reopening and setup failure remains dismissible", async ({ page }) => {
  const state = await mockedApp(page, status("later")), pending = deferred(), released = deferred();
  state.check = async route => { await pending.promise; await json(route, status("later", "chatgpt")).catch(() => {}); released.resolve(); };
  await library(page);
  await openSettings(page); await connection(page).click();
  await check(page).click();
  await expect.poll(() => state.checks).toBe(1);
  await dialog(page).getByRole("button", { name: "닫기", exact: true }).click();
  await expect(dialog(page)).not.toBeVisible();
  state.value = status("later", "signed_out");
  await openSettings(page); await connection(page).click();
  await expect(dialog(page)).toContainText(statusMessages.signed_out);
  pending.resolve();
  await released.promise;
  await expect(useCodex(page)).toBeDisabled();
  await expect(dialog(page)).toContainText(statusMessages.signed_out);
  await dialog(page).getByRole("button", { name: "닫기", exact: true }).click();
  state.value = status();
  state.setup = async route => { await json(route, { detail: "합성 설정 저장 실패" }, 503); };
  await page.reload();
  await expect(dialog(page)).toBeVisible();
  await dialog(page).getByRole("button", { name: "나중에 설정", exact: true }).click();
  await expect(dialog(page).getByRole("alert")).toContainText(serverError);
  await expect(dialog(page)).not.toContainText("합성 설정 저장 실패");
  await dialog(page).getByRole("button", { name: "저장하지 않고 닫기", exact: true }).click();
  await expect(dialog(page)).not.toBeVisible();
  await expect(page.getByRole("button", { name: documentRow.name, exact: true })).toBeEnabled();
  expect(state.modelCalls).toBe(0);
});

test("MOCK AI setup: unavailable status leaves the library usable and manual retry recovers", async ({ page }) => {
  const state = await mockedApp(page);
  state.read = async route => { await json(route, { detail: "합성 연결 상태 조회 실패" }, 503); };
  await library(page);
  await expect(dialog(page)).not.toBeVisible();
  await expect(page.getByRole("button", { name: documentRow.name, exact: true })).toBeEnabled();
  await openSettings(page); await connection(page).click();
  await expect(dialog(page).getByRole("alert")).toContainText(serverError);
  await expect(dialog(page)).not.toContainText("합성 연결 상태 조회 실패");
  state.read = async () => state.value;
  await dialog(page).getByRole("button", { name: "설정 다시 불러오기", exact: true }).click();
  await expect(check(page)).toBeEnabled();
  await expect(dialog(page).getByRole("alert")).toHaveCount(0);
  expect(state.checks).toBe(0);
  expect(state.modelCalls).toBe(0);
});

async function mockEditableEngine(page: Page) {
  await page.route("**/src/editor.ts", route => route.fulfill({ contentType: "text/javascript", body: `
    export class EditorAdapter {
      static async mount(host) {
        window.__setupEngineMounts = (window.__setupEngineMounts || 0) + 1;
        const frame = document.createElement('iframe'); frame.title = '합성 문서 편집기';
        frame.srcdoc = '<!doctype html><html><body><textarea aria-label="합성 문서 본문">저장된 본문</textarea></body></html>';
        host.append(frame);
        await new Promise(resolve => { frame.onload = resolve; });
        const callbacks = new Set(); const state = { documentEpoch: 1, changeSeq: 0, documentSha256: 'a'.repeat(64) };
        frame.contentDocument.querySelector('textarea').addEventListener('input', () => { state.changeSeq++; callbacks.forEach(callback => callback()); });
        const instance = new EditorAdapter();
        instance.sdk = { element: frame, getDocumentState: async () => ({ ...state }), destroy: () => { window.__setupEngineDestroys = (window.__setupEngineDestroys || 0) + 1; frame.remove(); } };
        instance.bridge = { revision: () => state.changeSeq, warnings: () => null, subscribe: callback => { callbacks.add(callback); return () => callbacks.delete(callback); }, newDocument: async () => {}, documentState: () => ({ ...state }) };
        return instance;
      }
      async load() { return this.sdk.getDocumentState(); }
      destroy() { this.sdk.destroy(); }
    }
  ` }));
}

test("MOCK AI setup: opening, checking and saving settings preserves mounted editor and unsaved body", async ({ page }, info) => {
  await mockEditableEngine(page);
  const state = await mockedApp(page, status("later"));
  await library(page);
  await page.getByRole("button", { name: documentRow.name, exact: true }).click();
  await expect(page.locator(".editor-current-document [role=status]")).toHaveText("저장 완료");
  const body = page.frameLocator('iframe[title="합성 문서 편집기"]').getByLabel("합성 문서 본문");
  await body.fill("서버에 저장하지 않은 본문 — AI 연결 설정 중에도 보존");
  await expect(page.locator(".editor-current-document [role=status]")).toHaveText("저장되지 않은 변경 있음");
  // Opening by keyboard leaves the editor's own menu open underneath. Escape
  // must dismiss only the modal, without reaching the document key listener.
  const fileMenu = page.getByLabel("파일 작업", { exact: true }).locator("..");
  await page.getByLabel("파일 작업", { exact: true }).click();
  await expect(fileMenu).toHaveJSProperty("open", true);
  await page.getByLabel("설정 메뉴", { exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator('.workspace-nav-settings')).toHaveJSProperty("open", true);
  await connection(page).focus();
  await page.keyboard.press("Enter");
  await expect(dialog(page)).toBeVisible();
  await expect(fileMenu).toHaveJSProperty("open", true);
  await page.keyboard.press("Escape");
  await expect(dialog(page)).not.toBeVisible();
  await expect(fileMenu).toHaveJSProperty("open", true);
  await expect(connection(page)).toBeFocused();
  await openSettings(page); await connection(page).click();
  await expect(dialog(page)).toBeVisible();
  expect(await page.evaluate(() => (window as any).__setupEngineMounts)).toBe(1);
  await check(page).click();
  await expect(useCodex(page)).toBeEnabled();
  await prepareScreenshotFont(page);
  await page.screenshot({ path: info.outputPath("ai-setup-over-unsaved-editor.png"), fullPage: true });
  await useCodex(page).click();
  await expect(dialog(page)).not.toBeVisible();
  await expect(body).toHaveValue("서버에 저장하지 않은 본문 — AI 연결 설정 중에도 보존");
  await expect(page.locator(".editor-current-document [role=status]")).toHaveText("저장되지 않은 변경 있음");
  expect(await page.evaluate(() => (window as any).__setupEngineMounts)).toBe(1);
  expect(await page.evaluate(() => (window as any).__setupEngineDestroys || 0)).toBe(0);
  expect(state.leases).toBe(1);
  expect(state.releases).toBe(0);
  expect(state.setups).toEqual(["codex"]);
  expect(state.modelCalls).toBe(0);
});

