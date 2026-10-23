import { test, expect, type Page, type Frame, type APIRequestContext } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

test.use({ baseURL: process.env.HWP_E2E_LOCAL_URL || "http://127.0.0.1:15212" });
test.skip(!process.env.HWP_E2E_LOCAL_URL || !process.env.HWP_E2E_MOCK_URL,
  "Requires the isolated synthetic HTTP provider; no actual model calls");

const digest = (value: string | Uint8Array) => createHash("sha256").update(value).digest("hex");
const mockUrl = () => process.env.HWP_E2E_MOCK_URL!;
async function focusEnd(page: Page, frame: Frame) {
  await frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
}
async function liveBytes(frame: Frame) {
  return Uint8Array.from(await frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
}
async function assistantCalls(request: APIRequestContext) {
  return (await (await request.get(mockUrl() + "/proof")).json()).calls
    .filter((call: any) => call.input._assistant_contract === "live-document-conversation-v1");
}
async function setup(page: Page, request: APIRequestContext, empty = false) {
  await request.post(mockUrl() + "/controls", { data: {} });
  await page.addInitScript(() => { window.prompt = () => { throw new Error("Native prompt unavailable in this embedded browser"); }; });
  await page.goto("/");
  await page.getByRole("button", { name: "빈 문서 만들기", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
  const frame = page.frames().find(candidate => candidate.url().includes("/rhwp/"))!;
  await focusEnd(page, frame);
  if (!empty) {
    await page.keyboard.insertText("보존 문단: 담당 부서는 운영팀입니다.");
    await page.keyboard.press("Enter");
    await page.keyboard.insertText("김민수는 10월 9일까지 30만원을 쓰지 않는다.");
  }
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const originalUrl = (await page.getByRole("link", { name: "저장본 다운로드" }).getAttribute("href"))!;
  const docPath = originalUrl.split("/versions/")[0];
  const original = await (await request.get(originalUrl)).body();
  await page.getByRole("button", { name: "문서 AI 대화 열기", exact: true }).click();
  await page.getByRole("textbox", { name: "대화 이름", exact: true }).fill("합성 문서 대화 시험");
  await page.getByRole("button", { name: "AI와 대화 시작", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "AI에게 요청", exact: true })).toBeVisible();
  const thread = await page.getByLabel("대화 선택", { exact: true }).inputValue();
  await expect(page.getByRole("button", { name: "보내기", exact: true })).toBeDisabled();
  return { frame, thread, originalUrl, docPath, original };
}
async function ask(page: Page, request: APIRequestContext, thread: string, message: string) {
  await page.getByRole("textbox", { name: "AI에게 요청", exact: true }).fill(message);
  const accepted = page.waitForResponse(response => response.request().method() === "POST" && /\/assistant-turns$/.test(response.url()));
  await page.getByRole("button", { name: "보내기", exact: true }).click();
  const response = await accepted;
  expect(response.ok(), await response.text()).toBeTruthy();
  const acceptedTurn = await response.json();
  const path = `/api/threads/${thread}/assistant-turns`;
  await expect.poll(async () => (await (await request.get(path)).json()).find((turn: any) => turn.id === acceptedTurn.id)?.status).toBe("ready");
  const turn = (await (await request.get(path)).json()).find((value: any) => value.id === acceptedTurn.id);
  const article = page.locator(`[data-assistant-turn-id="${turn.id}"]`);
  await expect(article).toContainText("MOCK 문서 대화 응답: " + message);
  return { turn, article, body: response.request().postDataJSON() };
}

test("REAL rhwp + MOCK document assistant: live unsaved body and table reach questions, follow-up history, and preview without edits", async ({ page, request }, info) => {
  const state = await setup(page, request);
  await focusEnd(page, state.frame);
  await page.keyboard.insertText(" 지급 승인은 하지 않았다.");
  await page.keyboard.press("Enter");
  await state.frame.locator('button[data-cmd="table:create"]').click();
  const cell = state.frame.locator('[data-row="1"][data-col="1"]');
  await cell.hover(); await cell.click();
  await page.keyboard.insertText("미저장 표 예산 30만원");
  await page.keyboard.press("Tab");
  await page.keyboard.insertText("미저장 표 담당 운영팀");
  const before = await liveBytes(state.frame);
  const versionCount = (await (await request.get(state.docPath + "/versions")).json()).length;
  await page.getByRole("button", { name: "AI가 읽을 내용 확인", exact: true }).click();
  await expect(page.locator(".assistant-context-text")).toContainText("지급 승인은 하지 않았다.");
  await expect(page.locator(".assistant-context-text")).toContainText("미저장 표 예산 30만원");
  await expect(page.locator(".assistant-context-text")).toContainText("미저장 표 담당 운영팀");
  const first = await ask(page, request, state.thread, "미저장 본문과 표의 담당 부서를 알려 주세요.");
  await expect(page.locator(".assistant-context-text")).toBeHidden();
  expect(first.body.document_context.text).toContain("지급 승인은 하지 않았다.");
  expect(first.body.document_context.text).toContain("미저장 표 예산 30만원");
  expect(first.body.document_context.text.split("미저장 표 예산 30만원")).toHaveLength(2);
  expect(first.body.document_context.text.indexOf("지급 승인은 하지 않았다.")).toBeLessThan(
    first.body.document_context.text.indexOf("미저장 표 예산 30만원"),
  );
  expect(first.body.document_context.sha256).toBe(digest(first.body.document_context.text));
  expect(first.body.selection).toBeNull();
  await expect(first.article.getByRole("button", { name: "이 문단 수정 후보 만들기", exact: true })).toHaveCount(0);
  const second = await ask(page, request, state.thread, "방금 질문에 이어 표의 금액도 알려 주세요.");
  await expect(page.getByRole("textbox", { name: "AI에게 요청", exact: true })).toBeInViewport();
  const lastAnswer = second.article.locator(".assistant-answer");
  await expect.poll(async () => {
    const [answer, composer] = await Promise.all([lastAnswer.boundingBox(), page.locator(".assistant-composer").boundingBox()]);
    return answer && composer ? Math.ceil(answer.y + answer.height) - Math.floor(composer.y) : 10000;
  }).toBeLessThanOrEqual(0);
  await expect(lastAnswer).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }))).toEqual({ x: 0, y: 0 });
  const calls = await assistantCalls(request);
  const secondInput = calls.find((call: any) => call.input.id === second.turn.id).input;
  expect(secondInput.history).toEqual(expect.arrayContaining([expect.objectContaining({
    message: first.body.message, answer: first.turn.answer,
  })]));
  expect(digest(await liveBytes(state.frame))).toBe(digest(before));
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(versionCount);
  expect(await (await request.get(state.originalUrl)).body()).toEqual(state.original);
  await page.screenshot({ path: info.outputPath("live-document-conversation.png"), fullPage: true });
  await writeFile(info.outputPath("assistant-proof.json"), JSON.stringify({ engine: "real rhwp", actual_model_calls: 0,
    unsaved_body_and_table_context: true, follow_up_history: true, question_changes_document: false,
    latest_reply_above_composer: true, outer_scroll_unchanged: true,
    frozen_context_sha256: first.body.document_context.sha256, original_blob_preserved: true }, null, 2));
});

