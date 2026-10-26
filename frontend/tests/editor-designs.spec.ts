import { test, expect, type Frame, type Page } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

// Real hosted rhwp + real backend. Synthetic document only; no AI requests.
// Hold the host autosave timer so each version assertion has an explicit save.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (window !== window.top) return;
    const interval = window.setInterval.bind(window);
    window.setInterval = ((handler: TimerHandler, delay?: number, ...args: any[]) =>
      interval(delay === 20000 ? () => {} : handler, delay, ...args)) as typeof window.setInterval;
  });
});
const sample = new URL("../../samples/paragraph-and-table.hwpx", import.meta.url);
const titleCard = "기준서 제목 · 파란 상하선 적용";
const tableCard = "기준서 표 · 회색 머리글 적용";
const panel = (page: Page) => page.getByLabel("문서 서식·디자인", { exact: true });
const status = (page: Page) => page.getByLabel("현재 문서와 저장 상태").getByRole("status");
async function frame(page: Page): Promise<Frame> {
  const handle = await page.locator(".block-editor-slot.active iframe").elementHandle();
  const result = await handle!.contentFrame();
  if (!result) throw new Error("활성 rhwp 프레임이 없습니다.");
  return result;
}
async function bytes(page: Page) {
  return Buffer.from(await (await frame(page)).evaluate(() => Array.from(
    (window as any).hwpAssistantBridge.exportSnapshot().bytes,
  ) as number[]));
}
async function target(page: Page) {
  return (await frame(page)).evaluate(() => {
    try { return (window as any).hwpAssistantBridge.styleTarget(); } catch { return null; }
  });
}
async function open(page: Page) {
  const name = `합성 디자인 검증 ${Date.now()}-${test.info().parallelIndex}.hwpx`;
  const original = await readFile(sample);
  await page.goto("/");
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({ name, mimeType: "application/hwp+zip", buffer: original });
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name, exact: true }) });
  const originalUrl = (await row.getByRole("link", { name: "다운로드", exact: true }).getAttribute("href"))!;
  await row.getByRole("button", { name, exact: true }).click();
  await expect(status(page)).toHaveText("저장 완료", { timeout: 90000 });
  await page.getByRole("button", { name: "서식·디자인 열기", exact: true }).click();
  await expect(panel(page).getByRole("button", { name: titleCard, exact: true })).toBeVisible();
  return { name, original, originalUrl, documentUrl: originalUrl.split("/versions/")[0] };
}
async function focus(page: Page, kind: "paragraph" | "table", paragraph = 1) {
  const editor = await frame(page);
  await editor.getByRole("textbox", { name: "문서 편집 입력", exact: true }).focus();
  await page.keyboard.press("Control+Home");
  for (let attempt = 0; attempt < 30; attempt++) {
    const current = await target(page);
    if (current?.kind === kind && (kind === "table" || current.paragraph === paragraph)) return current;
    await page.keyboard.press("ArrowDown");
  }
  throw new Error(`키보드로 ${kind} 대상에 도달하지 못했습니다: ${JSON.stringify(await target(page))}`);
}
async function waitPrepared(page: Page) {
  await expect.poll(async () =>
    await panel(page).getByRole("button", { name: "문서에 적용", exact: true }).isVisible()
      || await panel(page).getByRole("alert").isVisible(), { timeout: 90000 }).toBeTruthy();
  if (await panel(page).getByRole("alert").isVisible()) throw new Error(await panel(page).getByRole("alert").innerText());
  await expect(panel(page).getByRole("img", { name: "적용 전 실제 문서", exact: true })).toBeVisible();
  await expect(panel(page).getByRole("img", { name: "적용 후 실제 문서", exact: true })).toBeVisible();
}
async function preview(page: Page, card: string) {
  await panel(page).locator(".editor-design-option").filter({ has: page.getByRole("button", { name: card, exact: true }) })
    .getByRole("button", { name: "적용 전 보기", exact: true }).click();
  await waitPrepared(page);
}
async function applied(page: Page) {
  await expect.poll(async () =>
    await panel(page).getByRole("status").filter({ hasText: "서식을 적용했습니다" }).isVisible()
      || await panel(page).getByRole("alert").isVisible(), { timeout: 90000 }).toBeTruthy();
  if (await panel(page).getByRole("alert").isVisible()) throw new Error(await panel(page).getByRole("alert").innerText());
  await expect(page.getByRole("button", { name: "이전 서식으로 되돌리기", exact: true })).toBeEnabled();
}
async function proof(page: Page, data: Buffer) {
  const files = unzipSync(data);
  return page.evaluate(({ section, header }) => {
    const parse = (value: string) => new DOMParser().parseFromString(value, "application/xml");
    const s = parse(section), h = parse(header);
    const nodes = (root: Document | Element, name: string) => Array.from(root.getElementsByTagNameNS("*", name));
    const get = (kind: string, id: string | null) => nodes(h, kind).find(n => n.getAttribute("id") === id)!;
    const plain = (p: Element) => Array.from(p.children).filter(n => n.localName === "run")
      .flatMap(run => Array.from(run.children).filter(n => n.localName === "t").map(n => n.textContent || "")).join("");
    const attrs = (node?: Element) => node ? Object.fromEntries(Array.from(node.attributes).filter(a => a.name !== "id").map(a => [a.name, a.value])) : null;
    const border = (id: string | null) => {
      const fill = get("borderFill", id);
      return fill ? { edges: Array.from(fill.children).filter(n => n.localName.endsWith("Border")).map(n => [n.localName, attrs(n)]),
        brush: nodes(fill, "winBrush").map(attrs) } : null;
    };
    const styles = (p: Element) => {
      const para = get("paraPr", p.getAttribute("paraPrIDRef"));
      return { text: plain(p), paragraph: [attrs(nodes(para, "align")[0]), attrs(nodes(para, "lineSpacing")[0]),
        ...["left", "right", "indent", "prev", "next"].map(key => attrs(nodes(para, key)[0]))],
        characters: Array.from(p.children).filter(n => n.localName === "run" && nodes(n, "t").length).map(run => {
          const char = get("charPr", run.getAttribute("charPrIDRef"));
          return { text: nodes(run, "t").map(n => n.textContent).join(""), height: char.getAttribute("height"), color: char.getAttribute("textColor"), bold: nodes(char, "bold").length > 0 };
        }), border: border(nodes(para, "border")[0]?.getAttribute("borderFillIDRef") || null) };
    };
    return { text: nodes(s, "p").map(plain), body: Array.from(s.documentElement.children).map(styles),
      tables: nodes(s, "tbl").map(table => ({ rows: table.getAttribute("rowCnt"), cols: table.getAttribute("colCnt"),
        cells: nodes(table, "tc").map(cell => ({ text: nodes(cell, "p").map(plain), border: border(cell.getAttribute("borderFillIDRef")),
          size: attrs(nodes(cell, "cellSz")[0]), margin: attrs(nodes(cell, "cellMargin")[0]) })) })) };
  }, { section: strFromU8(files["Contents/section0.xml"]), header: strFromU8(files["Contents/header.xml"]) });
}

