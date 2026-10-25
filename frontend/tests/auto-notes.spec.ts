import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");

test("REAL rhwp + MOCK report: automatic notes number multiple sources and reset single notes through save and reopen", async ({ page, request }, info) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== "1" || !!process.env.HWP_REPORT_FIXTURES,
    "Synthetic source content with actual rhwp, no model call.");
  test.setTimeout(180000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await expect(page.getByLabel("문서 양식", { exact: true })).toBeEnabled();
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill("[AUTO-NOTES] 합성 자료 점검 현황을 별첨1로 작성해 주석을 확인");
  await page.getByLabel("공개 웹 자료를 검색하고 출처 포함").uncheck();
  const accepted = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/report-tasks");
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  const job = await (await accepted).json();
  expect(job.input.visual_standard.settings.citation_style).toBe("AUTO");
  await page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true }).click();
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeEnabled({ timeout: 90000 });
  expect(await page.getByRole("alert").allTextContents()).toEqual([]);
  const frozen = await (await request.get(`/api/report-tasks/${job.id}/preview`)).json();
  const completed = await (await request.get(`/api/report-tasks/${job.id}`)).json();
  expect(frozen.plan[0].note_presentation).toBe("reference-note-titles-v1");
  expect(frozen.plan[0].note_grouping).toBe("reference-note-section-groups-v2");
  expect(completed.draft.sources.map((source: any) => source.id)).toEqual(["S8", "S4"]);
  expect(completed.draft.sources.map((source: any) => [source.url, source.published])).toEqual([
    ["https://example.com/check", "2026. 10. 23."], ["https://example.com/approval", ""]]);
  expect(completed.draft.sections[0].paragraphs[0]).toContain("[S8][S4]");
  const frame = await (await page.locator(".report-editor-slot.active iframe").elementHandle())!.contentFrame();
  if (!frame) throw new Error("실제 보고서 편집기를 찾을 수 없습니다.");
  const bytes = Buffer.from(await frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  const text = await page.evaluate(section => {
    const document = new DOMParser().parseFromString(section, "application/xml");
    return Array.from(document.getElementsByTagNameNS("*", "p")).map(paragraph => Array.from(paragraph.children)
      .filter(node => node.localName === "run").flatMap(run => Array.from(run.children).filter(node => node.localName === "t")
        .map(node => node.textContent || "")).join(""));
  }, strFromU8(unzipSync(bytes)["Contents/section0.xml"]));
  const first = text.findIndex(value => value.startsWith("□ 자료 24건"));
  expect(text[first]).toBe("□ 자료 24건을 확인함. 주1), 주2) 동일 근거를 유지함. 주1)");
  // One uninterrupted section shares the source numbering and explanations.
  const single = text.findIndex(value => value.startsWith("ㅇ 보완 6건"));
  expect(single).toBe(first + 1);
  expect(text[single]).toBe("ㅇ 보완 6건은 담당 확인 후 게시함. 주2)");
  expect(text[single + 1]).toBe("주1) 합성 점검 자료");
  expect(text[single + 2]).toBe("주2) 합성 승인 조건");
  const scope = frozen.plan.find((item: any) => item.text.startsWith("□ 자료 24건")).note_scope;
  expect(frozen.plan.find((item: any) => item.text.startsWith("ㅇ 보완 6건")).note_scope).toBe(scope);
  expect(frozen.plan.filter((item: any) => item.note_scope === scope && item.note_source_id).map((item: any) => item.note_source_id)).toEqual(["S8", "S4"]);
  // The following table and next section each begin a fresh note scope.
  expect(text).toContain("확인 대상 주1)");
  expect(text).toContain("완료 주2)");
  expect(text.some(value => value.startsWith("주1) 합성 승인 조건"))).toBeTruthy();
  expect(text.some(value => value.startsWith("주2) 합성 점검 자료"))).toBeTruthy();
  const last = text.findIndex(value => value.startsWith("□ 원본과 이전 버전"));
  expect(text[last]).toBe("□ 원본과 이전 버전을 보존함. *");
  expect(text[last + 1]).toMatch(/^\* 합성 점검 자료/);
  expect(frozen.plan.find((item: any) => item.text.startsWith("□ 원본과 이전 버전")).note_scope).not.toBe(scope);
  for (const source of completed.draft.sources) expect(text.join("\n")).not.toContain(source.url);
  const pages = await frame.evaluate(() => (window as any).hwpAssistantBridge.documentState().pageCount);
  await page.screenshot({ path: info.outputPath("auto-notes-report.png"), fullPage: true });
  await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const href = (await page.getByRole("link", { name: "저장본 다운로드", exact: true }).getAttribute("href"))!;
  const saved = await (await request.get(href)).body();
  expect(hash(saved)).toBe(hash(bytes));
  // A clean save opens the regular editor automatically.
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const reopened = await (await page.locator(".block-editor-slot.active iframe").elementHandle())!.contentFrame();
  if (!reopened) throw new Error("저장 보고서를 다시 열지 못했습니다.");
  expect(hash(Buffer.from(await reopened.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[])))).toBe(hash(saved));
  expect((await (await request.get(`/api/report-tasks/${job.id}/preview`)).json()).layout_sha256).toBe(frozen.layout_sha256);
  await writeFile(info.outputPath("auto-notes-report.hwpx"), saved);
  await writeFile(info.outputPath("auto-notes-proof.json"), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    human_review: false, automated_approval: true, canonical_source_ids: ["S8", "S4"], canonical_draft_preserved: true,
    local_marker_checks: true, pages, saved_and_reopened: true, saved_sha256: hash(saved), paragraphs: text }, null, 2));
});
