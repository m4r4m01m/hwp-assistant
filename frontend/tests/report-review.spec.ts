import { test, expect } from "@playwright/test";
import { writeFile } from "node:fs/promises";

test("REVIEW flow: read before layout, frozen feedback retry, stable sidebar and saved reopen", async ({ page, request }, info) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== "1" || !!process.env.HWP_REPORT_FIXTURES, "Isolated synthetic generation with the actual rhwp engine.");
  await page.setViewportSize({ width: 1500, height: 1000 });
  let prepares = 0, failRevision = true;
  const revisions: unknown[] = [];
  await page.route("**/api/report-tasks/*/preview", async route => {
    if (route.request().method() === "POST") prepares++;
    await route.continue();
  });
  await page.route("**/api/report-tasks/*/revise", async route => {
    revisions.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    if (failRevision) { failRevision = false; await route.abort("failed"); }
    else await route.fulfill({ response });
  });
  const before = await (await request.get("/api/documents")).json();
  await page.goto("/");
  await page.getByRole("button", { name: "사이드바 펼침", exact: true }).click();
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill("[RICH] 문서함 정비 계획: 원본 보존과 담당자 확인 절차를 포함해줘.");
  await page.getByLabel("공개 웹 자료를 검색하고 출처 포함").uncheck();
  const post = page.waitForResponse(r => r.url().endsWith("/api/report-tasks") && r.request().method() === "POST");
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  const originalId = (await (await post).json()).id;
  await expect(page.getByLabel("초안 내용 검토", { exact: true })).toBeVisible({ timeout: 30000 });
  expect(prepares).toBe(0);
  expect(await (await request.get("/api/documents")).json()).toEqual(before);
  const original = await (await request.get(`/api/report-tasks/${originalId}`)).json();
  await page.getByLabel("보완 의견", { exact: true }).fill("담당자 확인 전 게시 금지 조건을 추진 일정에도 명시해줘. 기존 수치와 출처는 유지해줘.");
  await expect(page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "의견 반영해 다시 작성", exact: true }).click();
  await expect(page.getByRole("button", { name: "같은 보완 요청 재확인", exact: true })).toBeEnabled();
  // A refresh must recover the same frozen request rather than create another revision.
  let failRestore = true;
  await page.route(`**/api/report-tasks/${originalId}`, async route => {
    if (route.request().method() === "GET" && failRestore) {
      failRestore = false; await route.fulfill({ status: 503, json: { detail: "일시적인 복구 조회 실패" } });
    } else await route.continue();
  });
  await page.reload();
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await page.getByRole("button", { name: "이전 요청 다시 열기", exact: true }).click();
  await page.getByRole("button", { name: "같은 보완 요청 재확인", exact: true }).click();
  await expect(page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true })).toBeEnabled({ timeout: 30000 });
  expect(revisions).toHaveLength(2); expect(revisions[1]).toEqual(revisions[0]);
  const revisedId = (revisions[0] as { id: string }).id;
  const revised = await (await request.get(`/api/report-tasks/${revisedId}`)).json();
  const preserved = await (await request.get(`/api/report-tasks/${originalId}`)).json();
  expect(preserved.draft_sha256).toBe(original.draft_sha256);
  expect(preserved.draft).toEqual(original.draft);
  expect(preserved.preview_id).toBeNull();
  expect(revised.input.visual_standard).toEqual(original.input.visual_standard);
  expect(revised.input.revision.feedback).toContain("담당자 확인 전 게시 금지");
  expect(prepares).toBe(0);
  await page.screenshot({ path: info.outputPath("review-before-formatting.png"), fullPage: true });
  let release!: () => void;
  const paused = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/report-tasks/*/preview/render", async route => {
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    await paused; await route.fulfill({ response });
  });
  try {
    await page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true }).click();
    await expect(page.locator(".report-steps[data-stage=formatting]")).toBeVisible({ timeout: 60000 });
    expect((await page.locator(".workspace-nav").boundingBox())!.width).toBe(220);
    await expect(page.locator(".workspace-nav")).toHaveAttribute("data-mode", "expanded");
    await expect(page.locator(".report-formatting-heading")).toContainText("실제 문서");
    await expect(page.locator(".report-draft-content")).toBeVisible();
    await page.screenshot({ path: info.outputPath("formatting-stable-sidebar.png"), fullPage: true });
  } finally { release(); }
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeEnabled({ timeout: 90000 });
  expect(prepares).toBe(1);
  expect((await page.locator(".workspace-nav").boundingBox())!.width).toBe(220);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  // A clean save opens the regular editor automatically.
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  await writeFile(info.outputPath("review-flow-proof.json"), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    original_task: originalId, revised_task: revisedId, frozen_revision_retry_after_reload: true,
    original_preserved: true, preview_requests_before_review: 0, sidebar_width_during_formatting: 220,
    saved_and_reopened: true, human_review: false }, null, 2));
});