test("REAL rhwp + MOCK document assistant: lost acceptance response keeps frozen request and later composer with one generation", async ({ page, request }) => {
  const state = await setup(page, request);
  await focusEnd(page, state.frame);
  await page.keyboard.insertText(" 아직 저장하지 않은 설명.");
  const bodies: any[] = [];
  let dropped = false;
  await page.route(`**/api/threads/${state.thread}/assistant-turns`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    bodies.push(route.request().postDataJSON());
    if (!dropped) { dropped = true; await route.fetch(); return route.abort("failed"); }
    return route.continue();
  });
  await page.getByRole("textbox", { name: "AI에게 요청", exact: true }).fill("응답 유실을 확인할 첫 질문입니다.");
  await page.getByRole("button", { name: "보내기", exact: true }).click();
  await expect(page.getByRole("button", { name: "같은 질문 접수 재확인", exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "AI에게 요청", exact: true }).fill("나중에 전송할 새 질문을 작성 중입니다.");
  await page.getByRole("button", { name: "같은 질문 접수 재확인", exact: true }).click();
  await expect(page.locator(`[data-assistant-turn-id="${bodies[0].id}"]`)).toContainText("MOCK 문서 대화 응답");
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toEqual(bodies[0]);
  await expect(page.getByRole("textbox", { name: "AI에게 요청", exact: true })).toHaveValue("나중에 전송할 새 질문을 작성 중입니다.");
  const calls = await assistantCalls(request);
  expect(calls.filter((call: any) => call.input.id === bodies[0].id)).toHaveLength(1);
  expect(await (await request.get(`/api/threads/${state.thread}/assistant-turns`)).json()).toHaveLength(1);
});

