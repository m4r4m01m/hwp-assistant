import { test, expect, type Page, type Route } from "@playwright/test";

const json = (route: Route, value: unknown) => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
const task = (id: string, contextUsed = true) => ({
  id, document_id: "synthetic-document", base_version: "saved-version", requested_by: "test-user",
  status: "ready", stale: false, context_stale: false, error: "", instruction: "합성 개선 요청",
  snapshot: { revision: 0, evidence: { text: "보존할 원문" } },
  execution: { provider: "llm", model: "synthetic", completed: true, context_used: contextUsed,
    ...(contextUsed ? { context_revision: 1 } : {}) },
  candidates: [{ id: `${id}-candidate`, ordinal: 1, replacement: "검토할 수정 후보", reason: "합성 이유" }],
  application: null,
});
const tasks = () => [
  task("context-task"), task("plain-task", false),
  { ...task("approved-task"), application: { id: "approved-application", candidate_id: "approved-task-candidate",
    status: "approved", saved_version: null, approved_by: "test-user" } },
  { ...task("saved-task"), base_version: "previous-version", stale: true,
    application: { id: "saved-application", candidate_id: "saved-task-candidate",
      status: "saved", saved_version: "saved-version", approved_by: "test-user" } },
];
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}
async function openPanel(page: Page) {
  await page.route("**/api/rewrite-provider", route => json(route, {
    provider: "llm", model: "synthetic", configured: true, allowed: true, reason: "",
  }));
  await page.route("**/api/capabilities", route => json(route, {
    features: Object.fromEntries(["rewrite", "chat", "review", "reports"].map(name => [name, {
      configured: true, reachable: null, allowed: true, reason_code: null, message: "", supports_stream: false,
    }])),
  }));
  await page.route("**/rewrite-lifecycle-test.html", route => route.fulfill({ contentType: "text/html",
    body: '<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/rewrite-lifecycle.tsx"></script></body></html>',
  }));
  await page.clock.install();
  await page.goto("/rewrite-lifecycle-test.html");
  await expect(page.locator('[data-task-id="context-task"]')).toBeVisible();
  await page.getByTestId("rewrite-history").locator(":scope > summary").click();
}
const candidate = (page: Page, id: string) => page.locator(`[data-task-id="${id}"]`)
  .getByRole("button", { name: "후보 1 승인·적용·저장", exact: true });

test("MOCK rewrite lifecycle: changed context immediately fences new approval while preserving exact application retries", async ({ page }) => {
  const revalidation = deferred();
  let reads = 0, delivered = false;
  const approvals: { task: string; body: unknown }[] = [];
  await page.route("**/api/threads/thread-rewrite/rewrite-tasks", async route => {
    const read = ++reads;
    if (read === 2) await revalidation.promise;
    // Keep the old false stale flag: local known inputs must win even if a
    // delayed history response was captured before the context save.
    await json(route, tasks());
    if (read === 2) delivered = true;
  });
  await page.route("**/api/rewrite-tasks/*/approve", async route => {
    approvals.push({ task: new URL(route.request().url()).pathname.split("/").at(-2)!, body: route.request().postDataJSON() });
    await json(route, {});
  });
  try {
    await openPanel(page);
    await expect(candidate(page, "context-task")).toBeEnabled();
    await page.clock.runFor(6000);
    expect(reads).toBe(1);
    await page.getByRole("button", { name: "작성 정보 리비전 2 반영", exact: true }).click();
    await expect(candidate(page, "context-task")).toBeDisabled();
    await expect(page.locator('[data-task-id="context-task"]')).toContainText("작성 정보가 변경되었습니다");
    await expect.poll(() => reads).toBe(2);
    await expect(candidate(page, "plain-task")).toBeEnabled();
    await expect(candidate(page, "approved-task")).toBeEnabled();
    const saved = page.locator('[data-task-id="saved-task"]').getByRole("button", { name: "저장 완료 확인", exact: true });
    await expect(saved).toBeEnabled();
    expect(approvals).toHaveLength(0);
    await page.clock.runFor(6000);
    expect(reads).toBe(2);
    revalidation.resolve();
    await expect.poll(() => delivered).toBe(true);
    await expect(candidate(page, "context-task")).toBeDisabled();
    await candidate(page, "approved-task").click();
    await expect(candidate(page, "approved-task")).toBeEnabled();
    await saved.click();
    await expect(saved).toBeEnabled();
    expect(approvals).toEqual([
      { task: "approved-task", body: { id: "approved-application", candidate_id: "approved-task-candidate" } },
      { task: "saved-task", body: { id: "saved-application", candidate_id: "saved-task-candidate" } },
    ]);
  } finally { revalidation.resolve(); }
});

test("MOCK rewrite lifecycle: new document version fences new approvals while preserving application retries during revalidation", async ({ page }) => {
  const revalidation = deferred();
  let reads = 0, delivered = false;
  await page.route("**/api/threads/thread-rewrite/rewrite-tasks", async route => {
    const read = ++reads;
    if (read === 2) await revalidation.promise;
    await json(route, tasks());
    if (read === 2) delivered = true;
  });
  try {
    await openPanel(page);
    await expect(candidate(page, "plain-task")).toBeEnabled();
    await page.getByRole("button", { name: "새 문서 저장본 반영", exact: true }).click();
    await expect(candidate(page, "context-task")).toBeDisabled();
    await expect(candidate(page, "plain-task")).toBeDisabled();
    await expect(candidate(page, "approved-task")).toBeEnabled();
    await expect(page.locator('[data-task-id="plain-task"]')).toContainText("기준 버전 충돌");
    await expect.poll(() => reads).toBe(2);
    revalidation.resolve();
    await expect.poll(() => delivered).toBe(true);
    await expect(candidate(page, "context-task")).toBeDisabled();
    await expect(candidate(page, "plain-task")).toBeDisabled();
    await expect(candidate(page, "approved-task")).toBeEnabled();
    await expect(page.locator('[data-task-id="saved-task"]').getByRole("button", { name: "저장 완료 확인", exact: true })).toBeEnabled();
    await page.clock.runFor(6000);
    expect(reads).toBe(2);
  } finally { revalidation.resolve(); }
});
