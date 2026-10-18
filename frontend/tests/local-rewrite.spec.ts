import { test, expect, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

test.use({ baseURL: process.env.HWP_E2E_LOCAL_URL || "http://127.0.0.1:15212" });
test.skip(!process.env.HWP_E2E_LOCAL_URL || !process.env.HWP_E2E_MOCK_URL,
  "Requires isolated llm API and explicitly synthetic HTTP provider; never an actual model test");

const hash = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");
async function setup(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "+ 새 문서", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
  const frame = page.frames().find(f => f.url().includes("/rhwp/"))!;
  await frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("보존 문단: 담당 부서는 운영팀입니다.");
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("김민수는 10월 9일까지 30만원을 쓰지 않는다.");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const originalUrl = (await page.getByRole("link", { name: "저장본 다운로드" }).getAttribute("href"))!;
  const docPath = originalUrl.split("/versions/")[0];
  const original = await (await page.request.get(originalUrl)).body();
  await page.getByRole("button", { name: /대화 · (Codex|AI) 열기/ }).click();
  page.once("dialog", d => d.accept("MOCK 로컬 후보 시험"));
  await page.getByRole("button", { name: "공유 대화 만들기", exact: true }).click();
  await expect(page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" })).toBeEnabled();
  await expect(page.getByTestId("rewrite-provider")).toContainText("hwp-synthetic-fixture");
  await frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+Home");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("End");
  await page.keyboard.insertText(" 회의 장소는 제1회의실입니다.");
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(" 지급 승인은 하지 않았다.");
  const snapshot = await frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot());
  expect(snapshot.evidence.text).toContain("지급 승인은 하지 않았다.");
  return { frame, originalUrl, docPath, original, snapshot };
}

async function requestCandidates(page: Page) {
  const pending = page.waitForResponse(r => r.request().method() === "POST" && /\/rewrite-tasks$/.test(r.url()));
  await page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" }).click();
  const response = await pending;
  expect(response.ok(), await response.text()).toBeTruthy();
  const id = (await response.json()).id;
  const path = new URL(response.url()).pathname;
  await expect.poll(async () => (await (await page.request.get(path)).json()).find((t: any) => t.id === id).status).toBe("ready");
  return { id, path, request: response.request(), task: (await (await page.request.get(path)).json()).find((t: any) => t.id === id) };
}

async function saveWritingContext(page: Page, purpose = "운영팀에 지출 현황을 보고") {
  await page.getByLabel("작성 목적", { exact: true }).fill(purpose);
  await page.getByLabel("작성 기준", { exact: true }).fill("정중하고 짧게, 날짜와 부정 표현 유지");
  await page.getByRole("button", { name: "확인 사실 추가", exact: true }).click();
  await page.getByLabel("확인 사실 1", { exact: true }).fill("지출 승인은 하지 않았다.");
  await page.getByLabel("사실 1 출처", { exact: true }).selectOption("user_confirmed_ai_excerpt");
  await page.getByLabel("사실 1 출처 설명", { exact: true }).fill("AI 초안에서 확인한 문장");
  await page.getByLabel("목적·사실·기준을 확인했으며 후보 생성에 사용할 수 있습니다", { exact: true }).check();
  await page.getByRole("button", { name: "컨텍스트 저장", exact: true }).click();
  await expect(page.getByTestId("context-saved")).toContainText("확인한 작성 정보를 저장");
}

test("REAL rhwp + MOCK provider: confirmed writing context reaches the model and survives reopening with immutable input", async ({ page, request }, info) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const state = await setup(page);
  await page.getByLabel("저장한 작성 정보 사용", { exact: true }).check();
  await expect(page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" })).toBeDisabled();
  await saveWritingContext(page);
  const job = await requestCandidates(page);
  expect(job.request.postDataJSON().context_revision).toBe(1);
  expect(job.task.execution.context_used).toBe(true);
  const input = (await (await request.get(`/api/rewrite-tasks/${job.id}/input`)).json()).generation_input;
  const calls = (await (await request.get(process.env.HWP_E2E_MOCK_URL + "/proof")).json()).calls;
  expect(calls.at(-1).input).toEqual(input);
  expect(calls.at(-1).input_sha256).toBe(job.task.execution.input_sha256);
  expect(input.writing_context.confirmed_facts[0].source).toBe("user_confirmed_ai_excerpt");
  expect(input.writing_context.confirmation.by).toBe("local");
  const article = page.locator(`[data-task-id="${job.id}"]`);
  await article.getByText("이 후보에 전달한 작성 정보", { exact: true }).click();
  await expect(article.getByTestId("frozen-writing-context")).toContainText("운영팀에 지출 현황을 보고");
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
  expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot())).toEqual(state.snapshot);
  await page.getByRole("button", { name: "대화 · AI 닫기", exact: true }).click();
  await page.getByRole("button", { name: "대화 · AI 열기", exact: true }).click();
  const tid = new URL(job.request.url()).pathname.split("/")[3];
  await page.getByLabel("대화 선택").selectOption(tid);
  await expect(page.getByLabel("작성 목적", { exact: true })).toHaveValue("운영팀에 지출 현황을 보고");
  await expect(page.getByLabel("확인 사실 1", { exact: true })).toHaveValue("지출 승인은 하지 않았다.");
  await article.getByText("이 후보에 전달한 작성 정보", { exact: true }).click();
  await expect(article.getByTestId("frozen-writing-context")).toContainText("AI 문장을 사용자가 확인");
  await page.getByLabel("작성 목적", { exact: true }).fill("임원에게 보고하는 새 목적");
  await page.getByLabel("목적·사실·기준을 확인했으며 후보 생성에 사용할 수 있습니다", { exact: true }).check();
  await page.getByRole("button", { name: "컨텍스트 저장", exact: true }).click();
  await expect(page.getByTestId("context-confirmation")).toContainText("리비전 2");
  await expect(article.getByRole("button", { name: "후보 1 승인·적용·저장" })).toBeDisabled();
  await expect(article.getByTestId("frozen-writing-context")).toContainText("운영팀에 지출 현황을 보고");
  await expect(article.getByTestId("frozen-writing-context")).not.toContainText("임원에게 보고하는 새 목적");
  await page.getByLabel("저장한 작성 정보 사용", { exact: true }).check();
  const next = await requestCandidates(page);
  expect(next.task.execution.context_revision).toBe(2);
  await page.locator(`[data-task-id="${next.id}"]`).getByRole("button", { name: "후보 2 승인·적용·저장" }).click();
  await expect(page.locator(`[data-task-id="${next.id}"]`).getByTestId("application-history")).toContainText("saved");
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(2);
  expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
  await expect(page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" })).toBeDisabled(); // New saved version needs explicit re-confirmation.
  await page.screenshot({ path: info.outputPath("writing-context.png"), fullPage: true });
  await writeFile(info.outputPath("context-proof.json"), JSON.stringify({ actual_model_calls: 0, human_approvals: 0,
    engine: "real rhwp", first_input_sha256: job.task.execution.input_sha256, revisions: [1, 2],
    context_roundtrip: true, stale_approval_blocked: true, version_count: 2, original_preserved: true }, null, 2));
});

