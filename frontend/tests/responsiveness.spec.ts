import { test, expect, type Page, type Route } from "@playwright/test";
import { createHash } from "node:crypto";
import { prepareScreenshotFont } from "./ui-proof-font";

// These are UI lifecycle tests with explicitly mocked HTTP/engine responses.
// Native HWPX/AI correctness is covered separately by real-engine suites.
const documentRow = { id: "document-one", name: "합성 문서.hwpx", current_version: "version-list", role: "owner", created_at: 1 };
const feature = { configured: false, reachable: null, allowed: true, reason_code: "not_configured", message: "서버의 AI 연결 설정이 필요합니다.", supports_stream: false };
const capabilities = { features: Object.fromEntries(["chat", "rewrite", "review", "reports"].map(name => [name, feature])) };
const writingContext = { revision: 0, base_version: "version-lease", purpose: "", audience: "", tone: "", background: "", requirements: "", restrictions: "", facts: "", terminology: "", confirmation: null, stale: false };
function timelineFlow(currentVersion = documentRow.current_version) {
  const document = { ...documentRow, current_version: currentVersion };
  const original = { id: "version-original", document_id: document.id, base_version: null as string | null, size: 100, sha256: "a".repeat(64), created_at: 1, cause: "upload" };
  const latest = { ...original, id: documentRow.current_version, base_version: original.id, created_at: 2, cause: "web-edit" };
  const rows = [original, latest];
  if (currentVersion !== latest.id) rows.push({ ...latest, id: currentVersion, base_version: latest.id, created_at: 3, cause: "restore" });
  return { source_document: document, branches: [], requests: [], versions: rows,
    graph: { nodes: rows.map(row => ({ ...row, kind: "version", current: row.id === currentVersion, can_read: true,
      can_edit: row.id === currentVersion, can_restore: true, file_url: `/documents/${document.id}/versions/${row.id}/file`, document })),
      edges: rows.filter(row => row.base_version).map(row => ({ from: row.base_version, to: row.id, kind: "save" })), truncated: false } };
}
type Handler = (route: Route, path: string) => Promise<boolean | void>;
async function json(route: Route, body: unknown, status = 200) { await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) }); }
async function mockApi(page: Page, handler?: Handler) {
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname.replace(/^.*\/api/, "");
    if (await handler?.(route, path)) return;
    if (path === "/auth/status") return json(route, { accounts_enabled: false });
    if (path === "/auth/me") return json(route, { user_id: "test-user", authentication_required: true });
    if (path === "/capabilities") return json(route, capabilities);
    if (path === "/documents") return json(route, [documentRow]);
    if (path === "/documents/document-one/edit-target") return json(route, { document: documentRow, mode: "direct" });
    if (["/folders", "/workspaces"].includes(path)) return json(route, []);
    if (path === "/workspaces") return json(route, []);
    if (path.endsWith("/branches")) return json(route, timelineFlow());
    if (path === "/documents/document-one/session") return json(route, route.request().method() === "POST" ? { token: "local-test-lease", document: { ...documentRow, name: "서버의 현재 문서.hwpx", current_version: "version-lease" } } : {});
    if (path.endsWith("/file")) return route.fulfill({ status: 200, contentType: "application/octet-stream", body: "local-test-file" });
    if (path.endsWith("/versions") || path.endsWith("/proposals") || path.endsWith("/rewrite-tasks") || path.endsWith("/assistant-turns")) return json(route, []);
    if (path === "/documents/document-one/access") return json(route, []);
    if (path.endsWith("/threads")) return json(route, [{ id: "thread-one", title: "합성 대화", visibility: "document" }]);
    if (path === "/threads/thread-one") return json(route, { id: "thread-one", title: "합성 대화", visibility: "document", context: writingContext, messages: [] });
    if (path === "/rewrite-provider" || path === "/document-assistant-provider") return json(route, { provider: "llm", configured: false, allowed: true, model: null, reason: feature.message });
    if (path === "/document-blocks") return json(route, { blocks: [], standard_reference_sha256: "a".repeat(64), standard_profile_sha256: "b".repeat(64) });
    if (path === "/report-tasks") return json(route, []);
    if (path === "/visual-standards") return json(route, { items: [], presets: [] });
    if (path === "/report-templates") return json(route, { items: [{ id: "standard-business", revision: 1, values: { name: "기본 보고서", description: "합성 테스트 기준", writing_criteria: [] } }], default_id: "standard-business" });
    if (path === "/document-standard") return json(route, { id: "standard", name: "합성 기준", reference_url: "api/reference", reference_sha256: "a".repeat(64) });
    if (path === "/template-market") return json(route, { items: [], total: 0, categories: [] });
    if (path === "/editor-designs") return json(route, { designs: [], standards: [] });
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
        instance.sdk = { element: frame, getDocumentState: async () => state, destroy: () => frame.remove(), pageCount: async () => 1,
          getPageSvg: async () => '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="140"><text x="5" y="20">합성 저장본</text></svg>' };
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

for (const expired of [false, true]) test(`MOCK UI: lost timeline restore ${expired ? "cleans expired confirmation and reloads without a new lease" : "renews the frozen lease and retries the same request"}`, async ({ page }) => {
  await page.clock.install();
  await mockEditor(page);
  let leases = 0, heartbeats = 0, releases = 0, flowReads = 0;
  const keys: string[] = [];
  const bases: string[] = [], tokens: string[] = [];
  const original = timelineFlow().versions[0];
  const restored = { ...original, id: "restored-version", base_version: documentRow.current_version, created_at: 3, cause: "restore", restore_source: { version_id: original.id, created_at: 1 } };
  await mockApi(page, async (route, path) => {
    const method = route.request().method();
    if (path.endsWith("/session")) {
      if (method === "POST") { leases++; await json(route, { token: "synthetic-restore-lease", document: documentRow }); }
      else if (method === "PUT") { heartbeats++; await json(route, expired ? { detail: "편집 세션 만료" } : {}, expired ? 409 : 200); }
      else { releases++; await json(route, {}); }
      return true;
    }
    if (path.endsWith("/branches")) { flowReads++; await json(route, timelineFlow(keys.length ? restored.id : documentRow.current_version)); return true; }
    if (path.endsWith("/restore")) {
      keys.push(route.request().headers()["idempotency-key"]);
      bases.push(new URL(route.request().url()).searchParams.get("base_version")!);
      tokens.push(route.request().headers()["x-edit-session"]);
      await json(route, keys.length === 1 ? { detail: "합성 응답 유실" } : restored, keys.length === 1 ? 503 : 200);
      return true;
    }
  });
  await library(page);
  await page.getByRole("button", { name: "문서 이력", exact: true }).click();
  const timeline = page.getByRole("dialog", { name: "문서 이력", exact: true });
  await timeline.getByRole("button", { name: "분기 보기", exact: true }).click();
  await timeline.locator('[data-version-id="version-original"]').click();
  page.on("dialog", dialog => dialog.accept());
  await timeline.getByRole("button", { name: "이 저장본 복원", exact: true }).click();
    await expect(timeline.getByRole("alert")).toContainText("서버에서 요청을 처리하지 못했습니다");
  const heartbeat = page.waitForResponse(response => response.url().endsWith("/session") && response.request().method() === "PUT");
  await page.clock.fastForward(31000);
  await heartbeat;
  await expect.poll(() => heartbeats).toBe(1);
  await timeline.getByRole("button", { name: "같은 복원 요청으로 확인", exact: true }).click();
  if (expired) {
    await expect(timeline.getByRole("alert")).toContainText("복원 확인 세션이 만료");
    expect(keys).toHaveLength(1);
    await expect(timeline.getByRole("button", { name: "같은 복원 요청으로 확인", exact: true })).toHaveCount(0);
    await expect(timeline.locator('[data-version-id="restored-version"]')).toBeEnabled();
  } else {
    await expect(timeline.getByRole("status").filter({ hasText: "선택한 저장본을 새 버전으로 복원했습니다" })).toBeVisible();
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
    expect(bases).toEqual([documentRow.current_version, documentRow.current_version]);
    expect(tokens).toEqual(["synthetic-restore-lease", "synthetic-restore-lease"]);
  }
  await expect.poll(() => releases).toBe(1);
  expect(flowReads).toBeGreaterThanOrEqual(2);
  expect(leases).toBe(1);
});

test("MOCK UI: stale timeline restore releases its lease, reloads, and allows a fresh confirmed restore", async ({ page }) => {
  await mockEditor(page);
  let flowReads = 0, leases = 0, releases = 0, restores = 0;
  const changed = "version-server-newer", restored = "version-after-restore";
  const bases: string[] = [];
  await mockApi(page, async (route, path) => {
    const method = route.request().method();
    if (path.endsWith("/branches")) {
      flowReads++;
      await json(route, timelineFlow(restores ? restored : flowReads === 1 ? documentRow.current_version : changed));
      return true;
    }
    if (path.endsWith("/session")) {
      if (method === "POST") { leases++; await json(route, { token: `restore-lease-${leases}`, document: { ...documentRow, current_version: changed } }); }
      else { if (method === "DELETE") releases++; await json(route, {}); }
      return true;
    }
    if (path.endsWith("/restore")) {
      restores++; bases.push(new URL(route.request().url()).searchParams.get("base_version")!);
      await json(route, { ...timelineFlow(restored).versions.at(-1), cause: "restore" }); return true;
    }
  });
  await library(page);
  await page.getByRole("button", { name: "문서 이력", exact: true }).click();
  const timeline = page.getByRole("dialog", { name: "문서 이력", exact: true });
  await timeline.getByRole("button", { name: "분기 보기", exact: true }).click();
  await timeline.locator('[data-version-id="version-original"]').click();
  page.on("dialog", dialog => dialog.accept());
  const restore = timeline.getByRole("button", { name: "이 저장본 복원", exact: true });
  await restore.click();
  await expect(timeline.getByRole("alert")).toContainText("최신 버전이 변경");
  await expect(timeline.getByRole("button", { name: "같은 복원 요청으로 확인", exact: true })).toHaveCount(0);
  await expect(timeline.locator(`[data-version-id="${changed}"]`)).toBeEnabled();
  await expect.poll(() => releases).toBe(1);
  expect(restores).toBe(0);
  await restore.click();
  await expect(timeline.getByRole("status").filter({ hasText: "선택한 저장본을 새 버전으로 복원했습니다" })).toBeVisible();
  await expect.poll(() => releases).toBe(2);
  expect(leases).toBe(2); expect(restores).toBe(1); expect(bases).toEqual([changed]);
});

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

test("MOCK UI: timeline shows loading until its response and sharing dialog restores focus", async ({ page }) => {
  await mockEditor(page);
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  await mockApi(page, async (route, path) => {
    if (path.endsWith("/branches")) { await pending; await json(route, timelineFlow()); return true; }
  });
  await library(page);
  await page.getByRole("button", { name: "문서 이력", exact: true }).click();
  const timeline = page.getByRole("dialog", { name: "문서 이력", exact: true });
  await expect(timeline.getByRole("status")).toContainText("문서 이력을 불러오는 중");
  await expect(timeline.locator('[data-version-id]')).toHaveCount(0);
  finish();
  await timeline.getByRole("button", { name: "분기 보기", exact: true }).click();
  await expect(timeline.locator('[data-version-id]')).toHaveCount(2);
  await expect(timeline.getByRole("status").filter({ hasText: "문서 이력을 불러오는 중" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: `${documentRow.name} 더보기`, exact: true }).click();
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
  await page.getByLabel("추가 도구", { exact: true }).click();
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
  await page.getByRole("button", { name: "검토 패널 닫기", exact: true }).click();
  await expect(page.locator(".block-editor-wrapper")).toHaveJSProperty("inert", false);
  expect(await page.evaluate(() => (window as any).__engineStarted)).toBe(1);
});

test("MOCK UI: unsaved conversation confirmation traps focus, cancels with Escape, and discards only after confirmation", async ({ page }) => {
  await mockEditor(page);
  await mockApi(page);
  await openEditor(page);
  const openConversation = page.getByRole("button", { name: "문서 AI 대화 열기", exact: true });
  await openConversation.click();
  const question = page.getByRole("textbox", { name: "AI에게 요청", exact: true });
  await question.fill("보내지 않은 질문을 보존해 주세요.");
  const closePanel = page.getByRole("button", { name: "대화 패널 닫기", exact: true });
  await closePanel.click();
  const dialog = page.getByRole("alertdialog", { name: "미저장 입력 확인", exact: true });
  const keep = dialog.getByRole("button", { name: "계속 편집", exact: true });
  const discard = dialog.getByRole("button", { name: "입력 버리고 닫기", exact: true });
  await expect(keep).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(discard).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(keep).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(discard).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(keep).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(closePanel).toBeFocused();
  await expect(question).toHaveValue("보내지 않은 질문을 보존해 주세요.");
  await closePanel.click();
  await discard.click();
  await expect(page.getByLabel("대화 및 AI 패널", { exact: true })).toHaveCount(0);
  await openConversation.click();
  await expect(question).toHaveValue("");
  await question.fill("목록으로 이동하기 전에 확인할 질문");
  const back = page.getByRole("button", { name: "← 문서 목록", exact: true });
  await back.click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(back).toBeFocused();
  await expect(question).toHaveValue("목록으로 이동하기 전에 확인할 질문");
  await back.click();
  await dialog.getByRole("button", { name: "저장하지 않고 목록으로", exact: true }).click();
  await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
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
  await prepareScreenshotFont(page);
  await page.screenshot({ path: info.outputPath("mobile-report-capability-layout.png") });
  const drawer = page.getByRole("complementary", { name: "보고서 작성 요청 도구", exact: true });
  const notice = drawer.locator(".capability-notice");
  const recheck = notice.getByRole("button", { name: "사용 가능 여부 다시 확인", exact: true });
  const submit = drawer.getByRole("button", { name: "초안 작성하기", exact: true });
  await expect(submit).toBeDisabled();
  await expect(notice).toContainText(feature.message);
  for (const control of [notice, recheck, submit]) await expect(control).toBeInViewport({ ratio: 1 });
  const submitBefore = await submit.boundingBox(), noticeBefore = (await notice.boundingBox())!;
  const recheckBounds = (await recheck.boundingBox())!;
  const drawerScrollBefore = await drawer.evaluate(node => node.scrollTop);
  const inputs = drawer.locator(".report-input");
  const inputBounds = (await inputs.boundingBox())!;
  expect(recheckBounds.y + recheckBounds.height, "retry button must fit inside the capability notice")
    .toBeLessThanOrEqual(noticeBefore.y + noticeBefore.height);
  expect(inputBounds.y, "scrolling inputs must begin below the capability notice controls")
    .toBeGreaterThanOrEqual(recheckBounds.y + recheckBounds.height);
  await recheck.click({ trial: true });
  const scroll = await inputs.evaluate(node => {
    node.scrollTop = node.scrollHeight;
    return { top: node.scrollTop, maximum: node.scrollHeight - node.clientHeight };
  });
  expect(scroll.maximum).toBeGreaterThan(0);
  expect(scroll.top).toBeGreaterThan(0);
  expect(Math.abs(scroll.maximum - scroll.top)).toBeLessThanOrEqual(1);
  expect(await submit.boundingBox()).toEqual(submitBefore);
  expect(await notice.boundingBox()).toEqual(noticeBefore);
  expect(await drawer.evaluate(node => node.scrollTop)).toBe(drawerScrollBefore);
  for (const control of [notice, recheck, submit]) await expect(control).toBeInViewport({ ratio: 1 });
  const bounds = (await drawer.boundingBox())!;
  expect(bounds.width).toBeCloseTo(336, 1);
  await expect(page.locator(".report-workspace-canvas")).toHaveJSProperty("inert", true);
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
    if (path.endsWith("/rewrite-tasks") || path.endsWith("/assistant-turns")) { reads++; await json(route, running ? [task] : []); return true; }
  });
  await openEditor(page);
  await page.getByRole("button", { name: "문서 AI 대화 열기", exact: true }).click();
  await page.getByLabel("대화 선택").selectOption("thread-one");
  await page.getByRole("button", { name: "수정 기록", exact: true }).click();
  await expect(page.getByTestId("rewrite-provider")).toContainText("로컬·사내 모델");
  const idleReads = reads;
  await page.clock.install();
  await page.clock.runFor(6500);
  expect(reads).toBe(idleReads);
  running = true;
  await page.getByRole("button", { name: "후보·적용 이력 새로고침", exact: true }).click();
  await expect(page.locator('[data-task-id="rewrite-one"]')).toBeVisible();
  const activeReads = reads;
  await page.clock.runFor(2100);
  await expect.poll(() => reads).toBeGreaterThan(activeReads);
  await page.getByLabel("추가 도구", { exact: true }).click();
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
  await page.getByRole("button", { name: `${documentRow.name} 더보기`, exact: true }).click();
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
  await page.getByRole("button", { name: "양식 보관함", exact: true }).click();
  await page.getByRole("button", { name: "기존 문서 양식", exact: true }).click();
  const select = async (name: string) => page.locator(".market-card").filter({ has: page.getByRole("heading", { name, exact: true }) }).getByRole("button", { name: "양식 보기", exact: true }).click();
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

test("MOCK UI: removing the last favorite on a later page returns to the remaining templates", async ({ page }) => {
  const templates = Array.from({ length: 25 }, (_, index) => ({
    id: `template-${index + 1}`, name: `양식 ${index + 1}`, description: "합성 페이지 시험", tags: [],
    document_id: `document-${index + 1}`, version_id: "fixed-version", category: "보고서", revision: 1,
    favorite: true, can_manage: false, created_at: 1, sha256: "a".repeat(64), size: 10, source_name: "sample.hwpx",
  }));
  const reads: { offset: number; total: number }[] = [];
  await mockApi(page, async (route, path) => {
    if (path === "/template-market") {
      const params = new URL(route.request().url()).searchParams;
      const rows = templates.filter(item => params.get("favorite") !== "true" || item.favorite);
      const offset = Number(params.get("offset"));
      reads.push({ offset, total: rows.length });
      await json(route, { items: rows.slice(offset, offset + 24), total: rows.length, categories: [] });
      return true;
    }
    if (path === "/template-market/template-25/favorite") {
      templates[24].favorite = route.request().postDataJSON().favorite;
      await json(route, templates[24]);
      return true;
    }
  });
  await library(page);
  await page.getByRole("button", { name: "양식 보관함", exact: true }).click();
  await page.getByRole("button", { name: "기존 문서 양식", exact: true }).click();
  await page.getByRole("checkbox", { name: "즐겨찾기만", exact: true }).check();
  await page.getByRole("button", { name: "다음", exact: true }).click();
  await expect(page.locator(".market-card")).toHaveCount(1);
  await page.getByRole("button", { name: "양식 25 즐겨찾기", exact: true }).click();
  await expect(page.locator(".market-card")).toHaveCount(24);
  await expect(page.getByRole("heading", { name: "양식 1", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "양식 25", exact: true })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "양식 목록 페이지", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "조건에 맞는 양식이 없습니다", exact: true })).toHaveCount(0);
  expect(reads).toContainEqual({ offset: 24, total: 24 });
  expect(reads.at(-1)).toEqual({ offset: 0, total: 24 });
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

