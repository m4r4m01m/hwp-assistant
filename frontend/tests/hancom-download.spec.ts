import { test, expect, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

const sha = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");

async function syntheticDocument(page: Page) {
  await page.goto("/");
  // Public synthetic material only. Generate with the exact served core rather
  // than checking a user's document or a local diagnostic blob into the suite.
  const bytes = await page.evaluate(async () => {
    const manifest = await (await fetch("/comparison-engine.json")).json();
    const core = await import(/* @vite-ignore */ "/comparison-core.js");
    await core.default({ module_or_path: new Uint8Array(await (await fetch(manifest.wasm)).arrayBuffer()) });
    (window as any).__hancomTestCore = core;
    const d = core.HwpDocument.createEmpty();
    try {
      d.createBlankDocument();
      const table = JSON.parse(d.createTable(0, 0, 0, 3, 3));
      const colors = ["#FF0000", "#00FF00", "#0000FF"], bgr = [255, 65280, 16711680];
      for (let row = 0; row < 3; row++) for (let column = 0; column < 3; column++) {
        const cell = row * 3 + column;
        const text = ["TEXT", "FILL", "BORDER"][column] + " " + ["RED", "GREEN", "BLUE"][row];
        d.insertTextInCell(0, table.paraIdx, table.controlIdx, cell, 0, 0, text);
        d.applyCharFormatInCell(0, table.paraIdx, table.controlIdx, cell, 0, 0, text.length,
          JSON.stringify({ textColor: column === 0 ? colors[row] : "#000000" }));
        const border = { type: 1, width: 5, color: column === 2 ? colors[row] : "#000000" };
        d.setCellProperties(0, table.paraIdx, table.controlIdx, cell, JSON.stringify({
          fillType: "solid", fillColor: column === 1 ? colors[row] : "#FFFFFF",
          borderLeft: border, borderRight: border, borderTop: border, borderBottom: border,
        }));
      }
      for (let index = 0; index < 3; index++) {
        const paragraph = d.getParagraphCount(0) - 1;
        d.splitParagraph(0, paragraph, d.getParagraphLength(0, paragraph));
        const shape = JSON.parse(d.createShapeControl(JSON.stringify({ sectionIdx: 0,
          paraIdx: d.getParagraphCount(0) - 1, charOffset: 0, shapeType: "rectangle",
          treatAsChar: true, width: 6000, height: 2500 })));
        d.setShapeProperties(0, shape.paraIdx, shape.controlIdx, JSON.stringify({
          fillType: "solid", fillBgColor: bgr[index], borderColor: bgr[index], borderWidth: 100, lineType: 1,
        }));
      }
      return Array.from(d.exportHwpx()) as number[];
    } finally { d.free(); }
  });
  const source = Buffer.from(bytes), name = `합성 RGB 한컴 다운로드 ${Date.now()}.hwpx`;
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({ name, mimeType: "application/hwp+zip", buffer: source });
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name, exact: true }) });
  const file = (await row.getByRole("link", { name: "다운로드", exact: true }).getAttribute("href"))!;
  await row.getByRole("button", { name: "다운로드 형식 선택", exact: true }).click();
  const menu = page.getByRole("group", { name: "다운로드 형식", exact: true });
  await expect(menu.getByRole("link", { name: "HWPX 원본", exact: true })).toHaveAttribute("href", file);
  return { source, name, row, file, path: file.split("/versions/")[0], menu };
}

async function inspectWithActualCore(page: Page, bytes: Buffer) {
  const result = await page.evaluate(values => {
    const d = new (window as any).__hancomTestCore.HwpDocument(Uint8Array.from(values));
    try { return { pages: d.pageCount(), text: d.getTextFileText() as string, hwpx: Array.from(d.exportHwpx()) as number[] }; }
    finally { d.free(); }
  }, Array.from(bytes));
  const zip = unzipSync(Uint8Array.from(result.hwpx));
  const semantics = await page.evaluate(xml => {
    const parser = new DOMParser(), header = parser.parseFromString(xml.header, "application/xml"), section = parser.parseFromString(xml.section, "application/xml");
    const all = (element: Document | Element, localName: string) => Array.from(element.getElementsByTagNameNS("*", localName));
    const chars = new Map(all(header, "charPr").map(element => [element.getAttribute("id"), element]));
    const fills = new Map(all(header, "borderFill").map(element => [element.getAttribute("id"), element]));
    const styles = (element: Element) => Array.from(element.getElementsByTagName("*")).filter(child =>
      child.localName.endsWith("Border") || ["winBrush", "lineShape", "sz"].includes(child.localName))
      .map(child => ({ tag: child.localName, attributes: Object.fromEntries(Array.from(child.attributes).map(attribute => [attribute.name, attribute.value])) }));
    return {
      paragraphs: all(section, "p").length, tables: all(section, "tbl").length, shapes: all(section, "rect").length,
      runs: all(section, "run").flatMap(run => Array.from(run.children).filter(child => child.localName === "t")
        .map(text => ({ text: text.textContent, color: chars.get(run.getAttribute("charPrIDRef"))?.getAttribute("textColor") }))),
      cells: all(section, "tc").map(cell => styles(fills.get(cell.getAttribute("borderFillIDRef"))!)),
      shapeStyles: all(section, "rect").map(styles),
    };
  }, { header: strFromU8(zip["Contents/header.xml"]), section: strFromU8(zip["Contents/section0.xml"]) });
  return { pages: result.pages, text: result.text, semantics };
}