test("REAL rhwp + MOCK document assistant: moving selection after advice blocks creation against the wrong paragraph", async ({ page, request }) => {
  const state = await setup(page, request);
  await focusEnd(page, state.frame);
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("선택 변경을 확인할 문단입니다.");
  const answer = await ask(page, request, state.thread, "현재 문단을 보고서 문체로 다듬어 주세요.");
  expect(answer.body.selection.evidence.text).toContain("선택 변경을 확인할 문단");
  await focusEnd(page, state.frame);
  await page.keyboard.press("Control+Home");
  // The first paragraph owns native section controls. Move to the next plain
  // paragraph to check a changed editable target, not that unsupported control.
  await page.keyboard.press("ArrowDown");
  await expect.poll(() => state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toContain("30만원");
  const before = await liveBytes(state.frame);
  await answer.article.getByRole("button", { name: "이 문단 수정 후보 만들기", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: /선택|문단|위치/ })).toBeVisible();
  expect(await (await request.get(`/api/threads/${state.thread}/rewrite-tasks`)).json()).toHaveLength(0);
  expect(digest(await liveBytes(state.frame))).toBe(digest(before));
});

test("REAL rhwp + MOCK document assistant: advice creates approved fenced rewrite and saved document reopens", async ({ page, request }, info) => {
  const state = await setup(page, request);
  await focusEnd(page, state.frame);
  const answer = await ask(page, request, state.thread, "금액과 부정 표현을 보존해서 문단을 다듬어 주세요.");
  const acceptance = page.waitForResponse(response => response.request().method() === "POST" && /\/rewrite-tasks$/.test(response.url()));
  await answer.article.getByRole("button", { name: "이 문단 수정 후보 만들기", exact: true }).click();
  const response = await acceptance;
  expect(response.ok(), await response.text()).toBeTruthy();
  const rewrite = await response.json();
  expect(response.request().postDataJSON().selection).toEqual(answer.body.selection);
  await expect.poll(async () => (await (await request.get(`/api/threads/${state.thread}/rewrite-tasks`)).json()).find((value: any) => value.id === rewrite.id)?.status).toBe("ready");
  const article = page.locator(`[data-task-id="${rewrite.id}"]`);
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
  await article.getByRole("button", { name: "후보 1 승인·적용·저장", exact: true }).click();
  await expect(article.getByTestId("application-history")).toContainText("선택한 후보를 문서에 적용하고 서버에 저장했습니다.");
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const savedUrl = (await page.getByRole("link", { name: "저장본 다운로드", exact: true }).getAttribute("href"))!;
  const saved = await (await request.get(savedUrl)).body();
  const section = strFromU8(unzipSync(saved)["Contents/section0.xml"]);
  expect(section).toContain("김민수는 10월 9일까지 30만원을 쓰지 않는다. 확인했습니다.");
  expect(section).toContain("담당 부서는 운영팀입니다.");
  expect(await (await request.get(state.originalUrl)).body()).toEqual(state.original);
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(2);
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  const docId = state.docPath.split("/").at(-1);
  const row = page.getByRole("row").filter({ has: page.locator(`a[href*="/documents/${docId}/versions/"]`) });
  await row.getByRole("button", { name: "새 문서.hwpx", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const reopened = page.frames().find(candidate => candidate.url().includes("/rhwp/"))!;
  await focusEnd(page, reopened);
  await expect.poll(() => reopened.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toContain("30만원을 쓰지 않는다. 확인했습니다.");
  await page.screenshot({ path: info.outputPath("assistant-rewrite-reopened.png"), fullPage: true });
});

test("REAL rhwp + MOCK document assistant: empty document explains missing context without dispatch or discarded question", async ({ page, request }) => {
  const state = await setup(page, request, true);
  const before = await liveBytes(state.frame);
  const callsBefore = (await assistantCalls(request)).length;
  await page.getByRole("textbox", { name: "AI에게 요청", exact: true }).fill("이 문서를 요약해 주세요.");
  await page.getByRole("button", { name: "AI가 읽을 내용 확인", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "AI가 읽을 본문이 없습니다" })).toBeVisible();
  await page.getByRole("button", { name: "보내기", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "AI가 읽을 본문이 없습니다" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "AI에게 요청", exact: true })).toHaveValue("이 문서를 요약해 주세요.");
  expect(await (await request.get(`/api/threads/${state.thread}/assistant-turns`)).json()).toHaveLength(0);
  expect(await assistantCalls(request)).toHaveLength(callsBefore);
  expect(digest(await liveBytes(state.frame))).toBe(digest(before));
});

test("REAL rhwp + MOCK document assistant: active answer locks an already-open conversation switch confirmation", async ({ page, request }) => {
  const state = await setup(page, request);
  await request.post(mockUrl() + "/controls", { data: { hold: true } });
  try {
    await page.getByRole("textbox", { name: "AI에게 요청", exact: true }).fill("응답 중 대화 전환을 확인할 질문입니다.");
    await page.getByRole("button", { name: "새 대화", exact: true }).click();
    await page.getByRole("textbox", { name: "대화 이름", exact: true }).fill("응답 뒤에 열 대화");
    await page.getByRole("button", { name: "AI와 대화 시작", exact: true }).click();
    const confirm = page.getByRole("group", { name: "대화 전환 확인", exact: true });
    await expect(confirm).toBeVisible();
    const accepted = page.waitForResponse(response => response.request().method() === "POST" && /\/assistant-turns$/.test(response.url()));
    await page.getByRole("button", { name: "보내기", exact: true }).click();
    const response = await accepted;
    expect(response.ok(), await response.text()).toBeTruthy();
    const turn = await response.json();
    const article = page.locator(`[data-assistant-turn-id="${turn.id}"]`);
    await expect(article.getByRole("status")).toContainText("답변 작성 중");
    await expect(confirm.getByRole("button", { name: "입력 버리고 이동", exact: true })).toBeDisabled();
    await expect(page.getByLabel("대화 선택", { exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "문서 AI 대화 닫기", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "AI 응답을 확인하거나 수신을 중단한 뒤 대화를 닫으세요" })).toBeVisible();
    await expect(page.getByLabel("대화 선택", { exact: true })).toHaveValue(state.thread);
    expect(await (await request.get(state.docPath + "/threads")).json()).toHaveLength(1);
    await request.post(mockUrl() + "/release");
    await expect(article).toContainText("MOCK 문서 대화 응답");
    await expect(confirm.getByRole("button", { name: "입력 버리고 이동", exact: true })).toBeEnabled();
    await confirm.getByRole("button", { name: "입력 버리고 이동", exact: true }).click();
    await expect(page.getByLabel("대화 선택", { exact: true })).not.toHaveValue(state.thread);
    expect(await (await request.get(state.docPath + "/threads")).json()).toHaveLength(2);
    expect(await (await request.get(state.originalUrl)).body()).toEqual(state.original);
  } finally { await request.post(mockUrl() + "/release"); await request.post(mockUrl() + "/controls", { data: {} }); }
});

test("REAL rhwp + MOCK document assistant: sequential reads preserve accepted and completed answers across stale history", async ({ page, request }) => {
  let releaseEmpty!: () => void, releaseRunning!: () => void;
  const emptyGate = new Promise<void>(resolve => { releaseEmpty = resolve; });
  const runningGate = new Promise<void>(resolve => { releaseRunning = resolve; });
  let phase: "empty" | "running" | "terminal" | "stale-running" | "stale-empty" | "done" = "empty";
  let capturedEmpty = false, capturedRunning = false, deliveredEmpty = false, deliveredRunning = false;
  let deliveredStaleRunning = false, deliveredStaleEmpty = false;
  let reads = 0, inFlight = 0, maxInFlight = 0, capturedThread = "";
  let runningRows: any[] = [];
  // An idle history no longer polls. Intercept its opening read before the
  // conversation mounts so it can arrive after the accepted POST instead.
  await page.route("**/api/threads/*/assistant-turns", async route => {
    if (route.request().method() !== "GET") return route.continue();
    reads++; inFlight++; maxInFlight = Math.max(maxInFlight, inFlight);
    capturedThread = new URL(route.request().url()).pathname.split("/").at(-2)!;
    const current = phase;
    phase = current === "empty" ? "running" : current === "running" ? "terminal" : "done";
    try {
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBeTruthy();
      const rows = await response.json();
      if (current === "empty") {
        expect(rows).toHaveLength(0); capturedEmpty = true;
        await emptyGate; await route.fulfill({ response }); deliveredEmpty = true;
      } else if (current === "running") {
        expect(rows).toHaveLength(1); expect(rows[0].status).toBe("running");
        runningRows = rows; capturedRunning = true;
        await runningGate; await route.fulfill({ response }); deliveredRunning = true;
      } else if (current === "stale-running") {
        await route.fulfill({ response, json: runningRows }); deliveredStaleRunning = true;
      } else if (current === "stale-empty") {
        await route.fulfill({ response, json: [] }); deliveredStaleEmpty = true;
      } else {
        if (current === "terminal") { expect(rows).toHaveLength(1); expect(rows[0].status).toBe("ready"); }
        await route.fulfill({ response });
      }
    } finally { inFlight--; }
  });
  try {
    const state = await setup(page, request);
    await expect.poll(() => capturedEmpty).toBe(true);
    expect(capturedThread).toBe(state.thread);
    await request.post(mockUrl() + "/controls", { data: { hold: true } });
    await page.getByRole("textbox", { name: "AI에게 요청", exact: true }).fill("늦게 도착한 목록 응답을 확인할 질문입니다.");
    const accepted = page.waitForResponse(response => response.request().method() === "POST" && /\/assistant-turns$/.test(response.url()));
    await page.getByRole("button", { name: "보내기", exact: true }).click();
    const response = await accepted;
    expect(response.ok(), await response.text()).toBeTruthy();
    const turn = await response.json();
    const article = page.locator(`[data-assistant-turn-id="${turn.id}"]`);
    await expect(article.getByRole("status")).toContainText("답변 작성 중");
    releaseEmpty(); await expect.poll(() => deliveredEmpty).toBe(true);
    await expect(article.getByRole("status")).toContainText("답변 작성 중");
    await expect.poll(() => capturedRunning).toBe(true);
    await request.post(mockUrl() + "/release");
    await expect.poll(async () => (await (await request.get(`/api/threads/${state.thread}/assistant-turns`)).json())
      .find((value: any) => value.id === turn.id)?.status).toBe("ready");
    // The backend is complete, but a second browser GET must not overtake the
    // held running read. Observe more than two normal 1.5-second poll periods.
    const heldReads = reads;
    expect(heldReads).toBe(2);
    await page.waitForTimeout(3400);
    expect(reads).toBe(heldReads);
    expect(maxInFlight).toBe(1);
    await expect(article.getByRole("status")).toContainText("답변 작성 중");
    releaseRunning(); await expect.poll(() => deliveredRunning).toBe(true);
    // Only after that read settles may the next poll reconcile the ready row.
    await expect(article).toContainText("MOCK 문서 대화 응답");
    expect(reads).toBe(3);
    await expect(article.getByRole("status")).toHaveCount(0);
    await expect(article.getByRole("button", { name: "응답 수신 중단", exact: true })).toHaveCount(0);
    await expect(page.getByLabel("대화 선택", { exact: true })).toBeEnabled();
    const reopenConversation = async () => {
      await page.getByLabel("추가 도구", { exact: true }).click();
      await page.getByRole("button", { name: "AI 검토 열기", exact: true }).click();
      await expect(page.getByLabel("대화 및 AI 패널", { exact: true })).toBeHidden();
      await page.getByRole("button", { name: "문서 AI 대화 열기", exact: true }).click();
    };
    // Completed histories stay idle. A deliberate hide/show supplies later
    // stale snapshots without requiring the overlapping polling we prohibit.
    phase = "stale-running"; await reopenConversation();
    await expect.poll(() => deliveredStaleRunning).toBe(true);
    await expect(article).toContainText("MOCK 문서 대화 응답");
    await expect(article.getByRole("status")).toHaveCount(0);
    await expect(article.getByRole("button", { name: "응답 수신 중단", exact: true })).toHaveCount(0);
    phase = "stale-empty"; await reopenConversation();
    await expect.poll(() => deliveredStaleEmpty).toBe(true);
    await expect(article).toContainText("MOCK 문서 대화 응답");
    await expect(article.getByRole("status")).toHaveCount(0);
    await expect(page.getByLabel("대화 선택", { exact: true })).toBeEnabled();
    expect(maxInFlight).toBe(1);
    expect((await assistantCalls(request)).filter((call: any) => call.input.id === turn.id)).toHaveLength(1);
  } finally {
    releaseEmpty(); releaseRunning();
    await request.post(mockUrl() + "/release"); await request.post(mockUrl() + "/controls", { data: {} });
  }
});

test("REAL rhwp + MOCK document assistant: access error releases navigation hold and preserves the unsent question", async ({ page, request }) => {
  const state = await setup(page, request);
  await request.post(mockUrl() + "/controls", { data: { hold: true } });
  let turnId = "";
  try {
    await page.getByRole("textbox", { name: "AI에게 요청", exact: true }).fill("권한 오류 화면을 확인할 질문입니다.");
    const accepted = page.waitForResponse(response => response.request().method() === "POST" && /\/assistant-turns$/.test(response.url()));
    await page.getByRole("button", { name: "보내기", exact: true }).click();
    const response = await accepted;
    expect(response.ok(), await response.text()).toBeTruthy();
    turnId = (await response.json()).id;
    await expect(page.locator(`[data-assistant-turn-id="${turnId}"]`).getByRole("status")).toContainText("답변 작성 중");
    // This is an injected UI boundary failure. Real ACL revocation is verified
    // in backend contract tests; it must not be claimed by this browser fixture.
    await page.route(`**/api/threads/${state.thread}/assistant-turns`, route => route.request().method() === "GET"
      ? route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ detail: "합성 접근 권한 해제" }) })
      : route.continue());
    await page.getByRole("textbox", { name: "AI에게 요청", exact: true }).fill("잃어버리면 안 되는 후속 질문 초안입니다.");
    await expect(page.getByRole("alert").filter({ hasText: "합성 접근 권한 해제" })).toBeVisible();
    await expect(page.getByLabel("대화 선택", { exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "보내기", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "문서 AI 대화 닫기", exact: true }).click();
    const dialog = page.getByRole("alertdialog", { name: "미저장 입력 확인", exact: true });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "계속 편집", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "AI에게 요청", exact: true })).toHaveValue("잃어버리면 안 되는 후속 질문 초안입니다.");
    expect(await (await request.get(state.originalUrl)).body()).toEqual(state.original);
  } finally {
    if (turnId) await request.post(`/api/assistant-turns/${turnId}/cancel`);
    await request.post(mockUrl() + "/release"); await request.post(mockUrl() + "/controls", { data: {} });
  }
});