test("REAL rhwp + MOCK provider: context draft survives message refresh and concurrent context conflict", async ({ page, request }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const state = await setup(page);
  await saveWritingContext(page);
  const tid = await page.getByLabel("대화 선택").inputValue();
  await page.getByLabel("작성 목적", { exact: true }).fill("저장 전 내 작성 목적");
  await page.getByLabel("대화 메시지").fill("작성 정보 편집 중에도 대화를 기록");
  await page.getByRole("button", { name: "메시지 저장", exact: true }).click();
  await expect(page.getByLabel("저장된 대화")).toContainText("작성 정보 편집 중에도 대화를 기록");
  await expect(page.getByLabel("작성 목적", { exact: true })).toHaveValue("저장 전 내 작성 목적");
  const current = (await (await request.get(`/api/threads/${tid}`)).json()).context;
  const changed = await request.put(`/api/threads/${tid}/context`, { data: {
    expected_revision: current.revision, base_version: current.base_version, purpose: "다른 창에서 저장한 목적",
    facts: current.facts, confirmed_facts: current.confirmed_facts, writing_criteria: current.writing_criteria, confirm_for_rewrite: true,
  }});
  expect(changed.ok()).toBeTruthy();
  await page.getByRole("button", { name: "컨텍스트 저장", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "다른 사용자가 컨텍스트" })).toBeVisible();
  await page.getByRole("button", { name: "대화 새로고침", exact: true }).click();
  await expect(page.getByLabel("작성 목적", { exact: true })).toHaveValue("저장 전 내 작성 목적");
  await expect(page.getByRole("button", { name: "컨텍스트 저장", exact: true })).toBeDisabled();
  page.once("dialog", d => d.dismiss());
  await page.getByRole("button", { name: "대화 · AI 닫기", exact: true }).click();
  await expect(page.getByLabel("작성 목적", { exact: true })).toHaveValue("저장 전 내 작성 목적");
  page.once("dialog", d => d.accept());
  await page.getByRole("button", { name: "작성 정보 편집 취소", exact: true }).click();
  await expect(page.getByLabel("작성 목적", { exact: true })).toHaveValue("다른 창에서 저장한 목적");
  expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
});

test("REAL rhwp + MOCK provider: context save response loss preserves later draft and reuses the same write ID", async ({ page, request }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  await setup(page);
  await saveWritingContext(page);
  const tid = await page.getByLabel("대화 선택").inputValue();
  const sent: any[] = [];
  await page.route(`**/api/threads/${tid}/context`, async route => {
    sent.push(route.request().postDataJSON());
    if (sent.length === 1) { expect((await route.fetch()).ok()).toBeTruthy(); return route.abort("failed"); }
    return route.continue();
  });
  try {
    await page.getByLabel("작성 목적", { exact: true }).fill("응답이 유실될 저장 목적");
    await page.getByLabel("목적·사실·기준을 확인했으며 후보 생성에 사용할 수 있습니다", { exact: true }).check();
    await page.getByRole("button", { name: "컨텍스트 저장", exact: true }).click();
    await expect(page.getByRole("button", { name: "작성 정보 저장 결과 재확인", exact: true })).toBeEnabled();
    await page.getByLabel("작성 목적", { exact: true }).fill("그 뒤에 입력한 목적");
    await page.getByRole("button", { name: "작성 정보 저장 결과 재확인", exact: true }).click();
    await expect(page.getByTestId("context-saved")).toContainText("그 뒤에 입력한 변경은 아직 저장하지 않았습니다");
    await expect(page.getByLabel("작성 목적", { exact: true })).toHaveValue("그 뒤에 입력한 목적");
    expect(sent).toHaveLength(2); expect(sent[1]).toEqual(sent[0]);
    const stored = (await (await request.get(`/api/threads/${tid}`)).json()).context;
    expect(stored.revision).toBe(2); expect(stored.purpose).toBe("응답이 유실될 저장 목적");
    await page.getByLabel("목적·사실·기준을 확인했으며 후보 생성에 사용할 수 있습니다", { exact: true }).check();
    await page.getByRole("button", { name: "컨텍스트 저장", exact: true }).click();
    await expect(page.getByTestId("context-confirmation")).toContainText("리비전 3");
    expect(sent[2].request_id).not.toBe(sent[0].request_id);
    expect((await (await request.get(`/api/threads/${tid}`)).json()).context.purpose).toBe("그 뒤에 입력한 목적");
  } finally { await page.unrouteAll({ behavior: "wait" }); }
});

