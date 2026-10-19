import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { unzipSync, strFromU8 } from "fflate";

const hash = (value: Uint8Array | string) => createHash("sha256").update(value).digest("hex");
const projectRoot = fileURLToPath(new URL("../../", import.meta.url));
const compositions = [
  { name: "executive", preset: "appendix-1", tocPage: null, firstBodyPage: 1 },
  { name: "vendor", preset: "appendix-2", tocPage: 2, firstBodyPage: 3 },
  { name: "customer", preset: "appendix-3", tocPage: 1, firstBodyPage: 2 },
];

async function allRenderedPages(page: Page, bytes: Buffer, info: TestInfo, firstHeading: string | null) {
  // A separate read-only SDK harness renders the already-saved bytes. It does
  // not operate an existing user editor or generate a different layout.
  const laboratory = await page.context().newPage();
  await laboratory.route("**/reference-live-lab/**", async route => {
    const name = new URL(route.request().url()).pathname.split("/").pop()!;
    if (name.endsWith(".js") && /^[a-z-]+\.js$/.test(name))
      return route.fulfill({ contentType: "application/javascript", body: await readFile(resolve(projectRoot, "frontend/node_modules/@rhwp/editor", name)) });
    return route.fulfill({ contentType: "text/html", body: '<html><body style="margin:0"><div id="editor" style="height:100vh"></div></body></html>' });
  });
  await laboratory.goto(new URL("/reference-live-lab/", page.url()).href);
  const pages = await laboratory.evaluate(async encoded => {
    const { createEditor } = await import(/* @vite-ignore */ "/reference-live-lab/index.js");
    const sdk = await createEditor("#editor", { studioUrl: location.origin + "/rhwp/index.html?chrome=embed" });
    await sdk.loadFile(Uint8Array.from(atob(encoded), char => char.charCodeAt(0)).buffer, "saved-reference-report.hwpx", { suppressDialogs: true });
    const count = await sdk.pageCount();
    const result: string[] = [];
    for (let page = 0; page < count; page++) result.push(await sdk.getPageSvg(page));
    sdk.destroy();
    return result;
  }, bytes.toString("base64"));
  const visible: { page: number; text: string; glyph_count: number; title_clearance: any }[] = [];
  for (const [index, svg] of pages.entries()) {
    await writeFile(info.outputPath(`page-${index + 1}.svg`), svg);
    await laboratory.setContent(`<html><body style="margin:0;background:white">${svg}</body></html>`);
    const detail = await laboratory.evaluate(async heading => {
      await document.fonts.ready;
      const root = document.querySelector("svg")!;
      const glyphs = Array.from(root.querySelectorAll("text"));
      const normalize = (text: string) => text.replace(/\s/g, "");
      let title_clearance = null;
      if (heading) {
        const rows = new Map<string, SVGTextElement[]>();
        for (const glyph of glyphs) {
          const y = glyph.getAttribute("y")!;
          rows.set(y, [...(rows.get(y) || []), glyph]);
        }
        const row = Array.from(rows.values()).find(items => normalize(items.map(node => node.textContent).join("")).includes(normalize(heading)));
        const rules = Array.from(root.querySelectorAll("line")).filter(line =>
          line.getAttribute("stroke")?.toLowerCase() === "#437fc1" &&
          line.getAttribute("y1") === line.getAttribute("y2") &&
          Math.abs(Number(line.getAttribute("x2")) - Number(line.getAttribute("x1"))) > 500);
        title_clearance = { blue_rule_count: rules.length,
          bottom_rule_edge: rules.length ? Math.max(...rules.map(line => Number(line.getAttribute("y1")) + Number(line.getAttribute("stroke-width")) / 2)) : null,
          first_heading_top: row ? Math.min(...row.map(glyph => glyph.getBBox().y)) : null };
      }
      return { text: glyphs.map(node => node.textContent).join(""), glyph_count: glyphs.length, title_clearance };
    }, index === 0 ? firstHeading : null);
    visible.push({ page: index + 1, ...detail });
    await laboratory.locator("svg").screenshot({ path: info.outputPath(`page-${index + 1}.png`) });
  }
  await laboratory.close();
  return visible;
}

