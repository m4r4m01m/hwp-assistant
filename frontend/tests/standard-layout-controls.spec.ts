import { test, expect, type Page } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { strFromU8, unzipSync } from "fflate";

// Real hosted rhwp and synthetic standard sample. No AI or document saves.
const pane = (page: Page) => page.locator(".standard-preview-pages");
const register = (page: Page) => page.locator(".standard-preview-toolbar").getByRole("button", { name: /^(새 표준 등록|변경사항 저장)$/ });
async function ready(page: Page) {
  await expect.poll(async () => await register(page).isEnabled().catch(() => false)
    || await page.locator('[role="alert"]:visible').count() > 0, { timeout: 90000 }).toBe(true);
  expect(await page.locator('[role="alert"]:visible').allTextContents()).toEqual([]);
  await expect(pane(page)).toHaveAttribute("aria-busy", "false");
  await expect(page.getByRole("img", { name: /^문서 표준 예시 \d+쪽$/ }).first()).toBeVisible();
}
async function group(page: Page, name: string) {
  if (!(await page.getByRole("complementary", { name: "문서 표준 설정", exact: true }).isVisible()))
    await page.getByRole("button", { name: "문서 표준 설정 열기", exact: true }).click();
  const section = page.locator(`.standard-setting-section[data-setting-group=${JSON.stringify(name)}]`);
  const category = page.locator(".standard-setting-category").filter({ has: section });
  if (await category.count() && await category.getAttribute("open") === null) await category.locator(":scope > summary").click();
  const toggle = section.locator(".standard-group-toggle");
  if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
}
async function changed(page: Page, action: () => Promise<unknown>, match: (settings: any) => boolean) {
  const next = page.waitForResponse(response => response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/visual-standards/sample-preview"
    && match(response.request().postDataJSON().settings));
  await action();
  const response = await next;
  expect(response.ok(), await response.text()).toBe(true);
  const receipt = await response.json();
  await ready(page);
  await expect(pane(page)).toHaveAttribute("data-settings-sha256", receipt.settings_sha256);
}
async function snapshot(page: Page) {
  const frame = await (await page.locator(".standard-renderer iframe").elementHandle())!.contentFrame();
  const bytes = Buffer.from(await frame!.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  const files = unzipSync(bytes);
  const content = await page.evaluate(({ section, header }) => {
    const parser = new DOMParser(), s = parser.parseFromString(section, "application/xml"), h = parser.parseFromString(header, "application/xml");
    const nodes = (root: Element | Document, tag: string) => Array.from(root.getElementsByTagNameNS("*", tag));
    const text = nodes(s, "t").map(node => node.textContent || "").join("");
    const body = nodes(s, "p").find(node => nodes(node, "t").map(t => t.textContent).join("").includes("이 문서는 서식 비교를 위한 고정 합성 업무 자료"))!;
    const para = nodes(h, "paraPr").find(node => node.getAttribute("id") === body.getAttribute("paraPrIDRef"))!;
    const char = nodes(h, "charPr").find(node => node.getAttribute("id") === nodes(body, "run")[0].getAttribute("charPrIDRef"))!;
    return { text, condense: para.getAttribute("condense"), latin: nodes(para, "breakSetting")[0].getAttribute("breakLatinWord"),
      korean: nodes(para, "breakSetting")[0].getAttribute("breakNonLatinWord"), tracking: nodes(char, "spacing")[0].getAttribute("hangul") };
  }, { section: strFromU8(files["Contents/section0.xml"]), header: strFromU8(files["Contents/header.xml"]) });
  return { bytes, content };
}

test("REAL rhwp standard layout: word wrapping, space compression and tracking preserve preview through numeric edits", async ({ page, request }, info) => {
  test.setTimeout(240000);
  const errors: string[] = [], failures: { path: string; status: number; text: string }[] = [];
  const inputs: any[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", value => {
    if (value.method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/sample-preview") inputs.push(value.postDataJSON().settings);
  });
  page.on("response", async response => {
    const path = new URL(response.url()).pathname;
    if (path.startsWith("/api/visual-standards/") && response.status() >= 400)
      failures.push({ path, status: response.status(), text: await response.text().catch(() => "unreadable") });
  });
  try {
    await page.goto("/");
    await page.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
    await ready(page);
    const catalog = await (await request.get("/api/visual-standards")).json();
    const field = (key: string) => catalog.fields.find((item: any) => item.key === key);
    const control = (key: string) => page.getByLabel(`${field(key).group} ${field(key).label}`, { exact: true });
    await group(page, field("line_break_unit").group);
    const original = await snapshot(page);
    await changed(page, () => control("line_break_unit").selectOption("WORD"), s => s.line_break_unit === "WORD");
    await expect(control("line_break_unit")).toHaveValue("WORD");
    await expect(page.locator("#standard-help-line_break_unit")).toContainText("한글 어절·영문 단어를 가급적 같은 줄");
    await expect(page.locator("#standard-help-line_break_unit")).toContainText("남은 폭에 들어가지 않으면 다음 줄");
    await expect(page.locator("#standard-help-line_break_unit")).not.toContainText("기준서");
    await changed(page, () => control("line_space_policy").selectOption("TIGHTER"), s => s.line_space_policy === "TIGHTER");
    await expect(control("line_space_policy").locator("option:checked")).toContainText("20%");
    await expect(page.locator("#standard-help-line_space_policy")).toContainText("최대 20%");
    await expect(page.locator("#standard-help-line_space_policy")).not.toContainText("10%");
    await changed(page, () => control("body_tracking_percent").fill("-20"), s => s.body_tracking_percent === -20);
    const compressed = await snapshot(page);
    expect(compressed.content).toEqual({ ...original.content, condense: "20", latin: "KEEP_WORD", korean: "BREAK_WORD", tracking: "-20" });
    await writeFile(info.outputPath("word-space20-tracking-minus20.hwpx"), compressed.bytes);
    await page.screenshot({ path: info.outputPath("word-space20-tracking-minus20.png"), fullPage: true });
    await changed(page, () => control("body_tracking_percent").fill("20"), s => s.body_tracking_percent === 20);
    const previousSha = await pane(page).getAttribute("data-preview-sha256");
    const previousImages = await pane(page).locator("img").evaluateAll(nodes => nodes.map(node => (node as HTMLImageElement).src));
    const count = inputs.length;
    await control("body_tracking_percent").fill("");
    await control("body_tracking_percent").press("-");
    await page.waitForTimeout(600); // Past the real 400 ms preview debounce.
    expect(inputs).toHaveLength(count);
    await expect(pane(page)).toHaveAttribute("data-preview-sha256", previousSha!);
    expect(await pane(page).locator("img").evaluateAll(nodes => nodes.map(node => (node as HTMLImageElement).src))).toEqual(previousImages);
    expect(await pane(page).locator("img").evaluateAll(nodes => nodes.every(node => (node as HTMLImageElement).complete && (node as HTMLImageElement).naturalWidth > 0))).toBe(true);
    await page.screenshot({ path: info.outputPath("interim-minus-preserves-preview.png"), fullPage: true });
    await changed(page, async () => { await control("body_tracking_percent").press("2"); await control("body_tracking_percent").press("0"); await control("body_tracking_percent").blur(); }, s => s.body_tracking_percent === -20);
    expect((await snapshot(page)).content).toEqual(compressed.content);
    expect(errors).toEqual([]); expect(failures).toEqual([]);
  } finally {
    await writeFile(info.outputPath("layout-controls-observed.json"), JSON.stringify({ errors, failures, inputs }, null, 2));
    await page.screenshot({ path: info.outputPath("final-or-failure.png"), fullPage: true });
  }
});

test("REAL rhwp standard layout: later settings recover when typing overlaps a superseded preview load", async ({ page, request }, info) => {
  test.setTimeout(150000);
  await page.goto("/");
  await page.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
  await ready(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const field = (key: string) => catalog.fields.find((item: any) => item.key === key);
  const control = (key: string) => page.getByLabel(`${field(key).group} ${field(key).label}`, { exact: true });
  await group(page, field("line_break_unit").group);
  await changed(page, () => control("line_break_unit").selectOption("WORD"), s => s.line_break_unit === "WORD");
  await page.evaluate(async () => {
    // Vite may append an HMR timestamp to the module used by the application.
    // Intercept that exact loaded class, not a second module instance.
    const modulePath = performance.getEntriesByType("resource").map(entry => entry.name)
      .find(value => new URL(value).pathname === "/src/editor.ts");
    if (!modulePath) throw new Error("실제로 불러온 EditorAdapter 모듈이 없습니다.");
    const { EditorAdapter } = await import(/* @vite-ignore */ modulePath);
    const original = EditorAdapter.prototype.load;
    const gate: any = { armed: true, blocked: false, release: () => {}, restore: () => { EditorAdapter.prototype.load = original; } };
    (window as any).__standardLoadGate = gate;
    EditorAdapter.prototype.load = async function (...args: any[]) {
      const result = await original.apply(this, args);
      if (gate.armed) {
        gate.armed = false; gate.blocked = true;
        await new Promise<void>(resolve => { gate.release = resolve; });
      }
      return result;
    };
  });
  const observed: Record<string, unknown> = {};
  try {
    await control("line_space_policy").selectOption("TIGHTER");
    await control("body_tracking_percent").focus();
    await control("body_tracking_percent").press("Control+A");
    await expect.poll(() => page.evaluate(() => (window as any).__standardLoadGate.blocked), { timeout: 90000 }).toBe(true);
    observed.activeElement = await page.evaluate(() => {
      let doc = document; const path = [];
      for (let depth = 0; depth < 3 && doc.activeElement; depth++) {
        const active = doc.activeElement;
        path.push({ tag: active.tagName, label: active.getAttribute("aria-label"), className: active.className });
        if (!(active instanceof HTMLIFrameElement) || !active.contentDocument) break;
        doc = active.contentDocument;
      }
      return path;
    });
    await expect(control("body_tracking_percent")).toBeFocused();
    const frame = await (await page.locator(".standard-renderer iframe").elementHandle())!.contentFrame();
    observed.beforeTyping = await frame!.evaluate(() => (window as any).hwpAssistantBridge.documentState());
    expect((observed.beforeTyping as { dirty: boolean }).dirty).toBe(false);
    const next = page.waitForResponse(response => response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/visual-standards/sample-preview"
      && response.request().postDataJSON().settings.body_tracking_percent === -20);
    // Real keyboard input during the engine's temporary focus window.
    await page.keyboard.insertText("-20");
    await expect(control("body_tracking_percent")).toHaveValue("-20");
    await page.evaluate(() => (window as any).__standardLoadGate.release());
    expect((await next).ok()).toBe(true);
    await expect(pane(page)).toHaveAttribute("aria-busy", "false", { timeout: 15000 });
    await ready(page);
    expect((await snapshot(page)).content.tracking).toBe("-20");
    const latestFrame = await (await page.locator(".standard-renderer iframe").elementHandle())!.contentFrame();
    observed.afterTyping = await latestFrame!.evaluate(() => (window as any).hwpAssistantBridge.documentState());
    expect((observed.afterTyping as { dirty: boolean }).dirty).toBe(false);
  } finally {
    observed.status = await page.locator(".standard-preview-state").innerText();
    const frame = await (await page.locator(".standard-renderer iframe").elementHandle())!.contentFrame();
    observed.dialogs = await frame!.locator("dialog, [role=dialog], .modal, .dialog-body").allTextContents();
    await writeFile(info.outputPath("superseded-load-observed.json"), JSON.stringify(observed, null, 2));
    await page.screenshot({ path: info.outputPath("superseded-load-final.png"), fullPage: true });
    await page.evaluate(() => { const gate = (window as any).__standardLoadGate; gate.restore(); gate.release(); delete (window as any).__standardLoadGate; });
  }
});

async function currentSvgs(page: Page) {
  await page.getByRole("button", { name: "한 쪽 보기", exact: true }).click();
  const picker = page.getByLabel("미리보기 쪽", { exact: true }), result: string[] = [];
  for (let index = 1; index <= await picker.locator("option").count(); index++) {
    await picker.selectOption(String(index));
    result.push(await page.getByRole("img", { name: `문서 표준 예시 ${index}쪽`, exact: true })
      .evaluate(async (image: HTMLImageElement) => await (await fetch(image.src)).text()));
  }
  return result;
}

async function phraseLines(page: Page, svg: string, phrase: string, screenshot: string) {
  const laboratory = await page.context().newPage();
  try {
    await laboratory.setContent(`<html><body style="margin:0;background:white">${svg}</body></html>`);
    await laboratory.evaluate(() => document.fonts.ready);
    const result = await laboratory.evaluate(phrase => {
      const root = document.querySelector("svg")!, box = root.getBoundingClientRect();
      let position = 0;
      const glyphs = Array.from(root.querySelectorAll("text")).map(node => {
        const text = (node.textContent || "").replace(/\s/g, ""), start = position;
        position += text.length;
        const baseline = new DOMPoint(Number(node.getAttribute("x")), Number(node.getAttribute("y"))).matrixTransform(node.getCTM()!).y;
        return { text, start, end: position, baseline };
      });
      const all = glyphs.map(glyph => glyph.text).join(""), normalized = phrase.replace(/\s/g, ""), start = all.indexOf(normalized);
      if (start < 0) throw new Error("실제 렌더링에서 합성 예시 문장을 찾지 못했습니다.");
      const lines = new Map<number, string>();
      for (const glyph of glyphs.filter(glyph => glyph.end > start && glyph.start < start + normalized.length))
        lines.set(glyph.baseline, (lines.get(glyph.baseline) || "") + glyph.text);
      return { lines: [...lines.values()], width: Math.ceil(box.width), height: Math.ceil(box.height) };
    }, phrase);
    await laboratory.setViewportSize({ width: result.width, height: result.height });
    await laboratory.screenshot({ path: screenshot, fullPage: true });
    return result.lines;
  } finally { await laboratory.close(); }
}

test("REAL rhwp standard layout: stronger space compression preserves text and exports actual limits", async ({ page, request }, info) => {
  test.setTimeout(180000);
  await page.goto("/");
  await page.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
  await ready(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const field = (key: string) => catalog.fields.find((item: any) => item.key === key);
  const control = (key: string) => page.getByLabel(`${field(key).group} ${field(key).label}`, { exact: true });
  await group(page, field("line_break_unit").group);
  await changed(page, () => control("line_break_unit").selectOption("WORD"), s => s.line_break_unit === "WORD");
  const phrase = "예시 운영팀은 2026년 10월 30일까지 합성 점검 대상 20건의 확인을 완료할 계획임.";
  const unchangedPhrase = "문서관리체계와 업무인수인계절차를 함께 점검하여 자료의 누락 여부를 확인하고 담당자별 검토 결과를 기록함.";
  const proof: { policy: string; percent: number; lines: string[]; unchangedLines: string[]; pages: number }[] = [];
  let originalText = "";
  for (const [policy, percent] of [["NONE", 0], ["TIGHTER", 20], ["TIGHT_30", 30], ["TIGHT_50", 50]] as const) {
    if (percent) await changed(page, () => control("line_space_policy").selectOption(policy), s => s.line_space_policy === policy);
    else await expect(control("line_space_policy")).toHaveValue("NONE");
    const current = await snapshot(page), svgs = await currentSvgs(page);
    if (!originalText) originalText = current.content.text;
    expect(current.content.text).toBe(originalText);
    expect(current.content.condense).toBe(String(percent));
    expect(current.content.tracking).toBe("0");
    const indexOf = async (phrase: string) => await page.evaluate(({ svgs, phrase }) => svgs.findIndex(svg =>
      Array.from(new DOMParser().parseFromString(svg, "image/svg+xml").querySelectorAll("text"))
        .map(node => node.textContent || "").join("").replace(/\s/g, "").includes(phrase.replace(/\s/g, ""))), { svgs, phrase });
    const affectedIndex = await indexOf(phrase), unchangedIndex = await indexOf(unchangedPhrase);
    expect(affectedIndex).toBeGreaterThanOrEqual(0); expect(unchangedIndex).toBeGreaterThanOrEqual(0);
    const lines = await phraseLines(page, svgs[affectedIndex], phrase, info.outputPath(`compression-${percent}.png`));
    const unchangedLines = await phraseLines(page, svgs[unchangedIndex], unchangedPhrase, info.outputPath(`compression-${percent}-unchanged.png`));
    proof.push({ policy, percent, lines, unchangedLines, pages: svgs.length });
    await writeFile(info.outputPath(`compression-${percent}.hwpx`), current.bytes);
  }
  await writeFile(info.outputPath("stronger-compression-proof.json"), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    unchanged_text: true, phrase, unchangedPhrase, variants: proof }, null, 2));
  expect(proof[0].lines[0]).not.toContain("확인을");
  expect(proof[1].lines[0]).toContain("확인을");
  expect(proof[0].unchangedLines).toEqual(proof[1].unchangedLines);
});

test("REAL rhwp standard layout: equal settings are no-ops and saving resets comparison baseline", async ({ page, request }, info) => {
  test.setTimeout(150000);
  const posted: any[] = [];
  page.on("request", value => { if (value.method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/sample-preview") posted.push(value.postDataJSON()); });
  await page.goto("/");
  await page.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
  await ready(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const field = catalog.fields.find((item: any) => item.key === "line_space_policy");
  const control = page.getByLabel(`${field.group} ${field.label}`, { exact: true });
  await group(page, field.group);
  await expect(control).toHaveValue("NONE");
  await expect(control.locator('option[value="ORIGINAL"]')).toHaveCount(0);
  await expect(control.locator('option[value="NONE"]')).toContainText("✓ 기준");
  await expect(page.locator('.standard-field[data-changed="true"]')).toHaveCount(0);
  const count = posted.length, initialSha = await pane(page).getAttribute("data-preview-sha256");
  await control.selectOption("NONE"); await control.blur();
  await page.waitForTimeout(700); // Beyond preview debounce: alias-equivalent selection must not generate work.
  expect(posted).toHaveLength(count);
  await expect(pane(page)).toHaveAttribute("data-preview-sha256", initialSha!);
  await changed(page, () => control.selectOption("TIGHT_50"), s => s.line_space_policy === "TIGHT_50");
  await expect(page.locator('.standard-field[data-setting-key="line_space_policy"]')).toHaveAttribute("data-changed", "true");
  await control.selectOption("NONE");
  await expect(page.locator('.standard-field[data-changed="true"]')).toHaveCount(0);
  await expect(page.locator(".comparison-highlight rect")).toHaveCount(0);
  await expect(page.locator(".standard-compare-note")).toContainText("0개 설정 변경");
  await expect(pane(page)).toHaveAttribute("aria-busy", "false");
  await changed(page, () => control.selectOption("TIGHTER"), s => s.line_space_policy === "TIGHTER");
  await register(page).click();
  const name = `공백 압축 비교 기준 ${Date.now()}`;
  await page.getByLabel("표준 이름", { exact: true }).fill(name);
  const registration = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/visual-standards/sample-register");
  await page.getByRole("dialog", { name: "표준 저장", exact: true }).getByRole("button", { name: "새 표준 등록", exact: true }).click();
  const response = await registration; expect(response.ok(), await response.text()).toBe(true);
  const saved = await response.json();
  await expect(page.getByRole("heading", { name: `‘${name}’ 등록 완료`, exact: true })).toBeVisible();
  await expect(page.locator('.standard-field[data-changed="true"]')).toHaveCount(0);
  await expect(page.locator(".comparison-highlight rect")).toHaveCount(0);
  await expect(page.locator(".standard-compare-note")).toContainText("0개 설정 변경");
  await expect(control.locator('option[value="TIGHTER"]')).toContainText("✓ 기준");
  const afterSaveCount = posted.length;
  await control.selectOption("TIGHTER"); await control.blur(); await page.waitForTimeout(700);
  expect(posted).toHaveLength(afterSaveCount);
  await changed(page, () => control.selectOption("NONE"), s => s.line_space_policy === "NONE");
  await control.selectOption("TIGHTER"); // A matching saved receipt can safely restore the verified baseline.
  await expect(page.locator('.standard-field[data-changed="true"]')).toHaveCount(0);
  await expect(page.locator(".comparison-highlight rect")).toHaveCount(0);
  await expect(page.locator(".standard-compare-note")).toContainText("0개 설정 변경");
  await expect(pane(page)).toHaveAttribute("aria-busy", "false");
  await page.screenshot({ path: info.outputPath("saved-comparison-baseline.png"), fullPage: true });
  await writeFile(info.outputPath("baseline-noop-proof.json"), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    equivalent_selection_requests: 0, saved_equivalent_selection_requests: 0, restored_change_count: 0, saved_id: saved.id,
    saved_settings: saved.settings, posted_settings: posted.map(item => item.settings) }, null, 2));
});
