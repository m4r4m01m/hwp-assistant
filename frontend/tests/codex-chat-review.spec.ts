import { test, expect, type Page, type Route } from "@playwright/test";
import { createHash } from "node:crypto";

// UI transport/selection regressions only. This synthetic adapter does not prove
// native HWPX round trips or a real Codex model response.
const original = "합성 원문입니다.";
const originalHash = createHash("sha256").update(original).digest("hex");
const documentRow = { id: "synthetic-document", name: "합성 검토.hwpx", current_version: "version-one", role: "owner", created_at: 1 };
const writingContext = { revision: 0, base_version: "version-one", purpose: "", audience: "", tone: "", background: "", requirements: "", restrictions: "", facts: "", terminology: "", confirmation: null, stale: false };
const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, json: body });

async function setup(page: Page, options: { provider?: string; allowed?: boolean; configured?: boolean; reachable?: boolean | null; badHash?: boolean } = {}) {
  const provider = options.provider || "codex";
  const feature = { provider, configured: options.configured ?? true, reachable: options.reachable ?? null,
    allowed: options.allowed ?? true, reason_code: options.allowed === false ? "not_allowed" : null,
    message: options.allowed === false ? "이 계정은 Codex를 사용할 수 없습니다." : "", supports_stream: false };
  let modelCalls = 0, chatCalls = 0;
  const reviewRequests: { body: any; lease?: string }[] = [];
  const messages: any[] = [];
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname.replace(/^.*\/api/, "");
    if (path === "/auth/me") return json(route, { user_id: "synthetic-owner", authentication_required: true });
    if (path === "/capabilities") return json(route, { features: Object.fromEntries(["chat", "review", "rewrite", "reports"].map(name => [name, feature])) });
    if (path === "/documents") return json(route, [documentRow]);
    if (path === "/documents/synthetic-document/session") return json(route, route.request().method() === "POST" ? { token: "synthetic-lease", document: documentRow } : {});
    if (path.endsWith("/file")) return route.fulfill({ body: "synthetic bytes" });
    if (path === "/documents/synthetic-document/versions" && route.request().method() === "POST") return json(route, { id: "version-two" });
    if (path.endsWith("/threads")) return json(route, [{ id: "synthetic-thread", title: "합성 대화", visibility: "document" }]);
    if (path === "/threads/synthetic-thread") return json(route, { id: "synthetic-thread", title: "합성 대화", visibility: "document", context: writingContext, messages });
    if (path === "/threads/synthetic-thread/messages") {
      const input = route.request().postDataJSON();
      const row = { id: `message-${messages.length + 1}`, author_id: "synthetic-owner", role: "user", content: input.content, source: "user" };
      messages.push(row); return json(route, row);
    }
    if (path === "/threads/synthetic-thread/chat") {
      chatCalls++; messages.push({ id: "answer-one", author_id: "synthetic-owner", role: "assistant", content: "완료한 합성 답변", source: provider });
      return json(route, { ok: true });
    }
    if (path === "/ai/models") { modelCalls++; return json(route, { detail: "synthetic HTTP provider unavailable" }, 503); }
    if (path === "/ai/review") {
      const body = route.request().postDataJSON(); reviewRequests.push({ body, lease: route.request().headers()["x-edit-session"] });
      return json(route, { replacement: "합성 수정안입니다.", reason: "합성 표현 개선", original_sha256: options.badHash ? "0".repeat(64) : originalHash });
    }
    if (path === "/document-assistant-provider" || path === "/rewrite-provider") return json(route, { provider, configured: false, allowed: false, reason: "이 합성 시험에서는 문서 대화·수정 후보를 실행하지 않습니다.", model: null });
    if (path.endsWith("/proposals") || path.endsWith("/assistant-turns") || path.endsWith("/rewrite-tasks") || path.endsWith("/versions")) return json(route, []);
    if (path === "/document-blocks") return json(route, { blocks: [], standard_reference_sha256: "a".repeat(64), standard_profile_sha256: "b".repeat(64) });
    return json(route, { detail: `Unexpected synthetic request: ${path}` }, 404);
  });
  await page.route("**/rhwp/build-info.json", route => route.fulfill({ status: 503, body: "no engine in mocked UI test" }));
  await page.route("**/src/editor.ts", route => route.fulfill({ contentType: "text/javascript", body: `
    export class EditorAdapter {
      static async mount(host) {
        window.__reviewBody = ${JSON.stringify(original)}; window.__reviewRevision = 0; window.__reviewApplications = 0;
        const frame = document.createElement('iframe'); frame.title = '합성 편집기'; frame.srcdoc = '<p>합성 문서</p>'; host.append(frame);
        const instance = new EditorAdapter();
        const state = () => ({ documentEpoch: 1, changeSeq: window.__reviewRevision, documentSha256: 'a'.repeat(64) });
        instance.sdk = { element: frame, getDocumentState: async () => state(), destroy: () => frame.remove() };
        instance.bridge = { revision: () => window.__reviewRevision, warnings: () => null, subscribe: callback => { window.__reviewChanged = callback; return () => {}; }, newDocument: async () => {}, documentState: state, acknowledge: async () => true };
        return instance;
      }
      async load() { return this.sdk.getDocumentState(); }
      selection() { return { revision: window.__reviewRevision, state: this.bridge.documentState(), selection: { editable: true, target: { kind: 'body_paragraph', section: 0, paragraph: 0, charOffset: 0, length: window.__reviewBody.length } }, evidence: { text: window.__reviewBody, textSha256: '${originalHash}', formatSha256: 'b'.repeat(64), adjacentContextSha256: 'c'.repeat(64) } }; }
      exportHwpx() { return { bytes: new TextEncoder().encode(window.__reviewBody), epoch: 1, revision: window.__reviewRevision, contentLoss: { count: 0, losses: [] } }; }
      async apply(snapshot, replacement) { if (snapshot.evidence.text !== window.__reviewBody || snapshot.revision !== window.__reviewRevision) throw Error('합성 원문 fence'); window.__reviewBody = replacement; window.__reviewApplications++; window.__reviewRevision++; window.__reviewChanged?.(); }
      destroy() { this.sdk.destroy(); }
    }
  ` }));
  await page.goto("/");
  await page.getByRole("button", { name: documentRow.name, exact: true }).click();
  await expect(page.locator(".editor-current-document [role=status]")).toHaveText("저장 완료");
  return { counts: () => ({ modelCalls, chatCalls }), reviewRequests };
}

