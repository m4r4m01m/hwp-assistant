import { test, expect, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

const hash = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");

// Read-only inspection. Every document mutation below is a product UI action.
async function inspect(page: Page, bytes: Buffer) {
  const files = unzipSync(bytes);
  return page.evaluate(({ section, header, binData }) => {
    const parser = new DOMParser();
    const content = parser.parseFromString(section, "application/xml");
    const styles = parser.parseFromString(header, "application/xml");
    const all = (node: Document | Element, name: string) => Array.from(node.getElementsByTagNameNS("*", name));
    if (all(content, "parsererror").length || all(styles, "parsererror").length) throw Error("Invalid HWPX XML");
    const serialize = (node: Element) => new XMLSerializer().serializeToString(node);
    const reference = (name: string, id: string | null) => {
      const node = all(styles, name).find(el => el.getAttribute("id") === id);
      if (!node) throw Error(`Missing ${name} ${id}`);
      return serialize(node);
    };
    const text = (node: Element) => all(node, "t").map(el => el.textContent).join("");
    const body = Array.from(content.documentElement.children).filter(el => el.localName === "p");
    const title = body.find(p => text(p) === "반복 왕복 표 서식 시험");
    if (!title) throw Error("Title missing");
    return {
      paragraphs: body.map(text),
      title_format: {
        paragraph: reference("paraPr", title.getAttribute("paraPrIDRef")),
        characters: all(title, "run").map(run => reference("charPr", run.getAttribute("charPrIDRef"))),
      },
      tables: all(content, "tbl").map(table => ({
        xml: serialize(table),
        rows: table.getAttribute("rowCnt"), columns: table.getAttribute("colCnt"),
        cells: all(table, "tc").map(cell => ({
          text: text(cell),
          border: reference("borderFill", cell.getAttribute("borderFillIDRef")),
        })),
      })),
      bin_data: binData,
    };
  }, { section: strFromU8(files["Contents/section0.xml"]), header: strFromU8(files["Contents/header.xml"]),
    binData: Object.keys(files).filter(name => name.startsWith("BinData/")) });
}

test("REAL rhwp, NO AI: three save/download/reupload/reopen cycles preserve table and history; restore adds a version", async ({ page, request, baseURL }, info) => {
  const errors: string[] = [], external: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", r => {
    if (/^https?:/.test(r.url()) && new URL(r.url()).origin !== new URL(baseURL!).origin) external.push(r.url());
  });
  await page.goto("/");
  await page.getByRole("button", { name: "빈 문서 만들기", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
  let frame = page.frames().find(f => f.url().includes("/rhwp/"))!;
  await frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("반복 왕복 표 서식 시험");
  await page.keyboard.press("Home");
  await page.keyboard.press("Shift+End");
  await page.keyboard.press("Control+b");
  await frame.getByRole("button", { name: "가운데 정렬", exact: true }).click();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await page.keyboard.press("Control+b");
  await frame.getByRole("button", { name: "왼쪽 정렬", exact: true }).click();
  await page.keyboard.insertText("반복 편집 문단: 일정과 표의 서식을 보존합니다.");
  await page.keyboard.press("Enter");
  await frame.locator('button[data-cmd="table:create"]').click();
  const grid = frame.locator('[data-row="1"][data-col="1"]');
  await grid.hover(); await grid.click();
  for (const [index, text] of ["담당자", "김민수", "마감일", "10월 9일"].entries()) {
    if (index) await page.keyboard.press("Tab");
    await page.keyboard.insertText(text);
  }
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  let url = (await page.getByRole("link", { name: "저장본 다운로드" }).getAttribute("href"))!;
  const originalUrl = url, docPath = url.split("/versions/")[0];
  const original = await (await request.get(url)).body();
  const baseline = await inspect(page, original);
  expect(baseline.tables).toHaveLength(1);
  expect(baseline.tables[0].cells.map(cell => cell.text)).toEqual(["담당자", "김민수", "마감일", "10월 9일"]);
  expect(baseline.tables[0].rows).toBe("2"); expect(baseline.tables[0].columns).toBe("2");
  expect(baseline.title_format.characters.join("")).toContain(":bold");
  expect(baseline.title_format.paragraph).toContain("CENTER");
  const observed = new Map([[originalUrl, hash(original)]]);
  const cycles: unknown[] = [];
  const assertPreserved = async (bytes: Buffer) => {
    const inspected = await inspect(page, bytes);
    expect(inspected.tables).toEqual(baseline.tables);
    expect(inspected.title_format).toEqual(baseline.title_format);
    expect(inspected.bin_data).toEqual(baseline.bin_data);
    return inspected;
  };
  const openCurrent = async () => {
    await page.getByRole("row").filter({ has: page.locator(`a[href="${url}"]`) })
      .locator("td").first().getByRole("button").click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    frame = page.frames().find(f => f.url().includes("/rhwp/"))!;
    const bytes = Buffer.from(await frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes)) as number[]);
    return assertPreserved(bytes);
  };
  for (let cycle = 1; cycle <= 3; cycle++) {
    await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
    await openCurrent();
    await frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
    await page.keyboard.press("Control+Home");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("End");
    expect(await frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toContain("반복 편집 문단");
    await page.keyboard.insertText(` [${cycle}차 편집]`);
    await page.getByRole("button", { name: "저장", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    url = (await page.getByRole("link", { name: "저장본 다운로드" }).getAttribute("href"))!;
    const saved = await (await request.get(url)).body();
    const savedState = await assertPreserved(saved);
    for (let previous = 1; previous <= cycle; previous++) expect(savedState.paragraphs.join(" ")).toContain(`[${previous}차 편집]`);
    observed.set(url, hash(saved));
    const savedVersion = url.split("/").at(-2);
    await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
    const more = page.getByRole("row").filter({ has: page.locator(`a[href="${url}"]`) })
      .getByRole("button", { name: / 더보기$/ });
    if (await more.getAttribute("aria-expanded") !== "true") await more.click();
    const actions = page.locator(`#${await more.getAttribute("aria-controls")}`);
    await expect(actions).toBeVisible();
    const responsePromise = page.waitForResponse(r => r.request().method() === "POST" && r.url().includes(docPath + "/versions?"));
    await actions.getByLabel(/ 새 버전 업로드$/)
      .setInputFiles({ name: `합성-왕복-${cycle}.hwpx`, mimeType: "application/hwp+zip", buffer: saved });
    const response = await responsePromise;
    expect(response.ok(), await response.text()).toBeTruthy();
    const reuploaded = await response.json();
    expect(reuploaded.cause).toBe("reupload"); expect(reuploaded.base_version).toBe(savedVersion);
    url = `${docPath}/versions/${reuploaded.id}/file`;
    expect(await (await request.get(url)).body()).toEqual(saved);
    observed.set(url, hash(saved));
    const reopened = await openCurrent();
    expect(reopened.paragraphs).toEqual(savedState.paragraphs);
    for (const [versionUrl, digest] of observed) expect(hash(await (await request.get(versionUrl)).body())).toBe(digest);
    const versions = await (await request.get(docPath + "/versions")).json();
    expect(versions).toHaveLength(1 + cycle * 2);
    cycles.push({ cycle, saved_version: savedVersion, reuploaded_version: reuploaded.id,
      sha256: hash(saved), version_count: versions.length, table_and_title_preserved: true, prior_versions_unchanged: true });
    await writeFile(info.outputPath(`cycle-${cycle}.hwpx`), saved);
  }
  await page.screenshot({ path: info.outputPath("third-cycle.png"), fullPage: true });
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  await page.getByRole("row").filter({ has: page.locator(`a[href="${url}"]`) })
    .getByRole("button", { name: "버전 기록", exact: true }).click();
  page.once("dialog", dialog => dialog.accept()); // Automated fixture restore, not a human action.
  const restoredResponse = page.waitForResponse(r => r.request().method() === "POST" && r.url().includes("/restore?"));
  await page.locator(".history-table tbody tr").filter({ has: page.locator(`a[href="${originalUrl}"]`) })
    .getByRole("button", { name: "이 버전 복원", exact: true }).click();
  const restoreResponse = await restoredResponse;
  expect(restoreResponse.ok(), await restoreResponse.text()).toBeTruthy();
  const restored = await restoreResponse.json();
  expect(restored.cause).toBe("restore"); expect(restored.id).not.toBe(originalUrl.split("/").at(-2));
  url = `${docPath}/versions/${restored.id}/file`;
  expect(await (await request.get(url)).body()).toEqual(original);
  await expect(page.locator(".modal")).not.toBeVisible();
  expect((await openCurrent()).paragraphs).toEqual(baseline.paragraphs);
  const versions = await (await request.get(docPath + "/versions")).json();
  expect(versions).toHaveLength(8);
  for (const [versionUrl, digest] of observed) expect(hash(await (await request.get(versionUrl)).body())).toBe(digest);
  expect(await (await request.get(docPath + "/applications")).json()).toEqual([]);
  expect(errors).toEqual([]); expect(external).toEqual([]);
  await page.screenshot({ path: info.outputPath("restored.png"), fullPage: true });
  await writeFile(info.outputPath("proof.json"), JSON.stringify({ kind: "real rhwp; synthetic UI document; no AI; no Hancom",
    completed_at: new Date().toISOString(), document_path: docPath, actual_model_calls: 0, cycles,
    original_sha256: hash(original), restored_version: restored.id, restored_sha256: hash(original),
    version_count: versions.length, versions, baseline, page_errors: errors, external_requests: external }, null, 2));
});