test("REAL rhwp + MOCK provider: highlighted candidate and copy keep exact text without applying", async ({ page, request, context }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const state = await setup(page);
  const job = await requestCandidates(page);
  const article = page.locator(`[data-task-id="${job.id}"]`);
  await expect(article.getByTestId("candidate-1")).toHaveText(job.task.candidates[0].replacement);
  await expect(article.getByTestId("candidate-1").locator("mark")).toContainText("확인했습니다.");
  await article.getByText("원문에서 바뀐 부분 확인", { exact: true }).first().click();
  await expect(article.getByTestId("candidate-original-1")).toHaveText(state.snapshot.evidence.text);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await article.getByRole("button", { name: "후보 1 복사", exact: true }).click();
  await expect(article.getByRole("button", { name: "복사 완료", exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(job.task.candidates[0].replacement);
  await page.getByLabel("후보 변경 부분 강조", { exact: true }).uncheck();
  await expect(article.getByTestId("candidate-1")).toHaveText(job.task.candidates[0].replacement);
  await expect(article.getByTestId("candidate-1").locator("mark")).toHaveCount(0);
  await page.getByLabel("문체 개선 요청").fill("다른 지시");
  await article.getByText("요청한 개선 지시", { exact: true }).click();
  await article.getByRole("button", { name: "개선 요청 불러오기", exact: true }).click();
  await expect(page.getByLabel("문체 개선 요청")).toHaveValue(job.task.instruction);
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
  expect(await (await request.get(state.docPath + "/applications")).json()).toEqual([]);
  expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot())).toEqual(state.snapshot);
});

test("REAL rhwp + MOCK provider: explicit pending request recovery keeps original context after subsequent draft edits", async ({ page, request }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const state = await setup(page);
  await saveWritingContext(page);
  await page.getByLabel("저장한 작성 정보 사용", { exact: true }).check();
  const tid = await page.getByLabel("대화 선택").inputValue();
  const sends: any[] = [];
  await page.route(`**/api/threads/${tid}/rewrite-tasks`, async route => {
    if (route.request().method() !== "POST") return route.continue();
    sends.push(route.request().postDataJSON());
    if (sends.length === 1) { expect((await route.fetch()).ok()).toBeTruthy(); return route.abort("failed"); }
    return route.continue();
  });
  try {
    await page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" }).click();
    await expect(page.getByRole("button", { name: "보관한 생성 요청 재확인", exact: true })).toBeEnabled();
    await page.getByLabel("작성 목적", { exact: true }).fill("나중에 작성한 미저장 목적");
    await page.getByLabel("문체 개선 요청").fill("다른 개선 지시를 나중에 작성");
    await expect(page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" })).toBeDisabled();
    await page.getByRole("button", { name: "보관한 생성 요청 재확인", exact: true }).click();
    await expect(page.getByTestId("pending-rewrite")).toHaveCount(0);
    expect(sends).toHaveLength(2); expect(sends[1]).toEqual(sends[0]);
    expect(sends[1].context_revision).toBe(1);
    await expect(page.getByLabel("작성 목적", { exact: true })).toHaveValue("나중에 작성한 미저장 목적");
    await expect(page.getByLabel("문체 개선 요청")).toHaveValue("다른 개선 지시를 나중에 작성");
    const tasks = await (await request.get(`/api/threads/${tid}/rewrite-tasks`)).json();
    expect(tasks).toHaveLength(1);
    // Acceptance/reconfirmation can finish before the background provider starts.
    // Wait for its terminal result before asserting the total number of model calls.
    await expect.poll(async () => {
      const current = await (await request.get(`/api/threads/${tid}/rewrite-tasks`)).json();
      return current.find((task: any) => task.id === tasks[0].id)?.status;
    }).toBe("ready");
    const calls = (await (await request.get(process.env.HWP_E2E_MOCK_URL + "/proof")).json()).calls;
    expect(calls.filter((call: any) => call.input_sha256 === tasks[0].execution.input_sha256)).toHaveLength(1);
    expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
  } finally { await page.unrouteAll({ behavior: "wait" }); }
});

test("REAL rhwp + MOCK HTTP local provider: frozen input, no generation writes, automated approval, save and reopen", async ({ page, request }, info) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const state = await setup(page);
  const job = await requestCandidates(page);
  const article = page.locator(`[data-task-id="${job.id}"]`);
  await expect(article.getByTestId("as-is")).toHaveText(state.snapshot.evidence.text);
  expect(job.task.snapshot).toEqual(state.snapshot);
  expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot())).toEqual(state.snapshot);
  expect((await (await request.get(state.docPath + "/versions")).json())).toHaveLength(1);
  expect((await (await request.get(state.docPath + "/applications")).json())).toEqual([]);
  const mock = await (await request.get(process.env.HWP_E2E_MOCK_URL + "/proof")).json();
  const call = mock.calls.findLast((c: any) => c.input_sha256 === job.task.execution.input_sha256);
  expect(mock.actual_model_calls).toBe(0);
  expect(call.input.selected_paragraph).toBe(state.snapshot.evidence.text);
  expect(job.task.execution.provider).toBe("llm");
  await expect(article.getByTestId("candidate-2")).toHaveText(job.task.candidates[1].replacement);
  await article.getByText("실행 근거", { exact: true }).click();
  await page.setViewportSize({ width: 1700, height: 1800 });
  await article.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("comparison.png"), fullPage: true });
  const beforeBytes = Buffer.from(await state.frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes)) as number[]);
  await article.getByRole("button", { name: "후보 2 승인·적용·저장" }).click(); // Automated fixture approval, NOT human consent.
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  await expect(article.getByTestId("application-history")).toContainText("saved");
  const history = (await (await request.get(state.docPath + "/applications")).json())[0];
  const versions = await (await request.get(state.docPath + "/versions")).json();
  expect(versions).toHaveLength(2);
  const saved = Buffer.from(await (await request.get(state.docPath + `/versions/${history.saved_version}/file`)).body());
  expect(hash(saved)).toBe(versions.find((v: any) => v.id === history.saved_version).sha256);
  expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
  const before = unzipSync(beforeBytes), after = unzipSync(saved);
  const texts = strFromU8(after["Contents/section0.xml"]);
  expect(texts).toContain("회의 장소는 제1회의실입니다.");
  expect(texts).toContain(job.task.candidates[1].replacement);
  for (const [name, bytes] of Object.entries(before)) {
    if (name.startsWith("BinData/")) expect(Buffer.from(after[name])).toEqual(Buffer.from(bytes));
  }
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  const savedUrl = state.docPath + `/versions/${history.saved_version}/file`;
  await page.getByRole("row").filter({ has: page.locator(`a[href="${savedUrl}"]`) }).locator("td").first().getByRole("button").click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const reopened = page.frames().find(f => f.url().includes("/rhwp/"))!;
  const reopenedBytes = Buffer.from(await reopened.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes)) as number[]);
  expect(strFromU8(unzipSync(reopenedBytes)["Contents/section0.xml"])).toContain(job.task.candidates[1].replacement);
  await page.screenshot({ path: info.outputPath("reopened.png"), fullPage: true });
  await writeFile(info.outputPath("proof.json"), JSON.stringify({ mock: true, actual_model_calls: 0, approval: "automated test click",
    task_id: job.id, candidates: job.task.candidates.map((c: any) => c.id), application: history,
    original_sha256: hash(state.original), saved_sha256: hash(saved), execution: job.task.execution,
    snapshot: state.snapshot, non_target_preserved: true, version_count: versions.length }, null, 2));
});

