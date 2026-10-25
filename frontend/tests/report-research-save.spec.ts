import { test, expect, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { unzipSync, strFromU8 } from "fflate";

const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");

async function documentText(page: Page, bytes: Uint8Array) {
  const sections = Object.entries(unzipSync(bytes)).filter(([name]) => /^Contents\/section\d+\.xml$/.test(name)).map(([, data]) => strFromU8(data));
  return page.evaluate(sources => sources.map(source => {
    const document = new DOMParser().parseFromString(source, "application/xml");
    if (document.querySelector("parsererror")) throw Error("Invalid synthetic HWPX section XML");
    return [...document.getElementsByTagNameNS("http://www.hancom.co.kr/hwpml/2011/paragraph", "t")].map(node => node.textContent || "").join("");
  }).join("\n"), sections);
}

test("REAL rhwp + MOCK research: incomplete lookup final draft preserves warning, six full limitations, manual edits and frozen save retry", async ({ page, request }) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== "1" || !!process.env.HWP_REPORT_FIXTURES, "Synthetic completed model output and reused verified real engine only.");
  const documentsBefore = await (await request.get("/api/documents")).json();
  const saves: unknown[] = [];
  let generationPosts = 0, previewPosts = 0;
  page.on("request", req => {
    if (req.method() !== "POST") return;
    if (req.url().endsWith("/api/report-tasks")) generationPosts++;
    if (/\/api\/report-tasks\/[^/]+\/preview$/.test(req.url())) previewPosts++;
  });
  await page.route("**/api/report-tasks/*/preview/save", async route => {
    saves.push(route.request().postDataJSON());
    if (saves.length === 1) {
      const response = await route.fetch(); expect(response.ok(), await response.text()).toBeTruthy();
      return route.abort("failed");
    }
    return route.continue();
  });
  await page.goto("/");
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill("[UNVERIFIED] 합성 회의실 예약 검토 보고서");
  await expect(page.getByRole("checkbox", { name: /공개 웹 자료/ })).toBeChecked();
  const posted = page.waitForResponse(response => response.url().endsWith("/api/report-tasks") && response.request().method() === "POST");
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  const id = (await (await posted).json()).id;
  const review = page.getByLabel("초안 내용 검토", { exact: true });
  await expect(review).toBeVisible({ timeout: 30000 });
  await expect(review.getByLabel("자료 확인 상태")).toContainText("자료 조회 미완료 · 최신 정보 미검증");
  const task = await (await request.get(`/api/report-tasks/${id}`)).json();
  expect(task.status).toBe("ready"); expect(task.input.research).toBe(true);
  expect(task.input.research_policy).toBe("allow_unverified");
  expect(task.execution.web_search_calls).toBe(0); expect(task.draft.research_notice.status).toBe("unverified");
  expect(task.draft_sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(task.draft.limitations).toHaveLength(6);
  for (const limitation of task.draft.limitations) { expect(limitation.length).toBe(400); await expect(review.getByText(limitation, { exact: true })).toBeVisible(); }
  await review.getByText("미검증 참고 출처 1개", { exact: true }).click();
  await expect(review.getByText("웹 확인 미완료", { exact: true })).toBeVisible();
  await expect(review.getByRole("link", { name: "합성 참고 자료", exact: true })).toHaveCount(0);
  expect(previewPosts).toBe(0); expect(generationPosts).toBe(1);
  expect(await (await request.get("/api/documents")).json()).toEqual(documentsBefore);
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true }).click();
  await expect(page.locator(".formatted-report").getByLabel("자료 확인 상태")).toContainText("최신 정보 미검증");
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeEnabled({ timeout: 90000 });
  const preview = await (await request.get(`/api/report-tasks/${id}/preview`)).json();
  expect(preview.plan[0].note_presentation).toBe("reference-note-titles-v1");
  expect(task.draft.sources).toEqual([{ id: "S1", title: "합성 참고 자료",
    url: "https://example.invalid/synthetic-reference", published: "2026-01-01" }]);
  expect(previewPosts).toBe(1); expect(saves).toHaveLength(0);
  expect(await (await request.get("/api/documents")).json()).toEqual(documentsBefore);
  const frame = page.frames().find(frame => frame.url().includes("/rhwp/"))!;
  await frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End"); await page.keyboard.insertText(" 수동 편집 보존 확인");
  await expect.poll(() => frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toContain("수동 편집 보존 확인");
  const edited = Uint8Array.from(await frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  const pages = await frame.evaluate(() => (window as any).hwpAssistantBridge.documentState().pageCount);
  await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).click();
  await expect(page.getByRole("button", { name: "같은 보고서 저장 재확인", exact: true })).toBeEnabled();
  await expect(page.locator(".formatted-report").getByLabel("자료 확인 상태")).toContainText("최신 정보 미검증");
  await page.getByRole("button", { name: "같은 보고서 저장 재확인", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  await expect(page.locator(".report-editor-slot.active iframe")).toHaveCount(0);
  expect(saves).toHaveLength(2); expect(saves[1]).toEqual(saves[0]); expect(generationPosts).toBe(1);
  const file = (await page.getByRole("link", { name: "저장본 다운로드", exact: true }).getAttribute("href"))!;
  const bytes = await (await request.get(file)).body(), nativeText = await documentText(page, bytes);
  expect(hash(bytes)).toBe(hash(edited));
  expect(nativeText).toContain("수동 편집 보존 확인"); expect(nativeText).toContain("합성 참고 자료");
  // A newly frozen titles-only plan does not print candidate URLs; the full
  // unverified source and date remain in the canonical draft checked below.
  expect(nativeText).not.toContain(task.draft.sources[0].url);
  for (const warning of task.draft.research_notice.warnings) expect(nativeText).toContain(warning.message);
  for (const limitation of task.draft.limitations) expect(nativeText).toContain(limitation);
  const versions = await (await request.get(file.split("/versions/")[0] + "/versions")).json();
  expect(versions).toHaveLength(1);
  const frozen = await (await request.get(`/api/report-tasks/${id}`)).json();
  expect(frozen.draft_sha256).toBe(task.draft_sha256); expect(frozen.draft).toEqual(task.draft);
  await expect(page.getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  const reopened = await (await page.locator(".block-editor-slot.active iframe").elementHandle())!.contentFrame();
  if (!reopened) throw Error("저장한 미검증 보고서의 일반 편집기를 찾지 못했습니다.");
  const reopenedBytes = Uint8Array.from(await reopened.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  expect(hash(reopenedBytes)).toBe(hash(bytes));
  expect(await reopened.evaluate(() => (window as any).hwpAssistantBridge.documentState().pageCount)).toBe(pages);
  const body = await documentText(page, reopenedBytes);
  expect(body).toContain("수동 편집 보존 확인");
  for (const warning of task.draft.research_notice.warnings) expect(body).toContain(warning.message);
});
