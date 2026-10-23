import { test, expect, type Page, type Route } from "@playwright/test";
import { createHash } from "node:crypto";
import { prepareScreenshotFont } from "./ui-proof-font";

// These are UI lifecycle tests with explicitly mocked HTTP/engine responses.
// Native HWPX/AI correctness is covered separately by real-engine suites.
const documentRow = { id: "document-one", name: "합성 문서.hwpx", current_version: "version-list", role: "owner", created_at: 1 };
const feature = { configured: false, reachable: null, allowed: true, reason_code: "not_configured", message: "서버의 AI 연결 설정이 필요합니다.", supports_stream: false };
const capabilities = { features: Object.fromEntries(["chat", "rewrite", "review", "reports"].map(name => [name, feature])) };
const writingContext = { revision: 0, base_version: "version-lease", purpose: "", audience: "", tone: "", background: "", requirements: "", restrictions: "", facts: "", terminology: "", confirmation: null, stale: false };
type Handler = (route: Route, path: string) => Promise<boolean | void>;
async function json(route: Route, body: unknown, status = 200) { await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) }); }
async function mockApi(page: Page, handler?: Handler) {
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname.replace(/^.*\/api/, "");
    if (await handler?.(route, path)) return;
    if (path === "/auth/me") return json(route, { user_id: "test-user", authentication_required: true });
    if (path === "/capabilities") return json(route, capabilities);
    if (path === "/documents") return json(route, [documentRow]);
    if (path === "/documents/document-one/session") return json(route, route.request().method() === "POST" ? { token: "local-test-lease", document: { ...documentRow, name: "서버의 현재 문서.hwpx", current_version: "version-lease" } } : {});
    if (path.endsWith("/file")) return route.fulfill({ status: 200, contentType: "application/octet-stream", body: "local-test-file" });
    if (path.endsWith("/versions") || path.endsWith("/proposals") || path.endsWith("/rewrite-tasks")) return json(route, []);
    if (path === "/documents/document-one/access") return json(route, []);
    if (path.endsWith("/threads")) return json(route, [{ id: "thread-one", title: "합성 대화", visibility: "document" }]);
    if (path === "/threads/thread-one") return json(route, { id: "thread-one", title: "합성 대화", visibility: "document", context: writingContext, messages: [] });
    if (path === "/rewrite-provider") return json(route, { provider: "llm", configured: false, allowed: true, model: null, reason: feature.message });
    if (path === "/document-blocks") return json(route, { blocks: [], standard_reference_sha256: "a".repeat(64), standard_profile_sha256: "b".repeat(64) });
    if (path === "/report-tasks") return json(route, []);
    if (path === "/visual-standards") return json(route, { items: [], presets: [] });
    if (path === "/report-templates") return json(route, { items: [{ id: "standard-business", revision: 1, values: { name: "기본 보고서", description: "합성 테스트 기준", writing_criteria: [] } }], default_id: "standard-business" });
    if (path === "/document-standard") return json(route, { id: "standard", name: "합성 기준", reference_url: "api/reference", reference_sha256: "a".repeat(64) });
    if (path === "/template-market") return json(route, { items: [], total: 0, categories: [] });
    return json(route, { detail: `Unmocked test request: ${path}` }, 404);
  });
}
async function mockEditor(page: Page) {
  await page.route("**/src/editor.ts", route => route.fulfill({ contentType: "text/javascript", body: `
    export class EditorAdapter {
      static async mount(host, options = {}) {
        window.__engineStarted = (window.__engineStarted || 0) + 1;
        const frame = document.createElement('iframe'); frame.title = '합성 편집기';
        frame.srcdoc = '<!doctype html><html><body><p>합성 편집 화면</p></body></html>';
        host.append(frame);
        await new Promise(resolve => setTimeout(resolve, window.__engineDelay || 0));
        const state = { documentEpoch: 1, changeSeq: 0, documentSha256: 'a'.repeat(64) };
        const instance = new EditorAdapter();
        instance.sdk = { element: frame, getDocumentState: async () => state, destroy: () => frame.remove() };
        instance.bridge = { revision: () => 0, warnings: () => null, subscribe: () => () => {}, newDocument: async () => {}, documentState: () => state };
        return instance;
      }
      async load(bytes, name) { window.__loadedName = name; window.__loadedBytes = new TextDecoder().decode(bytes); return this.sdk.getDocumentState(); }
      destroy() { this.sdk.destroy(); }
    }
  ` }));
}
async function library(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
}
async function openEditor(page: Page) {
  await library(page);
  await page.getByRole("button", { name: documentRow.name, exact: true }).click();
  await expect(page.locator(".editor-current-document [role=status]")).toHaveText("저장 완료");
}