test("REAL rhwp designs: direct table/title cards preserve content and untouched styles; undo restores original", async ({ page, request }, info) => {
  const doc = await open(page);
  await focus(page, "paragraph");
  await page.keyboard.press("End"); await page.keyboard.insertText(" · 미저장 합성 내용");
  const original = await bytes(page), before = await proof(page, original);
  const versions = await (await request.get(doc.documentUrl + "/versions")).json();
  await focus(page, "table");
  await panel(page).getByRole("button", { name: tableCard, exact: true }).click();
  await applied(page);
  const tableAfter = await proof(page, await bytes(page));
  expect(tableAfter.text).toEqual(before.text);
  expect(tableAfter.body[1]).toEqual(before.body[1]);
  expect(tableAfter.tables).not.toEqual(before.tables);
  expect(tableAfter.tables[0].cells[0].border?.brush[0]?.faceColor).toBe("#E5E5E5");
  await focus(page, "paragraph");
  await preview(page, titleCard);
  await panel(page).getByRole("button", { name: "취소", exact: true }).click();
  await page.getByRole("button", { name: "이전 서식으로 되돌리기", exact: true }).click();
  expect(await bytes(page)).toEqual(original);
  await focus(page, "paragraph");
  await panel(page).getByRole("button", { name: titleCard, exact: true }).click();
  await applied(page);
  const titleAfter = await proof(page, await bytes(page));
  expect(titleAfter.text).toEqual(before.text);
  expect(titleAfter.tables).toEqual(before.tables);
  expect(titleAfter.body[1]).not.toEqual(before.body[1]);
  expect(titleAfter.body[1].border?.edges).toEqual(expect.arrayContaining([
    ["topBorder", expect.objectContaining({ color: "#437FC1" })], ["bottomBorder", expect.objectContaining({ color: "#437FC1" })],
  ]));
  expect(await (await request.get(doc.documentUrl + "/versions")).json()).toEqual(versions);
  expect(await (await request.get(doc.originalUrl)).body()).toEqual(doc.original);
  await writeFile(info.outputPath("direct-title-applied.hwpx"), await bytes(page));
  await page.screenshot({ path: info.outputPath("direct-design-applied.png"), fullPage: true });
});

