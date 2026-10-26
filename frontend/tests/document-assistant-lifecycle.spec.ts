import { test, expect, type Page, type Route } from "@playwright/test";

const provider = { provider: "llm", model: "synthetic", configured: true, allowed: true, reason: "" };
const turn = (thread = "thread-a", status = "running", answer: string | null = null) => ({
  id: `${thread}-turn`, conversation_id: thread, document_id: "synthetic-document", base_version: "saved-version",
  message: `${thread} 합성 질문`, status, answer, rewrite_instruction: null, selection: null,
  document_context: { sha256: "a".repeat(64), truncated: false, revision: 0, epoch: 1, characters: 5 },
  error: "", execution: { provider: "llm", model: "synthetic", completed: status !== "running" },
  created_at: 1, stale: false, can_cancel: status === "running",
});
const json = (route: Route, value: unknown) => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}
async function openPanel(page: Page, options: { provider?: typeof provider; rewriteAllowed?: () => boolean } = {}) {
  const selectedProvider = options.provider || provider;
  await page.route("**/api/document-assistant-provider", route => json(route, selectedProvider));
  await page.route("**/api/capabilities", route => json(route, {
    features: Object.fromEntries(["rewrite", "chat", "review", "reports"].map(name => {
      const allowed = name !== "rewrite" || (options.rewriteAllowed?.() ?? true);
      return [name, { configured: true, reachable: null, allowed, reason_code: allowed ? null : "not_allowed",
        message: allowed ? "" : "문단 수정 모델을 사용할 권한이 없습니다.", supports_stream: false }];
    })),
  }));
  await page.route("**/assistant-lifecycle-test.html", route => route.fulfill({
    contentType: "text/html", body: '<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/document-assistant-lifecycle.tsx"></script></body></html>',
  }));
  await page.clock.install();
  await page.goto("/assistant-lifecycle-test.html");
  await expect(page.getByRole("region", { name: "문서와 AI 대화" })).toBeVisible();
  await expect(page.getByText(`사용 AI: ${selectedProvider.provider === "codex" ? "Codex" : "연결한 모델"} · synthetic`, { exact: false })).toBeVisible();
}
async function visibility(page: Page, value: "hidden" | "visible") {
  await page.evaluate(state => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
    document.dispatchEvent(new Event("visibilitychange"));
  }, value);
}

test("MOCK assistant lifecycle: idle history does not poll and hiding preserves the unsent draft", async ({ page }) => {
  let reads = 0;
  await page.route("**/api/threads/thread-a/assistant-turns", route => { reads++; return json(route, []); });
  await openPanel(page);
  await expect.poll(() => reads).toBe(1);
  await page.getByLabel("AI에게 요청", { exact: true }).fill("보내지 않은 질문");
  await page.clock.runFor(6000);
  expect(reads).toBe(1);
  await page.getByRole("button", { name: "대화 패널 숨기기" }).click();
  await page.clock.runFor(6000);
  expect(reads).toBe(1);
  await page.getByRole("button", { name: "대화 패널 보이기" }).click();
  await expect.poll(() => reads).toBe(2);
  await expect(page.getByLabel("AI에게 요청", { exact: true })).toHaveValue("보내지 않은 질문");
  await page.clock.runFor(6000);
  expect(reads).toBe(2);
});

test("MOCK assistant lifecycle: slow running requests never overlap and completion stops polling", async ({ page }) => {
  let reads = 0, concurrent = 0, maximum = 0;
  const finish = deferred();
  await page.route("**/api/threads/thread-a/assistant-turns", async route => {
    reads++; concurrent++; maximum = Math.max(maximum, concurrent);
    if (reads > 1) await finish.promise;
    const result = reads === 1 ? turn() : turn("thread-a", "ready", "완료한 합성 답변");
    await json(route, [result]); concurrent--;
  });
  await openPanel(page);
  await expect(page.getByTestId("assistant-hold")).toHaveText("응답 확인 중");
  await page.clock.runFor(1500);
  await expect.poll(() => reads).toBe(2);
  await page.clock.runFor(7500);
  expect(reads).toBe(2); expect(maximum).toBe(1);
  finish.resolve();
  await expect(page.getByText("완료한 합성 답변", { exact: true })).toBeVisible();
  await expect(page.getByTestId("assistant-hold")).toHaveText("대기");
  await page.clock.runFor(6000);
  expect(reads).toBe(2);
});