test("MOCK UI: initial 401 is normal and repeated Enter submits one pending login", async ({ page }) => {
  let authenticated = false, logins = 0;
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  await mockApi(page, async (route, path) => {
    if (path === "/auth/me" && !authenticated) { await json(route, { detail: "authentication required" }, 401); return true; }
    if (path === "/auth/login") { logins++; await pending; authenticated = true; await json(route, { ok: true }); return true; }
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "HWPX 문서함 로그인" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByLabel("인증 토큰").fill("synthetic-test-token");
  await page.getByLabel("인증 토큰").press("Enter");
  await page.keyboard.press("Enter"); await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "로그인 확인 중…" })).toBeDisabled();
  expect(logins).toBe(1);
  finish();
  await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
  expect(logins).toBe(1);
});

test("MOCK UI: search debounces keystrokes and a late old response cannot replace the new result", async ({ page }) => {
  const queries: string[] = [];
  let oldResponse!: () => void;
  const old = new Promise<void>(resolve => { oldResponse = resolve; });
  await mockApi(page, async (route, path) => {
    if (path !== "/documents") return;
    const query = new URL(route.request().url()).searchParams.get("q") || "";
    queries.push(query);
    if (query === "old") await old;
    await json(route, [{ ...documentRow, name: `${query || "initial"}.hwpx` }]).catch(() => {});
    return true;
  });
  await library(page);
  const search = page.getByLabel("파일명 검색");
  await search.pressSequentially("old", { delay: 35 });
  await expect.poll(() => queries).toContain("old");
  expect(queries).not.toContain("o"); expect(queries).not.toContain("ol");
  await search.fill("new");
  await search.press("Enter");
  await expect(page.getByRole("button", { name: "new.hwpx", exact: true })).toBeVisible();
  oldResponse();
  await expect(page.getByRole("button", { name: "old.hwpx", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "new.hwpx", exact: true })).toBeVisible();
});

