import { test, expect, type Frame, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

// Real main.tsx + backend catalog/lease/verification + patched rhwp. Only the
// model answer is synthetic, via the explicit test-only API fixture guard.
test.skip(process.env.HWP_E2E_ASSISTANT_DESIGNS !== "1", "Requires the explicit synthetic design-inference API fixture");
test.beforeEach(async ({ page }) => {
  // Isolate buffer preview/apply/undo from the host's 20-second autosave.
  // Editor timers remain active; these cases do not verify default autosave.
  await page.addInitScript(() => {
    if (window !== window.top) return;
    const interval = window.setInterval.bind(window);
    window.setInterval = ((handler: TimerHandler, delay?: number, ...args: any[]) =>
      interval(delay === 20000 ? () => {} : handler, delay, ...args)) as typeof window.setInterval;
  });
});
const sample = new URL("../../samples/paragraph-and-table.hwpx", import.meta.url);
async function frame(page: Page): Promise<Frame> {
  const handle = await page.locator(".block-editor-slot.active iframe").elementHandle();
  const editor = await handle!.contentFrame();
  if (!editor) throw new Error("활성 rhwp 프레임이 없습니다.");
  return editor;
}
async function bytes(page: Page) {
  return Buffer.from(await (await frame(page)).evaluate(() =>
    Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
}
async function focus(page: Page, kind: "table" | "paragraph") {
  await (await frame(page)).getByRole("textbox", { name: "문서 편집 입력", exact: true }).focus();
  await page.keyboard.press("Control+Home");
  for (let attempt = 0; attempt < 35; attempt++) {
    const target = await (await frame(page)).evaluate(() => {
      try { return (window as any).hwpAssistantBridge.styleTarget(); } catch { return null; }
    });
    if (target?.kind === kind && (kind === "table" || target.paragraph === 1)) return target;
    await page.keyboard.press("ArrowDown");
  }
  throw new Error(`실제 커서가 ${kind} 대상에 도달하지 못했습니다.`);
}
async function text(page: Page, value: Buffer) {
  const files = unzipSync(value);
  return page.evaluate(section => Array.from(new DOMParser().parseFromString(section, "application/xml")
    .getElementsByTagNameNS("*", "t")).map(node => node.textContent), strFromU8(files["Contents/section0.xml"]));
}
async function openAndAsk(page: Page) {
  const name = `합성 AI 디자인 ${Date.now()}-${test.info().parallelIndex}.hwpx`;
  const original = await readFile(sample);
  await page.goto("/");
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({ name, mimeType: "application/hwp+zip", buffer: original });
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name, exact: true }) });
  const originalUrl = (await row.getByRole("link", { name: "다운로드", exact: true }).getAttribute("href"))!;
  await row.getByRole("button", { name, exact: true }).click();
  await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  const selected = await focus(page, "table"), frozen = await bytes(page);
  await page.getByRole("button", { name: "문서 AI 대화 열기", exact: true }).click();
  if (await page.getByRole("button", { name: "새 대화", exact: true }).isVisible())
    await page.getByRole("button", { name: "새 대화", exact: true }).click();
  await page.getByLabel("대화 이름", { exact: true }).fill(`합성 디자인 대화 ${Date.now()}`);
  await page.getByRole("button", { name: "AI와 대화 시작", exact: true }).click();
  const assistant = page.getByRole("region", { name: "문서와 AI 대화", exact: true });
  await expect(assistant).toBeVisible();
  await assistant.getByLabel("AI에게 요청", { exact: true }).fill("현재 표의 내용은 유지하고 읽기 쉬운 디자인을 추천해줘.");
  const responsePromise = page.waitForResponse(response => response.request().method() === "POST"
    && /\/api\/threads\/[^/]+\/assistant-turns$/.test(new URL(response.url()).pathname));
  await assistant.getByRole("button", { name: "보내기", exact: true }).click();
  const response = await responsePromise;
  expect(response.ok(), await response.text()).toBeTruthy();
  const accepted = await response.json();
  await expect(assistant.getByRole("button", { name: "디자인 제안 보기", exact: true })).toBeEnabled({ timeout: 15000 });
  return { original, originalUrl, documentUrl: originalUrl.split("/versions/")[0], selected, frozen, assistant, accepted };
}