for (const denied of [false, true]) {
test(`REAL rhwp + MOCK provider: lost approved-save response preserves later typing and retries the same output (permission response ${denied})`, async ({ page, request }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const state = await setup(page);
  const job = await requestCandidates(page);
  const article = page.locator(`[data-task-id="${job.id}"]`);
  let lost = false;
  const saves: { key: string; data: string | null }[] = [];
  await page.route("**" + state.docPath + "/versions?**", async route => {
    if (route.request().method() !== "POST") return route.continue();
    saves.push({ key: route.request().headers()["idempotency-key"], data: route.request().postData() });
    if (!lost) {
      lost = true;
      const result = await route.fetch();
      expect(result.ok()).toBeTruthy();
      return route.abort("failed");
    }
    if (denied && saves.length === 2) return route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ detail: "합성 권한 거부 응답" }) });
    return route.continue();
  });
  await article.getByRole("button", { name: "후보 1 승인·적용·저장" }).click();
  await expect(page.getByRole("status")).toHaveText("저장 확인 실패");
  await expect(article.getByTestId("application-history")).toContainText("saved");
  await state.frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  const appended = " 저장 응답 유실 뒤 후속 편집.";
  await page.keyboard.insertText(appended);
  await article.getByRole("button", { name: "저장 완료 확인" }).click();
  if (denied) {
    await expect(page.getByRole("status")).toHaveText("저장 확인 실패");
    await expect(page.getByRole("alert").filter({ hasText: "서버 반영 여부" })).toContainText("권한 거부");
    expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(2);
    await expect(article.getByTestId("application-history")).toContainText("saved");
    await article.getByRole("button", { name: "저장 완료 확인" }).click();
  }
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
  expect(saves).toHaveLength(denied ? 3 : 2);
  for (const saved of saves) expect(saved).toEqual(saves[0]);
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(2);
  const application = (await (await request.get(state.docPath + "/applications")).json())[0];
  const approved = await (await request.get(`${state.docPath}/versions/${application.saved_version}/file`)).body();
  expect(strFromU8(unzipSync(approved)["Contents/section0.xml"])).not.toContain(appended.trim());
  expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toBe(job.task.candidates[0].replacement + appended);
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(3);
  const latest = (await page.getByRole("link", { name: "저장본 다운로드" }).getAttribute("href"))!;
  expect(strFromU8(unzipSync(await (await request.get(latest)).body())["Contents/section0.xml"])).toContain(appended.trim());
  expect((await (await request.get(state.docPath + "/applications")).json())[0].saved_version).toBe(application.saved_version);
  expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
});
}

test("REAL rhwp + MOCK provider: lost approval response preserves the buffer and reuses the application", async ({ page, request }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const state = await setup(page);
  const job = await requestCandidates(page);
  const article = page.locator(`[data-task-id="${job.id}"]`);
  const approvals: any[] = [];
  await page.route(`**/api/rewrite-tasks/${job.id}/approve`, async route => {
    approvals.push(route.request().postDataJSON());
    if (approvals.length === 1) {
      expect((await route.fetch()).ok()).toBeTruthy();
      return route.abort("failed");
    }
    return route.continue();
  });
  await article.getByRole("button", { name: "후보 1 승인·적용·저장" }).click();
  await expect(article.getByTestId("application-history")).toContainText("approved");
  expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot())).toEqual(state.snapshot);
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
  await article.getByRole("button", { name: "후보 1 승인·적용·저장" }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  expect(approvals).toHaveLength(2); expect(approvals[1]).toEqual(approvals[0]);
  const applications = await (await request.get(state.docPath + "/applications")).json();
  expect(applications).toHaveLength(1); expect(applications[0].id).toBe(approvals[0].id);
  expect(applications[0].status).toBe("saved");
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(2);
  expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
});

test("REAL rhwp + MOCK provider: lost receipt response retries frozen output and preserves later typing", async ({ page, request }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const state = await setup(page);
  const job = await requestCandidates(page);
  // Hold a second logical task; cancelling it must not clear the first approval's frozen output.
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: { hold: true } });
  try {
  const other = { ...job.request.postDataJSON(), id: crypto.randomUUID() };
  expect((await request.post(job.path, { data: other, headers: { "X-Edit-Session": job.request.headers()["x-edit-session"] } })).ok()).toBeTruthy();
  await expect.poll(async () => (await (await request.get(process.env.HWP_E2E_MOCK_URL + "/proof")).json()).calls.at(-1)?.held).toBe(true);
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const article = page.locator(`[data-task-id="${job.id}"]`);
  const receipts: any[] = [];
  await page.route("**/api/applications/*/receipt", async route => {
    receipts.push(route.request().postDataJSON());
    if (receipts.length === 1) {
      expect((await route.fetch()).ok()).toBeTruthy();
      return route.abort("failed");
    }
    return route.continue();
  });
  await article.getByRole("button", { name: "후보 1 승인·적용·저장" }).click();
  await expect(article.getByTestId("application-history")).toContainText("applied");
  await page.getByRole("button", { name: "후보·적용 이력 새로고침" }).click();
  await page.locator(`[data-task-id="${other.id}"]`).getByRole("button", { name: "이 작업 결과 수신 중단" }).click();
  await expect(page.locator(`[data-task-id="${other.id}"]`)).toContainText("cancelled");
  await request.post(process.env.HWP_E2E_MOCK_URL + "/release");
  const replacement = job.task.candidates[0].replacement;
  expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toBe(replacement);
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
  await state.frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  const appended = " 영수증 응답 유실 후 추가 입력.";
  await page.keyboard.insertText(appended);
  const instruction = "새 지시도 승인 저장 연결이 끝날 때까지 보존한다.";
  await page.getByRole("textbox", { name: "문체 개선 요청" }).fill(instruction);
  await page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" }).click();
  await expect(page.locator(".rewrite > [role=alert]")).toContainText("승인 적용의 저장 연결");
  await expect(page.getByRole("textbox", { name: "문체 개선 요청" })).toHaveValue(instruction);
  expect(await (await request.get(job.path)).json()).toHaveLength(2);
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.locator(".workspace > [role=alert]").filter({ hasText: "승인 적용의 저장 연결" })).toBeVisible();
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
  await article.getByRole("button", { name: "후보 1 승인·적용·저장" }).click();
  await expect(article.getByTestId("application-history")).toContainText("saved");
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
  expect(receipts).toHaveLength(2); expect(receipts[1]).toEqual(receipts[0]);
  expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toBe(replacement + appended);
  const application = (await (await request.get(state.docPath + "/applications")).json())[0];
  const approvedUrl = `${state.docPath}/versions/${application.saved_version}/file`;
  const approvedBytes = await (await request.get(approvedUrl)).body();
  const approvedXml = strFromU8(unzipSync(approvedBytes)["Contents/section0.xml"]);
  expect(approvedXml).toContain(replacement); expect(approvedXml).not.toContain(appended.trim());
  expect(hash(approvedBytes)).toBe(receipts[0].export_sha256);
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(2);
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const latestUrl = (await page.getByRole("link", { name: "저장본 다운로드" }).getAttribute("href"))!;
  expect(strFromU8(unzipSync(await (await request.get(latestUrl)).body())["Contents/section0.xml"])).toContain(replacement + appended);
  expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(3);
  expect((await (await request.get(state.docPath + "/applications")).json())[0].saved_version).toBe(application.saved_version);
  expect(hash(await (await request.get(approvedUrl)).body())).toBe(hash(approvedBytes));
  expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
  } finally {
    await request.post(process.env.HWP_E2E_MOCK_URL + "/release");
    await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  }
});