test("REAL rhwp designs: preview rejects a different target and later unsaved text", async ({ page, request }) => {
  const doc = await open(page);
  await focus(page, "paragraph");
  const original = await bytes(page), versions = await (await request.get(doc.documentUrl + "/versions")).json();
  // Delay delivery of an actual engine SVG, never replace its content or issue
  // editing commands. A cancelled old render must not poison the next preview.
  await page.evaluate(async () => {
    const modulePath = "/src/editor.ts";
    const { EditorAdapter } = await import(/* @vite-ignore */ modulePath);
    const originalMount = EditorAdapter.mount;
    const gate: any = { started: false, armed: true, release: () => {}, restore: () => { EditorAdapter.mount = originalMount; } };
    (window as any).__designSvgGate = gate;
    EditorAdapter.mount = async (...args: any[]) => {
      const adapter = await originalMount.apply(EditorAdapter, args);
      if (gate.armed) {
        gate.armed = false;
        const render = adapter.sdk.getPageSvg.bind(adapter.sdk);
        adapter.sdk.getPageSvg = async (index: number) => {
          const svg = await render(index);
          gate.started = true;
          await new Promise<void>(resolve => { gate.release = resolve; });
          return svg;
        };
      }
      return adapter;
    };
  });
  try {
    await panel(page).locator(".editor-design-option").filter({ has: page.getByRole("button", { name: titleCard, exact: true }) })
      .getByRole("button", { name: "적용 전 보기", exact: true }).click();
    await expect.poll(() => page.evaluate(() => (window as any).__designSvgGate.started), { timeout: 90000 }).toBeTruthy();
    await panel(page).getByRole("button", { name: "취소", exact: true }).click();
  } finally {
    await page.evaluate(async () => {
      const gate = (window as any).__designSvgGate;
      gate.restore(); gate.release();
      await new Promise(resolve => setTimeout(resolve, 0));
      delete (window as any).__designSvgGate;
    });
  }
  expect(await bytes(page)).toEqual(original);
  await preview(page, titleCard);
  await focus(page, "table");
  await panel(page).getByRole("button", { name: "문서에 적용", exact: true }).click();
  await expect(panel(page).getByRole("alert")).toContainText(/바뀌|변경|다시/);
  expect(await bytes(page)).toEqual(original);
  await panel(page).getByRole("button", { name: "취소", exact: true }).click();
  await focus(page, "paragraph"); await preview(page, titleCard);
  await focus(page, "paragraph"); await page.keyboard.press("End"); await page.keyboard.insertText(" 후속 편집 보존");
  const changed = await bytes(page);
  await panel(page).getByRole("button", { name: "문서에 적용", exact: true }).click();
  await expect(panel(page).getByRole("alert")).toContainText(/바뀌|변경|다시/);
  expect(await bytes(page)).toEqual(changed);
  expect(await (await request.get(doc.documentUrl + "/versions")).json()).toEqual(versions);
});