test("HANCOM HWP real engine: saved download preserves RGB text, tables, shapes and canonical versions", async ({ page, request }, info) => {
  const state = await syntheticDocument(page);
  const versions = await (await request.get(state.path + "/versions")).json();
  const requestDownload = page.waitForEvent("download");
  await state.menu.getByRole("button", { name: "한컴 2018용 HWP", exact: true }).click();
  const download = await requestDownload;
  expect(download.suggestedFilename()).toBe(state.name.replace(/\.hwpx$/, ".hwp"));
  const output = await readFile((await download.path())!);
  expect(Array.from(output.subarray(0, 8))).toEqual([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]);
  const before = await inspectWithActualCore(page, state.source), after = await inspectWithActualCore(page, output);
  expect(after).toEqual(before);
  expect(before.pages).toBe(1);
  expect(before.semantics.tables).toBe(1);
  expect(before.semantics.shapes).toBe(3);
  expect(before.semantics.runs.filter(run => run.text?.startsWith("TEXT ")).map(run => run.color)).toEqual(["#FF0000", "#00FF00", "#0000FF"]);
  expect(await (await request.get(state.file)).body()).toEqual(state.source);
  expect(await (await request.get(state.path + "/versions")).json()).toEqual(versions);
  await expect(state.row.getByRole("link", { name: "다운로드", exact: true })).toHaveAttribute("href", state.file);
  await page.screenshot({ path: info.outputPath("hancom-download.png"), fullPage: true });
  await writeFile(info.outputPath("proof.json"), JSON.stringify({ engine: "real pinned rhwp", ai_calls: 0,
    source_sha256: sha(state.source), output_sha256: sha(output), native_hancom_verified: false,
    saved_blob_and_versions_unchanged: true, roundtrip: after }, null, 2));
});

test("HANCOM HWP safety: source hash mismatch and reported content loss block download", async ({ page, request }) => {
  const state = await syntheticDocument(page);
  const downloads: string[] = [];
  page.on("download", download => downloads.push(download.suggestedFilename()));
  await page.route("**" + state.path + "/versions", async route => {
    const response = await route.fetch(), versions = await response.json();
    await route.fulfill({ response, json: versions.map((version: any) => ({ ...version, sha256: "0".repeat(64) })) });
  });
  await state.menu.getByRole("button", { name: "한컴 2018용 HWP", exact: true }).click();
  await expect(state.menu.getByRole("alert")).toContainText("해시가 일치하지 않습니다");
  expect(downloads).toHaveLength(0);
  await page.unroute("**" + state.path + "/versions");

  // Inject one reported loss around the real converter. This verifies the
  // product's fail-closed boundary, not a claim of an actual engine defect.
  await page.evaluate(() => {
    const prototype = (window as any).__hancomTestCore.HwpDocument.prototype;
    (window as any).__hancomOriginalExport = prototype.exportHwpWithReport;
    prototype.exportHwpWithReport = function () {
      const artifact = (window as any).__hancomOriginalExport.call(this);
      return { contentLoss: () => JSON.stringify({ schemaVersion: 1, outputFormat: "hwp", count: 1,
        losses: [{ code: "binaryContentEmptied", subject: "binaryData", path: "BinData/SYNTHETIC", reason: "rawPassthroughUnavailable" }] }),
        takeBytes: () => { throw Error("A lossy artifact must not be consumed"); }, free: () => artifact.free() };
    };
  });
  try {
    await state.menu.getByRole("button", { name: "한컴 2018용 HWP", exact: true }).click();
    await expect(state.menu.getByRole("alert")).toContainText("내용 손실이 감지되었습니다");
    expect(downloads).toHaveLength(0);
  } finally {
    await page.evaluate(() => { (window as any).__hancomTestCore.HwpDocument.prototype.exportHwpWithReport = (window as any).__hancomOriginalExport; delete (window as any).__hancomOriginalExport; });
  }
  expect(await (await request.get(state.file)).body()).toEqual(state.source);
  expect(await (await request.get(state.path + "/versions")).json()).toHaveLength(1);
  await expect(state.menu.getByRole("link", { name: "HWPX 원본", exact: true })).toHaveAttribute("href", state.file);
});