test("REAL rhwp + MOCK provider: page-count rejection after approval preserves bytes and permits recovery", async ({ page, request }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: { failure: "page-growth" } });
  try {
    const state = await setup(page);
    const before = Buffer.from(await state.frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes)) as number[]);
    const job = await requestCandidates(page);
    let receipts = 0;
    page.on("request", r => { if (r.method() === "POST" && /\/applications\/[^/]+\/receipt$/.test(r.url())) receipts++; });
    const article = page.locator(`[data-task-id="${job.id}"]`);
    await article.getByRole("button", { name: "후보 1 승인·적용·저장" }).click();
    await expect(page.locator(".rewrite [role=alert]")).toContainText("문서 페이지 수가 변경되었습니다");
    await expect(page.getByRole("status")).not.toHaveText("저장 완료");
    const application = (await (await request.get(state.docPath + "/applications")).json())[0];
    expect(application.status).toBe("approved"); expect(application.saved_version).toBeNull();
    expect(application.candidate_id).toBe(job.task.candidates[0].id); expect(receipts).toBe(0);
    expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
    const after = await state.frame.evaluate(() => ({ snapshot: (window as any).hwpAssistantBridge.snapshot(), bytes: Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) }));
    expect(Buffer.from(after.bytes as number[])).toEqual(before);
    expect(after.snapshot.evidence.text).toBe(state.snapshot.evidence.text);
    expect(after.snapshot.state.documentSha256).toBe(state.snapshot.state.documentSha256);
    expect(after.snapshot.state.pageCount).toBe(state.snapshot.state.pageCount);
    expect(strFromU8(unzipSync(before)["Contents/section0.xml"])).toContain("회의 장소는 제1회의실입니다.");
    await page.getByRole("button", { name: "비교 종료 · 자동 저장 재개" }).click();
    await page.getByRole("button", { name: "저장", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(2);
    await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
    const next = await requestCandidates(page);
    expect(next.id).not.toBe(job.id);
    expect(next.task.base_version).not.toBe(job.task.base_version);
    expect(next.task.snapshot.evidence.text).toBe(state.snapshot.evidence.text);
    await page.locator(`[data-task-id="${next.id}"]`).getByRole("button", { name: "후보 2 승인·적용·저장" }).click();
    await expect(page.locator(`[data-task-id="${next.id}"]`).getByTestId("application-history")).toContainText("saved");
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(3);
    const history = await (await request.get(state.docPath + "/applications")).json();
    expect(history.find((a: any) => a.id === application.id)).toEqual(application);
    const latest = (await page.getByRole("link", { name: "저장본 다운로드" }).getAttribute("href"))!;
    expect(strFromU8(unzipSync(await (await request.get(latest)).body())["Contents/section0.xml"])).toContain("회의 장소는 제1회의실입니다.");
    expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
  } finally { await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} }); }
});

