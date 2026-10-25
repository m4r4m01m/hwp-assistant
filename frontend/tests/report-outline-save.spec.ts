import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");

test("REAL rhwp + MOCK outline: selected content headings and new note policies survive reviewed manual save and ordinary reopen", async ({ page, request }, info) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== "1" || !!process.env.HWP_REPORT_FIXTURES,
    "Fixed synthetic output, real outline API and verified optimized engine; no inference.");
  const originalDocuments = await (await request.get("/api/documents")).json();
  await page.goto("/");
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await expect(page.getByLabel("문서 양식", { exact: true })).toBeEnabled();
  await page.getByLabel("대제목 구성 방식").selectOption("response");
  await page.getByRole("textbox", { name: "2번째 대제목", exact: true }).fill("자료 현황");
  await expect(page.getByLabel("대제목 구성 방식")).toHaveValue("custom");
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill("[OUTLINE] 합성 자료의 점검 상태와 제안할 대응을 보고해줘");
  await page.getByLabel("공개 웹 자료를 검색하고 출처 포함").uncheck();
  const posted = page.waitForResponse(response => response.url().endsWith("/api/report-tasks") && response.request().method() === "POST");
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  const accepted = await (await posted).json(), headings = ["개요", "자료 현황", "대응", "향후 계획"];
  expect(accepted.input.section_outline).toEqual(headings);
  const review = page.getByLabel("초안 내용 검토", { exact: true });
  await expect(review).toBeVisible({ timeout: 30000 });
  const task = await (await request.get(`/api/report-tasks/${accepted.id}`)).json();
  expect(task.status).toBe("ready"); expect(task.input.section_outline).toEqual(headings);
  expect(task.draft.sections.map((section: any) => section.heading)).toEqual(headings);
  expect(task.draft.sources).toEqual([{ id: "S1", title: "합성 점검 근거", url: "https://example.invalid/outline-evidence", published: "2026-10-26" }]);
  await expect(review.locator(".report-review-section h2").filter({ has: page.locator("span") })).toHaveText(headings.map((heading, index) => `${index + 1}${heading}`));
  expect(task.approval).toBeNull();
  expect(await (await request.get("/api/documents")).json()).toEqual(originalDocuments);
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true }).click();
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeEnabled({ timeout: 90000 });
  const frozen = await (await request.get(`/api/report-tasks/${task.id}/preview`)).json();
  expect(frozen.plan[0].note_presentation).toBe("reference-note-titles-v1");
  expect(frozen.plan[0].note_grouping).toBe("reference-note-section-groups-v2");
  expect(frozen.plan[0].table_layout).toBe("reference-content-columns-v1");
  expect(frozen.plan.some((entry: any) => entry.table_common_source_id === "S1" && entry.canonical_citations?.length)).toBe(true);
  const frame = await (await page.locator(".report-editor-slot.active iframe").elementHandle())!.contentFrame();
  if (!frame) throw Error("개요 보고서의 실제 미리보기 엔진이 없습니다.");
  await frame.getByRole("textbox", { name: "문서 편집 입력", exact: true }).focus();
  await page.keyboard.press("Control+End"); await page.keyboard.insertText(" 개요 선택 및 저장 전환 검수");
  await expect.poll(() => frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toContain("개요 선택 및 저장 전환 검수");
  const edited = Uint8Array.from(await frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  const pages = await frame.evaluate(() => (window as any).hwpAssistantBridge.documentState().pageCount);
  await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  await expect(page.locator(".report-editor-slot.active iframe")).toHaveCount(0);
  const href = (await page.getByRole("link", { name: "저장본 다운로드", exact: true }).getAttribute("href"))!;
  const saved = await (await request.get(href)).body(); expect(hash(saved)).toBe(hash(edited));
  const section = strFromU8(unzipSync(saved)["Contents/section0.xml"]);
  for (const heading of headings) expect(section).toContain(heading);
  expect(section).toContain("합성 점검 근거"); expect(section).not.toContain(task.draft.sources[0].url);
  expect(section).toContain("개요 선택 및 저장 전환 검수");
  expect(await (await request.get(href.split("/versions/")[0] + "/versions")).json()).toHaveLength(1);
  const retained = await (await request.get(`/api/report-tasks/${task.id}`)).json();
  expect(retained.draft_sha256).toBe(task.draft_sha256); expect(retained.draft).toEqual(task.draft);
  expect(retained.input).toEqual(task.input);
  const reopened = await (await page.locator(".block-editor-slot.active iframe").elementHandle())!.contentFrame();
  if (!reopened) throw Error("개요 보고서의 일반 편집기를 찾지 못했습니다.");
  const reopenedBytes = Uint8Array.from(await reopened.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  expect(hash(reopenedBytes)).toBe(hash(saved));
  expect(await reopened.evaluate(() => (window as any).hwpAssistantBridge.documentState().pageCount)).toBe(pages);
  await writeFile(info.outputPath("outline-proof.json"), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    section_outline: headings, draft_sha256: task.draft_sha256, canonical_input_and_draft_preserved: true,
    policies: frozen.plan[0], saved_sha256: hash(saved), pages, manual_edit_and_ordinary_reopen: true, versions: 1 }, null, 2));
});