test("MOCK UI: history shows loading until its response and sharing dialog restores focus", async ({ page }) => {
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  await mockApi(page, async (route, path) => {
    if (path.endsWith("/versions")) { await pending; await json(route, []); return true; }
  });
  await library(page);
  await page.getByRole("button", { name: "버전 기록", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("버전 기록을 불러오는 중");
  await expect(page.getByText("두 버전 이상 있어야 비교할 수 있습니다.", { exact: true })).toHaveCount(0);
  finish();
  await expect(page.getByRole("status")).toHaveCount(0);
  await page.keyboard.press("Escape");
  const share = page.getByRole("button", { name: "공유 권한", exact: true });
  await share.click();
  const dialog = page.getByRole("dialog", { name: /공유 권한/ });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Shift+Tab");
  expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(share).toBeFocused();
});

test("MOCK UI: fixed lease version downloads while the editor initializes; mobile drawer preserves the mounted canvas", async ({ page }, info) => {
  await mockEditor(page);
  await page.addInitScript(() => { (window as any).__engineDelay = 450; });
  let fileEngineStarted = false, filePath = "";
  await mockApi(page, async (route, path) => {
    if (path.endsWith("/file")) {
      filePath = path;
      fileEngineStarted = await page.evaluate(() => !!(window as any).__engineStarted);
      await route.fulfill({ body: "fixed lease bytes" });
      return true;
    }
  });
  await page.setViewportSize({ width: 400, height: 760 });
  await openEditor(page);
  expect(fileEngineStarted).toBe(true);
  expect(filePath).toContain("/version-lease/file");
  await expect(page.getByLabel("현재 문서와 저장 상태")).toContainText("서버의 현재 문서.hwpx");
  const frame = page.locator(".block-editor-slot.active iframe");
  const before = (await frame.boundingBox())!;
  await page.getByRole("button", { name: "AI 검토 열기", exact: true }).click();
  const drawer = page.getByLabel("문단 AI 검토 패널", { exact: true });
  const bounds = (await drawer.boundingBox())!;
  expect(bounds.width).toBeCloseTo(400 - 64, 1);
  expect(bounds.y).toBeGreaterThan(40);
  expect((await frame.boundingBox())!.width).toBeCloseTo(before.width, 1);
  await expect(page.locator(".block-editor-wrapper")).toHaveJSProperty("inert", true);
  await expect(page.getByRole("button", { name: "현재 문단 검토", exact: true })).toBeDisabled();
  await prepareScreenshotFont(page);
  await page.screenshot({ path: info.outputPath("mobile-editor-drawer.png") });
  await page.getByRole("button", { name: "AI 검토 닫기", exact: true }).click();
  await expect(page.locator(".block-editor-wrapper")).toHaveJSProperty("inert", false);
  expect(await page.evaluate(() => (window as any).__engineStarted)).toBe(1);
});

test("MOCK UI: unconfigured AI blocks new report calls but retains existing draft formatting", async ({ page }, info) => {
  const draft = { title: "이미 작성한 초안", summary: "보관된 내용", sections: [], sources: [], limitations: [] };
  const job = { id: "report-one", status: "ready", input: { prompt: "이미 작성된 보고서", research: false }, draft, draft_sha256: "a".repeat(64), error: "", approval: null };
  let modelCalls = 0;
  await mockApi(page, async (route, path) => {
    if (path === "/report-tasks") { if (route.request().method() === "POST") modelCalls++; await json(route, [job]); return true; }
    if (path === "/report-tasks/report-one") { await json(route, job); return true; }
  });
  await page.setViewportSize({ width: 400, height: 760 });
  await library(page);
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill("새 보고서 요청 내용");
  await expect(page.getByRole("button", { name: "Codex로 보고서 초안 만들기", exact: true })).toBeDisabled();
  await expect(page.locator(".capability-notice")).toContainText(feature.message);
  const bounds = (await page.getByLabel("보고서 작성 요청 도구", { exact: true }).boundingBox())!;
  expect(bounds.width).toBeCloseTo(336, 1);
  await expect(page.locator(".report-workspace-canvas")).toHaveJSProperty("inert", true);
  await prepareScreenshotFont(page);
  await page.screenshot({ path: info.outputPath("mobile-report-request.png") });
  await page.getByRole("button", { name: "최근 보고서", exact: true }).click();
  await page.getByRole("button", { name: draft.title, exact: true }).click();
  await expect(page.getByRole("heading", { name: draft.title, exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true })).toBeEnabled();
  await page.getByLabel("보완 의견", { exact: true }).fill("보완할 내용");
  await expect(page.getByRole("button", { name: "의견 반영해 다시 작성", exact: true })).toBeDisabled();
  expect(modelCalls).toBe(0);
});

test("MOCK UI: idle rewrite history does not poll, and hidden tools stop active polling", async ({ page }) => {
  await mockEditor(page);
  let reads = 0, running = false;
  const task = { id: "rewrite-one", document_id: "document-one", base_version: "version-lease", requested_by: "test-user", status: "running", stale: false, error: "", instruction: "합성 요청", snapshot: { revision: 0, evidence: { text: "원문" } }, execution: {}, candidates: [], application: null };
  await mockApi(page, async (route, path) => {
    if (path.endsWith("/rewrite-tasks")) { reads++; await json(route, running ? [task] : []); return true; }
  });
  await openEditor(page);
  await page.getByRole("button", { name: "대화 · AI 열기", exact: true }).click();
  await page.getByLabel("대화 선택").selectOption("thread-one");
  await expect(page.getByTestId("rewrite-provider")).toContainText("로컬·사내 모델");
  const idleReads = reads;
  await page.clock.install();
  await page.clock.runFor(6500);
  expect(reads).toBe(idleReads);
  running = true;
  await page.getByRole("button", { name: "후보·적용 이력 새로고침", exact: true }).click();
  await expect(page.getByText("작업 rewrite-one", { exact: true })).toBeVisible();
  const activeReads = reads;
  await page.clock.runFor(2100);
  await expect.poll(() => reads).toBeGreaterThan(activeReads);
  await page.getByRole("button", { name: "AI 검토 열기", exact: true }).click();
  const hiddenReads = reads;
  await page.clock.runFor(6500);
  expect(reads).toBe(hiddenReads);
});

test("MOCK UI: mobile document actions fit the viewport and remain keyboard reachable", async ({ page }, info) => {
  await mockApi(page);
  await page.setViewportSize({ width: 400, height: 760 });
  await library(page);
  const actions = page.locator(".library-document-actions");
  await expect(actions).toBeVisible();
  const bounds = (await actions.boundingBox())!;
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(400);
  const fileInput = page.getByLabel(`${documentRow.name} 새 버전 업로드`);
  await fileInput.focus();
  await expect(fileInput).toBeFocused();
  await prepareScreenshotFont(page);
  await page.screenshot({ path: info.outputPath("mobile-document-library.png") });
});

test("MOCK UI: failed fixed-version download releases its lease and discards a late engine", async ({ page }) => {
  await mockEditor(page);
  await page.addInitScript(() => { (window as any).__engineDelay = 350; });
  let released = 0;
  await mockApi(page, async (route, path) => {
    if (path.endsWith("/file")) { await json(route, { detail: "파일 권한이 변경되었습니다." }, 403); return true; }
    if (path.endsWith("/session") && route.request().method() === "DELETE") { released++; await json(route, {}); return true; }
  });
  await library(page);
  await page.getByRole("button", { name: documentRow.name, exact: true }).click();
  await expect(page.locator(".editor-current-document [role=status]")).toHaveText("열기 실패");
  await expect.poll(() => released).toBe(1);
  await expect(page.locator(".block-editor-slot iframe")).toHaveCount(0);
  await expect(page.getByRole("alert")).toContainText("파일 권한이 변경");
});

test("MOCK UI: template first page appears early; cache reuse still rechecks file authorization", async ({ page }) => {
  const file = "synthetic template bytes";
  const digest = createHash("sha256").update(file).digest("hex");
  const template = (id: string) => ({ id, name: `양식 ${id}`, description: "합성 테스트", tags: [], document_id: id, version_id: "fixed-version", category: "보고서", revision: 1, favorite: false, can_manage: false, created_at: 1, sha256: digest, size: file.length, source_name: "sample.hwpx" });
  const catalog = [template("A"), template("B")];
  let authorized = true, fileReads = 0;
  await page.route("**/src/editor.ts", route => route.fulfill({ contentType: "text/javascript", body: `
    export class EditorAdapter {
      static async mount(host) {
        window.__previewMounts = (window.__previewMounts || 0) + 1;
        const frame = document.createElement('iframe'); host.append(frame);
        const value = new EditorAdapter();
        value.sdk = { destroy: () => frame.remove(), pageCount: async () => 3, getPageSvg: async index => {
          if (index === 1 && !window.__pagesReleased) await new Promise(resolve => { window.__releasePages = () => { window.__pagesReleased = true; resolve(); }; });
          return '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="240"><text x="10" y="20">합성 ' + index + '</text></svg>';
        } }; return value;
      }
      async load() {}
      destroy() { this.sdk.destroy(); }
    }
  ` }));
  await page.route("**/rhwp/build-info.json", route => json(route, { commit: "synthetic-engine", bridge: "synthetic-bridge" }));
  await mockApi(page, async (route, path) => {
    if (path === "/template-market") { await json(route, { items: catalog, total: 2, categories: [] }); return true; }
    if (/^\/template-market\/[AB]$/.test(path)) { await json(route, catalog.find(value => path.endsWith(value.id))); return true; }
    if (/^\/template-market\/[AB]\/file$/.test(path)) {
      fileReads++;
      if (!authorized) await json(route, { detail: "forbidden" }, 403);
      else await route.fulfill({ body: file, contentType: "application/octet-stream" });
      return true;
    }
  });
  await library(page);
  await page.getByRole("button", { name: "템플릿 마켓", exact: true }).click();
  const select = async (name: string) => page.locator(".market-card").filter({ has: page.getByRole("heading", { name, exact: true }) }).getByRole("button", { name: "미리보기 · 사용", exact: true }).click();
  await select("양식 A");
  await expect(page.getByRole("img", { name: "양식 미리보기 1쪽", exact: true })).toBeVisible();
  await expect(page.getByRole("img", { name: "양식 미리보기 2쪽", exact: true })).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText("2 / 3쪽");
  await page.evaluate(() => (window as any).__releasePages());
  await expect(page.getByRole("img", { name: "양식 미리보기 3쪽", exact: true })).toBeVisible();
  await select("양식 B");
  await expect(page.getByRole("img", { name: "양식 미리보기 3쪽", exact: true })).toBeVisible();
  await select("양식 A");
  await expect(page.getByRole("img", { name: "양식 미리보기 3쪽", exact: true })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__previewMounts)).toBe(2);
  expect(fileReads).toBe(3);
  await select("양식 B");
  await expect(page.getByRole("img", { name: "양식 미리보기 3쪽", exact: true })).toBeVisible();
  authorized = false;
  await select("양식 A");
  await expect(page.getByRole("alert")).toContainText("현재 이 양식을 열람할 수 없습니다");
  await expect(page.getByRole("img", { name: /양식 미리보기/ })).toHaveCount(0);
  expect(fileReads).toBe(5);
  expect(await page.evaluate(() => (window as any).__previewMounts)).toBe(2);
});

test("MOCK UI: report polling backs off, pauses in hidden pages, and resumes to reconcile completion", async ({ page }) => {
  await page.clock.install();
  let reads = 0, finished = false;
  const job = { id: "report-running", status: "running", input: { prompt: "진행 중인 합성 보고서", research: false }, draft: null, draft_sha256: null, approval: null, error: "", execution: { progress: { revision: 1, stage: "writing", activity: "", research_calls: 0, blocks: [], timings: {} } } };
  await page.addInitScript(() => sessionStorage.setItem("hwp-report-task", "report-running"));
  await mockApi(page, async (route, path) => {
    if (path === "/report-tasks/report-running") { reads++; await json(route, { ...job, status: finished ? "failed" : "running", error: finished ? "합성 중단" : "" }); return true; }
  });
  await library(page);
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await expect.poll(() => reads).toBeGreaterThanOrEqual(2);
  const startReads = reads;
  for (let tick = 0; tick < 12; tick++) await page.clock.runFor(1000);
  expect(reads - startReads).toBeLessThanOrEqual(5);
  expect(reads).toBeGreaterThan(startReads);
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" }); document.dispatchEvent(new Event("visibilitychange")); });
  const hiddenReads = reads;
  await page.clock.runFor(20000);
  expect(reads).toBe(hiddenReads);
  finished = true;
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); document.dispatchEvent(new Event("visibilitychange")); });
  await expect.poll(() => reads).toBeGreaterThan(hiddenReads);
  await expect(page.getByText("합성 중단", { exact: true })).toBeVisible();
  const completedReads = reads;
  await page.clock.runFor(15000);
  expect(reads).toBe(completedReads);
});