test("REAL rhwp + MOCK provider: late document A generation response cannot alter document B", async ({ page, request }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const state = await setup(page);
  let release!: () => void, entered!: () => void;
  const barrier = new Promise<void>(r => { release = r; });
  const accepted = new Promise<void>(r => { entered = r; });
  let oldTask = "";
  await page.route("**/api/threads/*/rewrite-tasks", async route => {
    if (route.request().method() !== "POST") return route.continue();
    const result = await route.fetch();
    oldTask = (await result.json()).id;
    entered();
    await barrier;
    await route.fulfill({ response: result });
  });
  try {
    await page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" }).click();
    await Promise.race([accepted, new Promise((_, reject) => setTimeout(() => reject(Error("response barrier timed out")), 5000))]);
    page.once("dialog", d => d.accept());
    await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
    await page.getByRole("button", { name: "+ 새 문서", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
    const b = page.frames().find(f => f.url().includes("/rhwp/"))!;
    await b.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
    await page.keyboard.press("Control+End"); await page.keyboard.press("Enter");
    await page.keyboard.insertText("문서 B의 독립 미저장 편집");
    const before = await b.evaluate(() => (window as any).hwpAssistantBridge.snapshot());
    const delivered = page.waitForResponse(r => r.request().method() === "POST" && /\/rewrite-tasks$/.test(r.url()));
    release(); await delivered;
    // A's response handler then requests history: waiting for it makes the ordering observable.
    await page.waitForLoadState("networkidle");
    expect(await b.evaluate(() => (window as any).hwpAssistantBridge.snapshot())).toEqual(before);
    await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
    await expect(page.locator(`[data-task-id="${oldTask}"]`)).toHaveCount(0);
    await page.getByRole("button", { name: "저장", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    await page.getByRole("button", { name: /대화 · (Codex|AI) 열기/ }).click();
    page.once("dialog", d => d.accept("문서 B"));
    await page.getByRole("button", { name: "공유 대화 만들기", exact: true }).click();
    await expect(page.locator("[data-task-id]")).toHaveCount(0);
    expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
  } finally { release(); }
});

for (const lateResponse of ["success", "4xx"] as const) {
test(`REAL rhwp + MOCK provider: same-document late ${lateResponse} response preserves newer request retry`, async ({ page, request }, info) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: { hold: true } });
  let releaseA!: () => void, releaseB!: () => void;
  const barrierA = new Promise<void>(resolve => { releaseA = resolve; });
  const barrierB = new Promise<void>(resolve => { releaseB = resolve; });
  const posted: any[] = [];
  const stages: string[] = [];
  const routeWork: Promise<void>[] = [];
  const suffix = crypto.randomUUID();
  const instructionA = `표현 개선 A ${suffix}`;
  const instructionB = `표현 개선 B ${suffix}`;
  let path = "", acceptedA = false, acceptedB = false;
  await page.route("**/api/threads/*/rewrite-tasks", route => {
    const work = (async () => {
      if (route.request().method() !== "POST") return route.continue();
      posted.push(route.request().postDataJSON());
      const index = posted.length;
      path = new URL(route.request().url()).pathname;
      if (index > 2) return route.continue();
      const result = await route.fetch();
      expect(result.ok()).toBeTruthy();
      if (index === 1) {
        acceptedA = true; stages.push("A accepted; HTTP response held");
        await barrierA;
        if (lateResponse === "success") await route.fulfill({ response: result });
        else await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "합성 늦은 A 충돌" }) });
      } else {
        acceptedB = true; stages.push("B accepted; HTTP response held");
        await barrierB;
        await route.abort("failed");
      }
    })();
    routeWork.push(work);
    return work;
  });
  try {
    const state = await setup(page);
    const thread = await page.getByRole("combobox", { name: "대화 선택" }).inputValue();
    const generate = page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" });
    await page.getByRole("textbox", { name: "문체 개선 요청" }).fill(instructionA);
    await generate.click();
    await expect.poll(() => acceptedA).toBe(true);
    const modelCalls = async () => (await (await request.get(process.env.HWP_E2E_MOCK_URL + "/proof")).json()).calls
      .filter((call: any) => [instructionA, instructionB].includes(call.input.instruction));
    await expect.poll(async () => (await modelCalls())[0]?.held).toBe(true);
    expect((await (await request.get(path)).json())[0].status).toBe("running");
    stages.push("A model gate entered; still running");
    // Same Editor instance and document; only the conversation panel is remounted.
    await page.getByRole("button", { name: "대화 · AI 닫기" }).click();
    await page.getByRole("button", { name: "대화 · AI 열기" }).click();
    await expect(page.locator(`select[aria-label="대화 선택"] option[value="${thread}"]`)).toHaveCount(1);
    await page.getByRole("combobox", { name: "대화 선택" }).selectOption(thread);
    const articleA = page.locator(`[data-task-id="${posted[0].id}"]`);
    await articleA.getByRole("button", { name: "이 작업 결과 수신 중단" }).click();
    await expect(articleA).toContainText("cancelled");
    stages.push("A logically cancelled after panel remount");
    await request.post(process.env.HWP_E2E_MOCK_URL + "/release");
    await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
    await state.frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
    await page.keyboard.press("Control+End");
    await page.keyboard.insertText(" B 요청 전 미저장 입력.");
    const beforeB = await state.frame.evaluate(() => ({ snapshot: (window as any).hwpAssistantBridge.snapshot(),
      bytes: Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[] }));
    await page.getByRole("textbox", { name: "문체 개선 요청" }).fill(instructionB);
    await generate.click();
    await expect.poll(() => acceptedB).toBe(true);
    const frozenB = structuredClone(posted[1]);
    expect(frozenB.id).not.toBe(posted[0].id);
    expect(frozenB.instruction).toBe(instructionB);
    expect(frozenB.selection).toEqual(beforeB.snapshot);
    // Allow normal model publication so busy/running UI checks need no DB manipulation.
    await expect.poll(async () => (await (await request.get(path)).json()).find((t: any) => t.id === frozenB.id)?.status).toBe("ready");
    const deliveredA = page.waitForResponse(r => r.request().method() === "POST" && r.request().postDataJSON()?.id === posted[0].id);
    releaseA();
    await (await deliveredA).finished();
    // Let the already-delivered fetch continuation and React update run before inspecting the hold.
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    stages.push(`late A ${lateResponse} delivered while B response still held`);
    await expect(page.getByRole("button", { name: "비교 종료 · 자동 저장 재개" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "문체 개선 요청" })).toHaveValue(instructionB);
    expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot())).toEqual(beforeB.snapshot);
    releaseB();
    await expect(page.locator(".rewrite > [role=alert]")).toContainText("요청 원문과 ID를 보관");
    stages.push("B response lost after server acceptance");
    await expect(generate).toBeEnabled();
    await generate.click();
    await expect.poll(() => posted.length).toBe(3);
    expect(posted[2]).toEqual(frozenB);
    await expect(generate).toBeEnabled();
    const tasks = await (await request.get(path)).json();
    expect(tasks).toHaveLength(2);
    expect(tasks.find((t: any) => t.id === posted[0].id).status).toBe("cancelled");
    const storedB = tasks.find((t: any) => t.id === frozenB.id);
    expect(storedB.snapshot).toEqual(frozenB.selection);
    expect(storedB.base_version).toBe(frozenB.base_version);
    expect(storedB.instruction).toBe(instructionB);
    expect(storedB.execution.generation_calls).toBe(1);
    expect(storedB.execution.context_used).toBe(false);
    expect(await modelCalls()).toHaveLength(2);
    expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
    expect(await (await request.get(state.docPath + "/applications")).json()).toEqual([]);
    const after = Buffer.from(await state.frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes)) as number[]);
    expect(after).toEqual(Buffer.from(beforeB.bytes));
    const xml = strFromU8(unzipSync(after)["Contents/section0.xml"]);
    expect(xml).toContain("회의 장소는 제1회의실입니다.");
    expect(xml).toContain("B 요청 전 미저장 입력.");
    expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
    await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
    await expect(page.getByRole("button", { name: "비교 종료 · 자동 저장 재개" })).toBeVisible();
    stages.push("same B input/ID retried; 2 tasks, 2 MOCK calls, 1 version; bytes unchanged");
    await writeFile(info.outputPath("late-response-proof.json"), JSON.stringify({ kind: "real rhwp, MOCK HTTP provider, same document", lateResponse,
      actual_model_calls: 0, human_approvals: 0, stages, request_ids: posted.map(p => p.id),
      identical_B_retry: JSON.stringify(posted[1]) === JSON.stringify(posted[2]), task_count: tasks.length,
      mock_calls: (await modelCalls()).length, original_sha256: hash(state.original), buffer_sha256: hash(after),
      frozen_B_sha256: hash(Buffer.from(JSON.stringify(frozenB))), context_used: false }, null, 2));
  } finally {
    releaseA(); releaseB();
    await request.post(process.env.HWP_E2E_MOCK_URL + "/release");
    await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
    await page.unrouteAll({ behavior: "wait" });
    await Promise.allSettled(routeWork);
  }
});
}