test("REAL rhwp designs: gallery example opens an existing document and applies at its selected table", async ({ page, request }, info) => {
  const doc = await open(page);
  const before = await proof(page, await bytes(page));
  const versions = await (await request.get(doc.documentUrl + "/versions")).json();
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  await page.getByRole("button", { name: "양식 보관함", exact: true }).click();
  await page.getByLabel("표·제목 예시", { exact: true }).getByRole("button")
    .filter({ hasText: "기준서 표 · 회색 머리글" }).click();
  const picker = page.getByLabel("디자인을 적용할 문서", { exact: true });
  await expect(picker).toBeVisible();
  await picker.getByRole("button", { name: doc.name, exact: true }).click();
  await expect(status(page)).toHaveText("저장 완료", { timeout: 90000 });
  await expect(panel(page).locator(".editor-design-option.selected")
    .getByRole("button", { name: tableCard, exact: true })).toBeVisible();
  expect(await proof(page, await bytes(page))).toEqual(before);
  await focus(page, "table");
  await panel(page).getByRole("button", { name: tableCard, exact: true }).click();
  await applied(page);
  const after = await proof(page, await bytes(page));
  expect(after.text).toEqual(before.text);
  expect(after.body[1]).toEqual(before.body[1]);
  expect(after.tables[0].cells[0].border?.brush[0]?.faceColor).toBe("#E5E5E5");
  expect(await (await request.get(doc.documentUrl + "/versions")).json()).toEqual(versions);
  expect(await (await request.get(doc.originalUrl)).body()).toEqual(doc.original);
  await page.screenshot({ path: info.outputPath("gallery-document-table-applied.png"), fullPage: true });
});

test("REAL rhwp designs: lost verify response retries frozen input; whole standard preview saves and reopens", async ({ page, request }, info) => {
  test.setTimeout(240000);
  const doc = await open(page); await focus(page, "table");
  const original = await bytes(page), before = await proof(page, original);
  const requests: unknown[] = []; let drop = true;
  await page.route("**/api/documents/*/design-verify", async route => {
    requests.push(route.request().postDataJSON());
    const response = await route.fetch(); expect(response.ok(), await response.text()).toBeTruthy();
    if (drop) { drop = false; await route.abort("connectionreset"); } else await route.fulfill({ response });
  });
  await panel(page).locator(".editor-design-option").filter({ has: page.getByRole("button", { name: tableCard, exact: true }) })
    .getByRole("button", { name: "적용 전 보기", exact: true }).click();
  await expect(panel(page).getByRole("button", { name: "같은 요청 재확인", exact: true })).toBeVisible({ timeout: 90000 });
  expect(await bytes(page)).toEqual(original);
  await panel(page).getByRole("button", { name: "같은 요청 재확인", exact: true }).click();
  await waitPrepared(page); expect(requests).toHaveLength(2); expect(requests[1]).toEqual(requests[0]);
  await panel(page).getByRole("button", { name: "문서에 적용", exact: true }).click(); await applied(page);
  expect((await proof(page, await bytes(page))).text).toEqual(before.text);
  await panel(page).getByRole("button", { name: "문서 전체", exact: true }).click();
  const catalog = await (await request.get("/api/editor-designs")).json();
  await panel(page).getByRole("button", { name: `${catalog.standards[0].name} 미리보기`, exact: true }).click();
  await waitPrepared(page);
  await page.screenshot({ path: info.outputPath("whole-standard-comparison.png"), fullPage: true });
  await panel(page).getByRole("button", { name: "크게 비교", exact: true }).click();
  const comparison = page.getByRole("dialog", { name: "서식 적용 전후 크게 비교", exact: true });
  await expect(comparison.getByRole("img", { name: "적용 후 실제 문서", exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("whole-standard-large-comparison.png"), fullPage: true });
  await comparison.getByRole("button", { name: "비교 닫기", exact: true }).click();
  await panel(page).getByRole("button", { name: "문서에 적용", exact: true }).click(); await applied(page);
  const output = await proof(page, await bytes(page)); expect(output.text).toEqual(before.text);
  await panel(page).getByRole("button", { name: "닫기", exact: true }).click();
  await page.getByRole("button", { name: "저장", exact: true }).click(); await expect(status(page)).toHaveText("저장 완료");
  const savedUrl = (await page.getByRole("link", { name: "저장본 다운로드", exact: true }).getAttribute("href"))!;
  expect(savedUrl).not.toBe(doc.originalUrl); expect(await (await request.get(doc.originalUrl)).body()).toEqual(doc.original);
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  await page.getByRole("button", { name: doc.name, exact: true }).click();
  await expect(status(page)).toHaveText("저장 완료", { timeout: 90000 });
  expect(await proof(page, await bytes(page))).toEqual(output);
});