async function openReview(page: Page) {
  await page.getByLabel("추가 도구", { exact: true }).click();
  await page.getByRole("button", { name: "AI 검토 열기", exact: true }).click();
  return page.getByLabel("문단 AI 검토 패널", { exact: true });
}

test("MOCK Codex review: HTTP model discovery is not required; lease, original hash and explicit approval remain fenced", async ({ page }) => {
  const transport = await setup(page);
  const panel = await openReview(page);
  await expect(panel.getByRole("button", { name: "현재 문단 검토", exact: true })).toBeEnabled();
  await panel.getByRole("button", { name: "AI 연결 확인", exact: true }).click();
  await panel.getByRole("button", { name: "현재 문단 검토", exact: true }).click();
  await expect(panel.getByText("합성 수정안입니다.", { exact: true })).toBeVisible();
  expect(transport.counts().modelCalls).toBe(0);
  expect(transport.reviewRequests).toHaveLength(1);
  expect(transport.reviewRequests[0]).toMatchObject({ lease: "synthetic-lease", body: { document_id: documentRow.id, base_version: "version-one", revision: 0, original } });
  expect(await page.evaluate(() => (window as any).__reviewApplications)).toBe(0);
  await panel.getByRole("button", { name: "승인하고 적용", exact: true }).click();
  expect(await page.evaluate(() => (window as any).__reviewApplications)).toBe(1);
});

test("MOCK Codex review: mismatched original hash cannot become an applicable suggestion", async ({ page }) => {
  await setup(page, { badHash: true });
  const panel = await openReview(page);
  await panel.getByRole("button", { name: "현재 문단 검토", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("원문 검증 실패");
  await expect(panel.getByRole("button", { name: "승인하고 적용", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__reviewApplications)).toBe(0);
});

test("MOCK Codex review: saving a newer base version blocks applying an older suggestion", async ({ page }) => {
  await setup(page);
  const panel = await openReview(page);
  await panel.getByRole("button", { name: "현재 문단 검토", exact: true }).click();
  await expect(panel.getByRole("button", { name: "승인하고 적용", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.locator(".editor-current-document [role=status]")).toHaveText("저장 완료");
  await panel.getByRole("button", { name: "승인하고 적용", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("문서 버전이 바뀌었습니다");
  expect(await page.evaluate(() => (window as any).__reviewApplications)).toBe(0);
});

for (const options of [{ allowed: false }, { configured: false }, { reachable: false }]) {
  test(`MOCK Codex review: unavailable capability fences new generation ${JSON.stringify(options)}`, async ({ page }) => {
    const transport = await setup(page, options);
    const panel = await openReview(page);
    await expect(panel.getByRole("button", { name: "현재 문단 검토", exact: true })).toBeDisabled();
    expect(transport.reviewRequests).toHaveLength(0);
    expect(transport.counts().modelCalls).toBe(0);
  });
}

for (const provider of ["codex", "llm"]) {
  test(`MOCK chat/review provider routing: ${provider} uses its own readiness gate`, async ({ page }) => {
    const transport = await setup(page, { provider });
    const review = await openReview(page);
    const runReview = review.getByRole("button", { name: "현재 문단 검토", exact: true });
    if (provider === "llm") {
      await expect(runReview).toBeDisabled();
      await review.getByRole("button", { name: "AI 연결 확인", exact: true }).click();
      await expect(review.getByRole("alert")).toContainText("별도 AI에 연결할 수 없습니다");
      await expect(runReview).toBeDisabled();
    }
    await review.getByRole("button", { name: "검토 패널 닫기", exact: true }).click();
    await page.getByRole("button", { name: "문서 AI 대화 열기", exact: true }).click();
    await page.getByRole("button", { name: "메모", exact: true }).click();
    await page.getByText("별도 AI 연결", { exact: true }).click();
    await page.getByRole("textbox", { name: "대화 메시지", exact: true }).fill("합성 메모를 바탕으로 답해주세요.");
    const ask = page.getByRole("button", { name: "메모 내용으로 별도 AI에 질문", exact: true });
    if (provider === "codex") {
      await expect(ask).toBeEnabled();
      await page.getByRole("button", { name: "별도 AI 연결 확인", exact: true }).click();
      await expect(ask).toBeEnabled();
      await ask.click();
      await expect(page.getByLabel("저장된 대화", { exact: true }).getByText("완료한 합성 답변", { exact: true })).toBeVisible();
      expect(transport.counts()).toEqual({ modelCalls: 0, chatCalls: 1 });
    } else {
      await expect(ask).toBeDisabled();
      await page.getByRole("button", { name: "별도 AI 연결 확인", exact: true }).click();
      await expect(page.getByText("서버에서 요청을 처리하지 못했습니다.", { exact: false })).toBeVisible();
      await expect(ask).toBeDisabled();
      expect(transport.counts()).toEqual({ modelCalls: 2, chatCalls: 0 });
    }
  });
}