test("REAL rhwp + MOCK provider: lost generation response reuses the original task and snapshot", async ({ page, request }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const state = await setup(page);
  let lost = false;
  const submitted: any[] = [];
  let taskPath = "";
  await page.route("**/api/threads/*/rewrite-tasks", async route => {
    if (route.request().method() !== "POST") return route.continue();
    submitted.push(route.request().postDataJSON());
    taskPath = new URL(route.request().url()).pathname;
    if (!lost) {
      lost = true;
      expect((await route.fetch()).ok()).toBeTruthy();
      return route.abort("failed");
    }
    return route.continue();
  });
  const button = page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" });
  await button.click();
  await expect(page.locator(".rewrite [role=alert]")).toContainText("요청 원문과 ID를 보관");
  await expect(button).toBeEnabled();
  await state.frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(" 응답 유실 후 추가 입력.");
  await button.click();
  await expect.poll(() => submitted.length).toBe(2);
  expect(submitted[0]).toEqual(submitted[1]);
  const tasks = await (await request.get(taskPath)).json();
  expect(tasks).toHaveLength(1);
  expect(tasks[0].snapshot).toEqual(state.snapshot);
  expect(tasks[0].execution.generation_calls).toBe(1);
  const article = page.locator(`[data-task-id="${tasks[0].id}"]`);
  await article.getByRole("button", { name: "후보 1 승인·적용·저장" }).click();
  await expect(page.locator(".rewrite [role=alert]")).toContainText("다시 검토");
  expect((await (await request.get(state.docPath + "/versions")).json())).toHaveLength(1);
  expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toContain("응답 유실 후 추가 입력.");
});

for (const action of ["approve", "cancel"] as const) {
  test(`REAL rhwp + MOCK provider: ${action} resolves a lost request so a new instruction can start`, async ({ page, request }) => {
    await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: action === "cancel" ? { delay: 3 } : {} });
    try {
      const state = await setup(page);
      const submitted: any[] = [];
      let taskPath = "";
      await page.route("**/api/threads/*/rewrite-tasks", async route => {
        if (route.request().method() !== "POST") return route.continue();
        submitted.push(route.request().postDataJSON());
        taskPath = new URL(route.request().url()).pathname;
        if (submitted.length === 1) {
          expect((await route.fetch()).ok()).toBeTruthy();
          return route.abort("failed");
        }
        return route.continue();
      });
      const button = page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" });
      await button.click();
      await expect(page.locator(".rewrite [role=alert]")).toContainText("요청 원문과 ID를 보관");
      const first = submitted[0];
      const article = page.locator(`[data-task-id="${first.id}"]`);
      if (action === "approve") {
        await article.getByRole("button", { name: "후보 1 승인·적용·저장" }).click();
        await expect(page.getByRole("status")).toHaveText("저장 완료");
      } else {
        await article.getByRole("button", { name: "이 작업 결과 수신 중단" }).click();
        await expect(article).toContainText("cancelled");
      }
      await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
      const nextInstruction = "같은 사실을 유지하고 표현을 간결하게 다듬어 주세요.";
      await page.getByRole("textbox", { name: "문체 개선 요청" }).fill(nextInstruction);
      await button.click();
      await expect.poll(() => submitted.length).toBe(2);
      expect(submitted[1].id).not.toBe(first.id);
      expect(submitted[1].instruction).toBe(nextInstruction);
      await expect.poll(async () => (await (await request.get(taskPath)).json())
        .find((task: any) => task.id === submitted[1].id)?.status).toBe("ready");
      expect(await (await request.get(taskPath)).json()).toHaveLength(2);
      expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(action === "approve" ? 2 : 1);
      expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
    } finally {
      await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
    }
  });
}

for (const failure of ["json", "http", "timeout"]) {
  test(`REAL rhwp + MOCK provider: ${failure} failure leaves buffer and versions unchanged`, async ({ page, request }) => {
    await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", {
      data: failure === "timeout" ? { delay: 7 } : { failure },
    });
    try {
      const state = await setup(page);
      const responsePromise = page.waitForResponse(r => r.request().method() === "POST" && /\/rewrite-tasks$/.test(r.url()));
      await page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" }).click();
      const response = await responsePromise;
      const path = new URL(response.url()).pathname;
      await expect.poll(async () => (await (await request.get(path)).json())[0].status, { timeout: 15000 }).toBe("failed");
      const task = (await (await request.get(path)).json())[0];
      expect(task.candidates).toEqual([]);
      expect(task.execution.generation_calls).toBe(1);
      expect(task.application).toBeNull();
      expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot())).toEqual(state.snapshot);
      expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
      expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
    } finally {
      await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
    }
  });
}

test("REAL rhwp + MOCK delayed provider: cancel rejects late result and preserves the unsaved buffer", async ({ page, request }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: { delay: 3 } });
  try {
    const state = await setup(page);
    const responsePromise = page.waitForResponse(r => r.request().method() === "POST" && /\/rewrite-tasks$/.test(r.url()));
    await page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" }).click();
    const response = await responsePromise;
    const path = new URL(response.url()).pathname;
    const id = (await response.json()).id;
    const article = page.locator(`[data-task-id="${id}"]`);
    await article.getByRole("button", { name: "이 작업 결과 수신 중단" }).click();
    await expect(article).toContainText("cancelled");
    await expect.poll(async () => (await (await request.get(process.env.HWP_E2E_MOCK_URL + "/proof")).json()).calls.at(-1).finished,
      { timeout: 10000 }).toBe(true);
    const task = (await (await request.get(path)).json())[0];
    expect(task.status).toBe("cancelled");
    expect(task.candidates).toEqual([]);
    expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot())).toEqual(state.snapshot);
    expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
  } finally {
    await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  }
});

test("REAL rhwp + MOCK delayed provider: a manual save makes the pending generation stale", async ({ page, request }) => {
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: { delay: 3 } });
  try {
    const state = await setup(page);
    const responsePromise = page.waitForResponse(r => r.request().method() === "POST" && /\/rewrite-tasks$/.test(r.url()));
    await page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" }).click();
    const response = await responsePromise;
    const path = new URL(response.url()).pathname;
    await page.getByRole("button", { name: "저장", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    await expect.poll(async () => (await (await request.get(path)).json())[0].status, { timeout: 10000 }).toBe("failed");
    const task = (await (await request.get(path)).json())[0];
    expect(task.stale).toBe(true);
    expect(task.candidates).toEqual([]);
    expect(task.snapshot).toEqual(state.snapshot);
    expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(2);
  } finally {
    await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  }
});