for (const surface of ["panel", "tab"] as const) {
  test(`MOCK assistant lifecycle: hidden ${surface} aborts the active read and refreshes on return`, async ({ page }) => {
    let reads = 0, aborted = 0;
    const finishOld = deferred();
    page.on("requestfailed", request => { if (request.url().endsWith("/assistant-turns")) aborted++; });
    await page.route("**/api/threads/thread-a/assistant-turns", async route => {
      const request = ++reads;
      if (request === 2) {
        await finishOld.promise;
        await json(route, [{ ...turn("thread-a", "ready", "숨긴 뒤 늦게 도착한 답변"), id: "obsolete-turn" }]).catch(() => {});
      } else await json(route, [request === 1 ? turn() : turn("thread-a", "ready", "복귀 후 확인한 답변")]);
    });
    await openPanel(page);
    await expect(page.getByTestId("assistant-hold")).toHaveText("응답 확인 중");
    await page.clock.runFor(1500);
    await expect.poll(() => reads).toBe(2);
    if (surface === "panel") await page.getByRole("button", { name: "대화 패널 숨기기" }).click();
    else await visibility(page, "hidden");
    await expect.poll(() => aborted).toBe(1);
    await page.clock.runFor(6000);
    expect(reads).toBe(2);
    if (surface === "panel") await page.getByRole("button", { name: "대화 패널 보이기" }).click();
    else await visibility(page, "visible");
    await expect(page.getByText("복귀 후 확인한 답변", { exact: true })).toBeVisible();
    finishOld.resolve();
    await page.clock.runFor(6000);
    await expect(page.getByText("숨긴 뒤 늦게 도착한 답변", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(reads).toBe(3);
  });
}

test("MOCK assistant lifecycle: changing threads aborts old reads and cannot display the old thread", async ({ page }) => {
  const finishOld = deferred();
  let oldReads = 0, newReads = 0, aborted = 0;
  page.on("requestfailed", request => { if (request.url().includes("thread-a/assistant-turns")) aborted++; });
  await page.route("**/api/threads/thread-a/assistant-turns", async route => {
    oldReads++; await finishOld.promise;
    await json(route, [turn("thread-a", "ready", "이전 대화의 답변")]).catch(() => {});
  });
  await page.route("**/api/threads/thread-b/assistant-turns", route => { newReads++; return json(route, [turn("thread-b", "ready", "새 대화의 답변")]); });
  await openPanel(page);
  await expect.poll(() => oldReads).toBe(1);
  await page.getByRole("button", { name: "대화 B 열기" }).click();
  await expect(page.getByText("새 대화의 답변", { exact: true })).toBeVisible();
  await expect.poll(() => aborted).toBe(1);
  finishOld.resolve();
  await page.clock.runFor(6000);
  await expect(page.getByText("이전 대화의 답변", { exact: true })).toHaveCount(0);
  expect(oldReads).toBe(1); expect(newReads).toBe(1);
});

test("MOCK assistant lifecycle: temporary rate limiting respects Retry-After then reconciles once", async ({ page }) => {
  let reads = 0;
  await page.route("**/api/threads/thread-a/assistant-turns", route => {
    reads++;
    if (reads === 1) return route.fulfill({ status: 429, contentType: "application/json", headers: { "Retry-After": "5" }, body: '{"detail":"잠시 뒤 다시 확인하세요."}' });
    return json(route, [turn("thread-a", "ready", "재시도 후 확인한 답변")]);
  });
  await openPanel(page);
  await expect(page.getByRole("alert")).toHaveText("잠시 뒤 다시 확인하세요.");
  await page.clock.runFor(4500);
  expect(reads).toBe(1);
  await page.clock.runFor(500);
  await expect(page.getByText("재시도 후 확인한 답변", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.clock.runFor(6000);
  expect(reads).toBe(2);
});

test("MOCK assistant capability: Codex advice cannot dispatch a denied rewrite, but works after capability refresh", async ({ page }) => {
  let allowed = false, rewrites = 0;
  await page.route("**/api/threads/thread-a/assistant-turns", route => json(route, [{
    ...turn("thread-a", "ready", "현재 문단을 간결하게 바꾸는 방향을 제안합니다."),
    execution: { provider: "codex", completed: true }, rewrite_instruction: "사실을 유지하면서 간결하게 다듬어 주세요.",
    selection: { revision: 0, state: { documentEpoch: 1, changeSeq: 0, documentSha256: "a".repeat(64) },
      selection: { editable: true, target: { kind: "body_paragraph" } },
      evidence: { text: "합성 본문", textSha256: "a".repeat(64), formatSha256: "b".repeat(64), adjacentContextSha256: "c".repeat(64) } },
  }]));
  await page.route("**/api/threads/thread-a/rewrite-tasks", route => { rewrites++; return json(route, { id: "synthetic-rewrite" }); });
  await openPanel(page, { provider: { ...provider, provider: "codex" }, rewriteAllowed: () => allowed });
  const action = page.getByRole("button", { name: "이 문단 수정 후보 만들기", exact: true });
  await expect(action).toBeDisabled();
  await expect(page.getByText("문단 수정 모델을 사용할 권한이 없습니다.", { exact: true })).toBeVisible();
  expect(rewrites).toBe(0);
  allowed = true;
  await page.getByRole("button", { name: "사용 가능 여부 다시 확인", exact: true }).click();
  await expect(action).toBeEnabled();
  await action.click();
  await expect.poll(() => rewrites).toBe(1);
});

test("MOCK assistant edit: a fresh completed request prepares one rewrite and identifies its preview task", async ({ page }) => {
  const selection = { revision: 0, state: { documentEpoch: 1, changeSeq: 0, documentSha256: "a".repeat(64) },
    selection: { editable: true, target: { kind: "body_paragraph", section: 0, paragraph: 1, charOffset: 0, length: 5 } },
    evidence: { text: "합성 본문", textSha256: "a".repeat(64), formatSha256: "b".repeat(64), adjacentContextSha256: "c".repeat(64) } };
  let accepted: any, rewrites = 0;
  await page.route("**/api/threads/thread-a/assistant-turns", route => {
    if (route.request().method() === "POST") {
      const request = route.request().postDataJSON();
      accepted = { ...turn("thread-a", "ready", "요청에 맞춘 수정안을 비교할 수 있습니다."), id: request.id, selection,
        rewrite_instruction: "합성 본문의 사실을 유지하며 간결하게 정리하세요." };
      return json(route, accepted);
    }
    return json(route, accepted ? [accepted] : []);
  });
  await page.route("**/api/threads/thread-a/rewrite-tasks", route => { rewrites++; return json(route, { id: "automatic-rewrite" }); });
  await openPanel(page);
  await page.getByLabel("AI에게 요청", { exact: true }).fill("이 문단을 간결하게 다듬어줘");
  await page.getByRole("button", { name: "보내기", exact: true }).click();
  await expect(page.getByTestId("assistant-preview-task")).toHaveText("automatic-rewrite");
  await expect(page.getByRole("button", { name: "수정 후보 확인", exact: true })).toBeVisible();
  await page.clock.runFor(6000);
  await page.getByRole("button", { name: "대화 패널 숨기기" }).click();
  await page.getByRole("button", { name: "대화 패널 보이기" }).click();
  await page.clock.runFor(3000);
  expect(rewrites).toBe(1);
});

test("MOCK assistant layout: only messages scroll and the opaque composer occupies its own bottom row", async ({ page }) => {
  await page.route("**/api/threads/thread-a/assistant-turns", route => json(route, [turn("thread-a", "ready", "긴 합성 답변입니다.\n".repeat(100))]));
  await openPanel(page);
  await page.addStyleTag({ content: ".conversation-panel { height: 600px; width: 380px; overflow: hidden; }" });
  const bounds = await page.locator(".document-assistant").evaluate(element => {
    const scroll = element.querySelector<HTMLElement>(".assistant-scroll")!;
    const composer = element.querySelector<HTMLElement>(".assistant-composer")!;
    scroll.scrollTop = scroll.scrollHeight;
    return { scrollBottom: scroll.getBoundingClientRect().bottom, composerTop: composer.getBoundingClientRect().top,
      composerBottom: composer.getBoundingClientRect().bottom, panelBottom: element.getBoundingClientRect().bottom,
      scrollable: scroll.scrollHeight > scroll.clientHeight, background: getComputedStyle(composer).backgroundColor,
      position: getComputedStyle(composer).position };
  });
  expect(bounds.scrollable).toBe(true);
  expect(bounds.scrollBottom).toBeLessThanOrEqual(bounds.composerTop);
  expect(bounds.composerBottom).toBeLessThanOrEqual(bounds.panelBottom + 1);
  expect(bounds.background).toBe("rgb(255, 255, 255)");
  expect(bounds.position).toBe("static");
});