test("REAL rhwp + MOCK AI: full editor adopts the frozen catalog proposal only after review; undo preserves original", async ({ page, request }) => {
  const doc = await openAndAsk(page);
  const versions = await (await request.get(doc.documentUrl + "/versions")).json();
  const turn = await (await request.get(`/api/assistant-turns/${doc.accepted.id}`)).json();
  const catalog = await (await request.get("/api/editor-designs")).json();
  const choice = catalog.designs.find((item: any) => item.id === "reference-table");
  expect(turn.status).toBe("ready");
  expect(turn.execution.executor).toBe("synthetic-design-fixture");
  expect(turn.execution.actual_model_calls).toBe(0);
  expect(turn.design_context.target).toEqual(doc.selected);
  expect(turn.design_proposals[0]).toMatchObject({ kind: "design", id: choice.id, sha256: choice.sha256, target: doc.selected });
  expect(await bytes(page)).toEqual(doc.frozen);
  await doc.assistant.getByRole("button", { name: "디자인 제안 보기", exact: true }).click();
  const panel = page.getByLabel("문서 서식·디자인", { exact: true });
  await expect(panel.getByRole("button", { name: "문서에 적용", exact: true })).toBeVisible({ timeout: 90000 });
  await expect(panel.getByRole("img", { name: "적용 후 실제 문서", exact: true })).toBeVisible();
  expect(await bytes(page)).toEqual(doc.frozen); // The AI proposal and preview do not apply/save.
  await panel.getByRole("button", { name: "문서에 적용", exact: true }).click();
  await expect(panel.getByRole("status").filter({ hasText: "서식을 적용했습니다" })).toBeVisible({ timeout: 90000 });
  const applied = await bytes(page);
  expect(applied).not.toEqual(doc.frozen);
  expect(await text(page, applied)).toEqual(await text(page, doc.frozen));
  expect(await (await request.get(doc.documentUrl + "/versions")).json()).toEqual(versions);
  expect(await (await request.get(doc.originalUrl)).body()).toEqual(doc.original);
  await panel.getByRole("button", { name: "닫기", exact: true }).click();
  await page.getByRole("button", { name: "방금 적용 취소", exact: true }).click();
  expect(await bytes(page)).toEqual(doc.frozen);
});

for (const change of ["target", "unsaved bytes"] as const) {
  test(`REAL rhwp + MOCK AI: full editor refuses a proposal after changed ${change}`, async ({ page, request }) => {
    const doc = await openAndAsk(page), writes: string[] = [];
    page.on("request", request => {
      if (request.method() === "POST" && new URL(request.url()).pathname.endsWith("/design-verify")) writes.push(request.url());
    });
    if (change === "target") await focus(page, "paragraph");
    else { await focus(page, "table"); await page.keyboard.press("End"); await page.keyboard.insertText(" 미저장 합성 변경"); }
    const current = await bytes(page), versions = await (await request.get(doc.documentUrl + "/versions")).json();
    await doc.assistant.getByRole("button", { name: "디자인 제안 보기", exact: true }).click();
    await expect(doc.assistant.getByRole("alert")).toContainText(/문서나 선택 위치가 바뀌었/);
    expect(await bytes(page)).toEqual(current);
    expect(writes).toEqual([]);
    expect(await page.getByLabel("문서 서식·디자인", { exact: true }).count()).toBe(0);
    expect(await (await request.get(doc.documentUrl + "/versions")).json()).toEqual(versions);
    expect(await (await request.get(doc.originalUrl)).body()).toEqual(doc.original);
  });
}