test("REAL rhwp + MOCK provider: long-lived review holds autosave, renews its lease, then resumes saving", async ({ page, request }, info) => {
  const seconds = Number(process.env.HWP_E2E_SOAK_SECONDS || 0);
  const useWritingContext = process.env.HWP_E2E_CONTEXT_SOAK === "1";
  test.skip(!seconds, "Opt in with isolated harness -SoakSeconds; real elapsed time, no accelerated clocks");
  expect(Number.isInteger(seconds) && seconds >= 180 && seconds <= 7200).toBe(true);
  test.setTimeout(seconds * 1000 + 180000);
  await request.post(process.env.HWP_E2E_MOCK_URL + "/controls", { data: {} });
  const renewal: { elapsed: number; status: number }[] = [];
  const errors: string[] = [];
  const checkpoints: unknown[] = [];
  const started = Date.now();
  const proof: any = { kind: "real rhwp; MOCK HTTP model; automated clicks; real clock soak", actual_model_calls: 0,
    requested_seconds: seconds, writing_context: useWritingContext, started_at: new Date(started).toISOString(), outcome: "running", renewal, checkpoints, errors };
  const record = () => writeFile(info.outputPath("soak-proof.json"), JSON.stringify(proof, null, 2));
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", response => {
    if (response.request().method() === "PUT" && /\/api\/documents\/[^/]+\/session$/.test(response.url()))
      renewal.push({ elapsed: (Date.now() - started) / 1000, status: response.status() });
  });
  try {
    const state = await setup(page);
    if (useWritingContext) {
      await saveWritingContext(page);
      await page.getByLabel("저장한 작성 정보 사용", { exact: true }).check();
    }
    const job = await requestCandidates(page);
    if (useWritingContext) {
      expect(job.task.execution.context_used).toBe(true);
      proof.context_input_sha256 = job.task.execution.input_sha256;
    }
    proof.task_id = job.id; proof.document_path = state.docPath; proof.original_sha256 = hash(state.original);
    let expectedText = state.snapshot.evidence.text;
    const holdStarted = Date.now();
    const deadline = holdStarted + seconds * 1000;
    let checkpoint = 0;
    while (Date.now() < deadline) {
      await page.waitForTimeout(Math.min(60000, deadline - Date.now()));
      checkpoint++;
      if (checkpoint % 5 === 0) {
        await state.frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
        await page.keyboard.press("Control+End");
        const appended = ` [장기 검토 ${checkpoint}]`;
        await page.keyboard.insertText(appended); expectedText += appended;
      }
      expect(await state.frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toBe(expectedText);
      expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
      expect(await (await request.get(state.docPath + "/applications")).json()).toEqual([]);
      const task = (await (await request.get(job.path)).json()).find((item: any) => item.id === job.id);
      expect(task.status).toBe("ready"); expect(task.snapshot).toEqual(state.snapshot);
      if (useWritingContext) {
        expect(task.execution.context_revision).toBe(1);
        expect(task.execution.input_sha256).toBe(proof.context_input_sha256);
        expect(task.context_stale).toBe(false);
      }
      expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
      await expect(page.getByRole("button", { name: "비교 종료 · 자동 저장 재개" })).toBeVisible();
      await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
      expect(renewal.every(item => item.status === 200)).toBe(true);
      expect(errors).toEqual([]);
      checkpoints.push({ elapsed: (Date.now() - holdStarted) / 1000, version_count: 1,
        renewal_count: renewal.length, text_sha256: hash(Buffer.from(expectedText)) });
      await record();
      if (checkpoint % 5 === 0) console.log(`Soak checkpoint ${checkpoint}: review held, ${renewal.length} successful lease renewals`);
    }
    proof.held_seconds = (Date.now() - holdStarted) / 1000;
    expect(renewal.length).toBeGreaterThanOrEqual(Math.floor(seconds / 30) - 2);
    if (expectedText === state.snapshot.evidence.text) {
      await state.frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
      await page.keyboard.press("Control+End");
      await page.keyboard.insertText(" [검토 후 변경]");
    }
    const oldArticle = page.locator(`[data-task-id="${job.id}"]`);
    await oldArticle.getByRole("button", { name: "후보 1 승인·적용·저장" }).click();
    await expect(page.locator(".rewrite [role=alert]")).toContainText("다시 검토");
    expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(1);
    await page.getByRole("button", { name: "저장", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(2);
    if (useWritingContext) {
      await expect(page.getByRole("button", { name: "로컬·사내 모델로 후보 2개 요청" })).toBeDisabled();
      await page.getByLabel("목적·사실·기준을 확인했으며 후보 생성에 사용할 수 있습니다", { exact: true }).check();
      await page.getByRole("button", { name: "컨텍스트 저장", exact: true }).click();
      await expect(page.getByTestId("context-confirmation")).toContainText("리비전 2");
    }
    const fresh = await requestCandidates(page);
    await page.locator(`[data-task-id="${fresh.id}"]`).getByRole("button", { name: "후보 2 승인·적용·저장" }).click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    await expect(page.getByRole("button", { name: "비교 종료 · 자동 저장 재개" })).not.toBeVisible();
    expect(await (await request.get(state.docPath + "/versions")).json()).toHaveLength(3);
    await state.frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
    await page.keyboard.press("Control+End");
    await page.keyboard.insertText(" 자동 저장 재개 확인.");
    await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
    await expect(page.getByRole("status")).toHaveText("저장 완료", { timeout: 25000 });
    const versions = await (await request.get(state.docPath + "/versions")).json();
    expect(versions).toHaveLength(4);
    expect(hash(await (await request.get(state.originalUrl)).body())).toBe(hash(state.original));
    expect(errors).toEqual([]);
    proof.outcome = "passed";
    proof.final_version_count = versions.length; proof.fresh_task_id = fresh.id;
    proof.stale_approval_blocked = true; proof.autosave_resumed = true;
    await page.screenshot({ path: info.outputPath("soak-finished.png"), fullPage: true });
  } catch (error) {
    proof.outcome = "failed"; proof.error = String(error); throw error;
  } finally {
    proof.finished_at = new Date().toISOString(); await record();
  }
});