for (const composition of compositions) {
  test(`REAL rhwp + RECORDED reference sample: ${composition.preset} review, exact format, save and reopen`, async ({ page, request }, info) => {
    test.skip(!process.env.HWP_REPORT_FIXTURES, "Explicit actual Codex result directory required; this replay makes no model call.");
    test.setTimeout(240000);
    const record = JSON.parse(await readFile(resolve(process.env.HWP_REPORT_FIXTURES!, `actual-${composition.name}.json`), "utf8"));
    expect(record.result).toBe("passed");
    expect(record.evidence.executor).toBe("codex-app-server");
    expect(record.evidence.completed).toBe(true);
    const catalog = await (await request.get("/api/visual-standards")).json();
    const preset = catalog.presets.find((item: any) => item.settings.preset_id === composition.preset);
    const selection = Object.fromEntries(["id", "name", "revision", "settings", "settings_sha256", "base_standard", "preview_sha256"].map(key => [key, preset[key]]));
    expect(selection).toEqual(record.input.visual_standard);
    await page.goto("/");
    await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
    // Opening an optional settings disclosure is UI navigation, not a bypass of
    // any generation, content review, approval or storage fence.
    const standard = page.getByLabel("문서 서식 표준", { exact: true });
    if (!(await standard.isVisible())) {
      for (const summary of await page.locator("details > summary").all()) {
        if (!(await standard.isVisible())) await summary.click();
      }
    }
    await expect(standard).toBeEnabled();
    await standard.selectOption(preset.id);
    if (record.input.cover) {
      await page.getByLabel("표지 작성일", { exact: true }).fill(record.input.cover.date);
      await page.getByLabel("표지 작성 부서", { exact: true }).fill(record.input.cover.department);
    }
    await page.getByLabel("보고서 작성 요청", { exact: true }).fill(record.input.prompt);
    await page.getByLabel("공개 웹 자료를 검색하고 출처 포함").uncheck();
    const template = page.getByLabel("보고서 템플릿", { exact: true });
    if (await template.count()) await template.selectOption(record.input.template.id);
    const posted = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/report-tasks");
    await page.getByRole("button", { name: "Codex로 보고서 초안 만들기", exact: true }).click();
    const response = await posted, job = await response.json();
    expect(response.ok(), JSON.stringify(job)).toBeTruthy();
    expect(response.request().postDataJSON().visual_standard).toEqual(selection);
    expect(response.request().postDataJSON().template).toEqual(record.input.template);
    expect(response.request().postDataJSON().cover).toEqual(record.input.cover);
    const review = page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true });
    await expect(review).toBeVisible({ timeout: 90000 });
    expect(await page.locator(".report-editor-slot iframe").count()).toBe(0);
    const reviewed = await (await request.get(`/api/report-tasks/${job.id}`)).json();
    expect(reviewed.draft).toEqual(record.draft);
    expect(reviewed.execution.delivery).toBe("recorded-result-replay");
    expect(reviewed.execution.actual_model_calls_this_run).toBe(0);
    await page.screenshot({ path: info.outputPath("content-review.png"), fullPage: true });
    await review.click();
    const save = page.getByRole("button", { name: "초안 확정·서버 저장", exact: true });
    await expect.poll(async () => await save.isEnabled().catch(() => false) || await page.getByRole("alert").isVisible().catch(() => false), { timeout: 120000 }).toBeTruthy();
    expect(await page.getByRole("alert").allTextContents()).toEqual([]);
    await expect(save).toBeEnabled();
    const frozen = await (await request.get(`/api/report-tasks/${job.id}/preview`)).json();
    expect(frozen.visual_standard).toEqual(selection);
    const engine = await (await page.locator(".report-editor-slot.active iframe").elementHandle())!.contentFrame();
    if (!engine) throw new Error("No active report engine");
    const exported = await engine.evaluate(() => {
      const result = (window as any).hwpAssistantBridge.exportSnapshot();
      return { bytes: [...result.bytes] as number[], loss: result.contentLoss, pages: (window as any).hwpAssistantBridge.documentState().pageCount };
    });
    expect(exported.loss.count).toBe(0);
    const bytes = Buffer.from(exported.bytes), archive = unzipSync(bytes);
    const structure = await page.evaluate(({ header, section, title, cover }) => {
      const xml = new DOMParser().parseFromString(section, "application/xml");
      const formats = new DOMParser().parseFromString(header, "application/xml");
      const children = Array.from(xml.documentElement.children);
      const text = (p: Element) => Array.from(p.children).filter(n => n.localName === "run")
        .flatMap(r => Array.from(r.children).filter(n => n.localName === "t")).map(n => n.textContent || "").join("");
      const allText = Array.from(xml.getElementsByTagNameNS("*", "p")).map(text);
      const chars = Array.from(formats.getElementsByTagNameNS("*", "charPr"));
      const face = Array.from(formats.getElementsByTagNameNS("*", "fontface")).find(n => n.getAttribute("lang") === "HANGUL")!;
      const prose = children.filter(p => /^[□ㅇ-]\s/.test(text(p))).map(p => {
        const run = Array.from(p.children).find(r => r.localName === "run" && r.textContent?.trim())!;
        const char = chars.find(c => c.getAttribute("id") === run.getAttribute("charPrIDRef"))!;
        const reference = char.getElementsByTagNameNS("*", "fontRef")[0].getAttribute("hangul");
        return { text: text(p), height: Number(char.getAttribute("height")), font: Array.from(face.children).find(f => f.getAttribute("id") === reference)!.getAttribute("face") };
      });
      const paper = Array.from(xml.getElementsByTagNameNS("*", "pagePr"))[0];
      const margin = paper.getElementsByTagNameNS("*", "margin")[0];
      return { text: allText.join("\n"), prose,
        title: children.findIndex(p => Array.from(p.getElementsByTagNameNS("*", "t")).map(t => t.textContent || "").join("") === title),
        cover: cover ? Object.fromEntries(Object.entries(cover).map(([key, value]) => [key, children.findIndex(p => text(p) === value)])) : null,
        toc: children.findIndex(p => Array.from(p.getElementsByTagNameNS("*", "t")).some(t => t.textContent?.replace(/\s/g, "") === "<목차>")),
        page: { width: Number(paper.getAttribute("width")), height: Number(paper.getAttribute("height")), margins: Object.fromEntries(Array.from(margin.attributes).map(a => [a.name, Number(a.value)])) } };
    }, { header: strFromU8(archive["Contents/header.xml"]), section: strFromU8(archive["Contents/section0.xml"]), title: record.draft.title, cover: record.input.cover });
    expect(structure.title).toBeGreaterThanOrEqual(0);
    expect(structure.prose.length).toBeGreaterThan(0);
    expect(structure.prose.every(p => p.height === 1400 && p.font === "휴먼명조")).toBe(true);
    expect(Math.abs(structure.page.width - 210 * 7200 / 25.4)).toBeLessThanOrEqual(1);
    expect(Math.abs(structure.page.height - 297 * 7200 / 25.4)).toBeLessThanOrEqual(1);
    for (const side of ["top", "bottom", "left", "right", "header", "footer"])
      expect(Math.abs(structure.page.margins[side] - (selection.settings as any)[`margin_${side}_mm`] * 7200 / 25.4)).toBeLessThanOrEqual(1);
    if (structure.cover) {
      for (const index of Object.values(structure.cover) as number[]) {
        expect(index).toBeGreaterThanOrEqual(0);
        expect(await engine.evaluate(i => (window as any).hwpAssistantBridge.paragraphPage(0, i), index)).toBe(1);
      }
    }
    expect(structure.text).not.toContain("REPORT_SLOT_");
    expect(structure.text).not.toContain("2015");
    expect(structure.text).not.toContain("○○팀");
    for (const value of ["24개", "18개", "6개", "원본", "이전 버전", "담당 확인 전"])
      expect(structure.text.replace(/\s/g, "")).toContain(value.replace(/\s/g, ""));
    let evidence: any = null, headingTargets: any[] = [];
    if (composition.tocPage !== null) {
      expect(frozen.toc_verified).toBe(true);
      headingTargets = frozen.toc.headings.map(({ id, section, paragraph, length, text_sha256 }: any) => ({ id, section, paragraph, length, text_sha256 }));
      evidence = await engine.evaluate(headings => (window as any).hwpAssistantBridge.layoutEvidence(headings), headingTargets);
      expect(evidence.export_sha256).toBe(hash(bytes));
      expect(evidence.page_count).toBe(frozen.toc.page_count);
      expect(Object.fromEntries(evidence.headings.map((h: any) => [h.id, h.page]))).toEqual(frozen.toc.pages);
      expect(Math.min(...evidence.headings.map((h: any) => h.page))).toBe(composition.firstBodyPage);
      expect(await engine.evaluate(i => (window as any).hwpAssistantBridge.paragraphPage(0, i), structure.toc)).toBe(composition.tocPage);
      for (const heading of frozen.toc.headings) expect(hash(heading.text)).toBe(heading.text_sha256);
    } else {
      expect(frozen.toc_policy).toBeUndefined();
      expect(structure.toc).toBe(-1);
    }
    expect(await engine.evaluate(i => (window as any).hwpAssistantBridge.paragraphPage(0, i), structure.title)).toBe(1);
    await page.screenshot({ path: info.outputPath("formatted-report.png"), fullPage: true });
    await save.click();
    await expect(page.getByRole("status")).toHaveText("HWPX 서버 저장 완료");
    const href = (await page.getByRole("link", { name: "완성 보고서 다운로드", exact: true }).getAttribute("href"))!;
    const saved = await (await request.get(href)).body();
    expect(hash(saved)).toBe(hash(bytes));
    await writeFile(info.outputPath(`${composition.preset}.hwpx`), saved);
    await page.getByRole("button", { name: "보고서 편집하기", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    const reopened = await (await page.locator(".block-editor-slot.active iframe").elementHandle())!.contentFrame();
    if (!reopened) throw new Error("No reopened ordinary editor");
    const roundtrip = await reopened.evaluate(() => [...(window as any).hwpAssistantBridge.exportSnapshot().bytes] as number[]);
    expect(hash(Buffer.from(roundtrip))).toBe(hash(saved));
    expect(await reopened.evaluate(() => (window as any).hwpAssistantBridge.documentState().pageCount)).toBe(exported.pages);
    if (evidence) {
      const current = await reopened.evaluate(headings => (window as any).hwpAssistantBridge.layoutEvidence(headings), headingTargets);
      expect(current.export_sha256).toBe(evidence.export_sha256);
      expect(current.page_count).toBe(evidence.page_count);
      expect(current.headings).toEqual(evidence.headings);
    }
    const renderedPages = await allRenderedPages(page, saved, info, composition.preset === "appendix-1" ? record.draft.sections[0].heading : null);
    expect(renderedPages).toHaveLength(exported.pages);
    if (composition.preset === "appendix-1") {
      const clearance = renderedPages[0].title_clearance;
      expect(clearance.blue_rule_count).toBe(2);
      expect(clearance.first_heading_top).not.toBeNull();
      expect(clearance.first_heading_top).toBeGreaterThan(clearance.bottom_rule_edge);
    }
    await writeFile(info.outputPath("reference-live-proof.json"), JSON.stringify({ actual_model_calls_this_run: 0,
      recorded_model_calls: record.actual_model_calls, model_execution: record.evidence, frozen_model_input: record.input,
      recorded_draft_sha256: hash(JSON.stringify(record.draft)), real_engine: true, human_approval: false,
      automated_save: true, saved_sha256: hash(saved), composition, pages: exported.pages,
      structure, renderedPages, toc: evidence, saved_and_reopened: true, content_loss: exported.loss }, null, 2));
  });
}
