import { test, expect, type Locator, type Page, type Route } from "@playwright/test";
import { createHash } from "node:crypto";

// UI state regression with synthetic HTTP responses and a mocked renderer.
// This verifies comparison selection/baseline ownership, not native HWPX layout.
const hash = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");
const respond = (route: Route, value: unknown, status = 200) => route.fulfill({
  status, contentType: "application/json", body: JSON.stringify(value),
});
type Settings = { square_size_pt: number };
const base = { id: "synthetic-base", reference_sha256: "a".repeat(64), profile_sha256: "b".repeat(64) };
const previewFor = (settings: Settings) => {
  const bytes = Buffer.from(JSON.stringify(settings));
  return { settings_sha256: hash(JSON.stringify(settings)), preview_sha256: hash(bytes),
    preview_base64: bytes.toString("base64"), base_standard: base, max_pages: 10,
    context: JSON.stringify(settings), phase: "ready", sample_id: "synthetic-sample", sample_sha256: "f".repeat(64),
    sample_regions: [{ role: "square", texts: ["합성 비교 문장"] }], needs_toc: false,
    expires_at: Date.now() / 1000 + 600, page_count: 1 };
};

async function imageSvg(image: Locator) {
  return image.evaluate(async node => (await fetch((node as HTMLImageElement).src)).text());
}

async function mountDesigner(page: Page) {
  const previews: Settings[] = [];
  const registered: Array<{ id: string; revision: number; name: string; settings: Settings; settings_sha256: string; preview_sha256: string; base_standard: typeof base }> = [];
  await page.route("**/standard-compare-test.html*", route => route.fulfill({
    contentType: "text/html", body: '<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/catalog-lifecycle.tsx"></script></body></html>',
  }));
  await page.route("**/src/editor.ts*", route => route.fulfill({ contentType: "text/javascript", body: `
    const lifecycle = window.__standardEngineTest = { mounts: 0, destroyed: [], loads: [], holdNext: false, held: null };
    export class EditorAdapter {
      static async mount(host) { const adapter = new EditorAdapter(); host.appendChild(adapter.frame); return adapter; }
      id = ++lifecycle.mounts;
      frame = document.createElement("iframe");
      bytes = new Uint8Array([1, 2, 3]);
      size = 14;
      bridge = { newDocument: async () => {} };
      sdk = {
        element: this.frame,
        pageCount: async () => 1,
        getPageSvg: async () => '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="1100" viewBox="0 0 800 1100"><text x="20" y="40" font-size="' + this.size + '">합성 비교 문장</text></svg>',
      };
      exportHwpx() { return { bytes: this.bytes, contentLoss: { count: 0 } }; }
      async load(buffer, name, options) {
        const bytes = new Uint8Array(buffer), size = JSON.parse(new TextDecoder().decode(bytes)).square_size_pt;
        lifecycle.loads.push({ id: this.id, size, options });
        if (lifecycle.holdNext) {
          lifecycle.holdNext = false;
          await new Promise(resolve => { lifecycle.held = { id: this.id, release: resolve, completed: false }; });
          lifecycle.held.completed = true;
        }
        this.bytes = bytes; this.size = size;
      }
      destroy() { lifecycle.destroyed.push(this.id); this.frame.remove(); }
    }
  ` }));
  await page.route("**/src/report-toc.ts*", route => route.fulfill({ contentType: "text/javascript", body: `
    export const captureReport = engine => ({ source: engine.exportHwpx() });
    export const fenceReport = () => {};
  ` }));
  await page.route("**/src/standard-preview-fonts.ts*", route => route.fulfill({ contentType: "text/javascript", body: `
    export const embedStandardPreviewFonts = async value => value;
    export const standardPreviewFontNames = [];
  ` }));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    if (path === "/visual-standards" && request.method() === "GET") return respond(route, {
      defaults: { square_size_pt: 14 },
      fields: [{ key: "square_size_pt", group: "문단 내용 · □ 항목", label: "글자 크기", type: "number", min: 8, max: 30, step: 1, unit: "pt" }],
      items: registered,
      presets: [{ id: "synthetic-default", revision: 3, name: "기본 합성 표준", settings: { square_size_pt: 14 }, description: "합성 시험용" }],
    });
    if (path === "/design-fragments") return respond(route, { items: [] });
    if (path === "/visual-standards/sample-preview") {
      const settings = request.postDataJSON().settings as Settings;
      previews.push(settings);
      return respond(route, previewFor(settings));
    }
    if (path === "/visual-standards/sample-register") {
      const body = request.postDataJSON(), settings = JSON.parse(body.context) as Settings;
      const receipt = previewFor(settings);
      const result = { id: body.id, revision: 3, name: body.name, settings,
        settings_sha256: receipt.settings_sha256, preview_sha256: receipt.preview_sha256, base_standard: base };
      registered.push(result);
      return respond(route, result);
    }
    return respond(route, { detail: `Unexpected request: ${path}` }, 404);
  });
  await page.goto("/standard-compare-test.html?screen=standard");
  await expect(page.locator(".standard-preview-pages")).toHaveAttribute("aria-busy", "false");
  return { previews, registered };
}

test("MOCK standards: saving resets comparison, restored values clear marks, and save options stay usable", async ({ page }, info) => {
  const { previews, registered } = await mountDesigner(page);
  const compare = page.getByRole("button", { name: "변경 전·후 비교", exact: true });
  const single = page.getByRole("button", { name: "한 쪽 보기", exact: true });
  const before = page.getByRole("img", { name: "변경 전 1쪽", exact: true });
  const after = page.getByRole("img", { name: "문서 표준 예시 1쪽", exact: true });
  const pane = page.locator(".standard-preview-pages");

  await expect(compare).toHaveAttribute("aria-pressed", "true");
  await expect(single).toHaveAttribute("aria-pressed", "false");
  await expect(compare).toHaveCSS("background-color", "rgb(237, 245, 253)");
  await compare.click();
  await expect(before).toBeVisible();
  await expect(compare).toHaveAttribute("aria-pressed", "true");

  await page.getByLabel("설정 찾기", { exact: true }).fill("글자 크기");
  const size = page.locator('[data-setting-key="square_size_pt"] input[type="number"]');
  await size.fill("18");
  await expect(pane).toHaveAttribute("data-settings-sha256", previewFor({ square_size_pt: 18 }).settings_sha256);
  await expect(pane).toHaveAttribute("aria-busy", "false");
  await page.getByRole("button", { name: "새 표준 등록", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "표준 저장", exact: true });
  await dialog.getByLabel("표준 이름", { exact: true }).fill("합성 비교 기준 18pt");
  await dialog.getByRole("button", { name: "새 표준 등록", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(registered).toHaveLength(1);
  await expect.poll(() => imageSvg(before)).toContain('font-size="18"');
  await expect.poll(() => imageSvg(after)).toContain('font-size="18"');
  await expect(page.locator('.standard-field[data-changed="true"]')).toHaveCount(0);
  await expect(page.locator(".comparison-highlight rect")).toHaveCount(0);
  await expect(page.locator(".standard-compare-note")).toContainText("0개 설정 변경");

  await page.getByLabel("저장한 표준", { exact: true }).selectOption(registered[0].id);
  await page.getByRole("button", { name: "선택한 표준 편집", exact: true }).click();
  await expect(pane).toHaveAttribute("aria-busy", "false");
  await expect(compare).toBeEnabled();
  await compare.click();
  await compare.click();
  await expect(compare).toHaveAttribute("aria-pressed", "true");
  await expect(single).toHaveAttribute("aria-pressed", "false");
  await expect(before).toBeVisible();
  await expect.poll(() => imageSvg(before)).toContain('font-size="18"');

  await size.fill("22");
  await expect(pane).toHaveAttribute("data-settings-sha256", previewFor({ square_size_pt: 22 }).settings_sha256);
  await expect(pane).toHaveAttribute("aria-busy", "false");
  await expect.poll(() => imageSvg(before)).toContain('font-size="18"');
  await expect.poll(() => imageSvg(after)).toContain('font-size="22"');
  await expect(page.locator('.standard-field[data-changed="true"]')).toHaveCount(1);
  await expect(page.locator(".comparison-highlight rect").first()).toBeVisible();
  const unchangedCount = previews.length;
  await size.fill("22.0");
  // The renderer starts after a 400 ms debounce. Equal numeric meaning must
  // not enqueue another HTTP preview or discard the current verified image.
  await page.waitForTimeout(650);
  expect(previews).toHaveLength(unchangedCount);
  await expect(pane).toHaveAttribute("aria-busy", "false");
  await size.fill("18");
  await expect(pane).toHaveAttribute("data-settings-sha256", previewFor({ square_size_pt: 18 }).settings_sha256);
  await expect(pane).toHaveAttribute("aria-busy", "false");
  await expect(page.locator('.standard-field[data-changed="true"]')).toHaveCount(0);
  await expect(page.locator(".comparison-highlight rect")).toHaveCount(0);
  await expect(page.locator(".standard-compare-note")).toContainText("0개 설정 변경");
  await size.fill("22");
  await expect(pane).toHaveAttribute("data-settings-sha256", previewFor({ square_size_pt: 22 }).settings_sha256);
  await expect(pane).toHaveAttribute("aria-busy", "false");
  const previewCount = previews.length;
  await single.click();
  await expect(single).toHaveAttribute("aria-pressed", "true");
  await expect(compare).toHaveAttribute("aria-pressed", "false");
  await expect(before).toHaveCount(0);
  await compare.click();
  await expect(before).toBeVisible();
  await expect(compare).toHaveAttribute("aria-pressed", "true");
  await expect(single).toHaveAttribute("aria-pressed", "false");
  expect(previews).toHaveLength(previewCount);
  await expect.poll(() => imageSvg(before)).toContain('font-size="18"');

  await page.getByRole("button", { name: "변경사항 저장", exact: true }).click();
  const overwrite = dialog.getByRole("radio", { name: "현재 표준 덮어쓰기", exact: true });
  const add = dialog.getByRole("radio", { name: "새 표준으로 추가", exact: true });
  await expect(overwrite).toBeChecked();
  for (const radio of [overwrite, add]) {
    const bounds = await radio.evaluate(input => {
      const label = input.closest("label")!, rect = input.getBoundingClientRect();
      const text = Array.from(label.childNodes).find(node => node.nodeType === Node.TEXT_NODE && node.textContent?.trim())!;
      const range = document.createRange(); range.selectNode(text);
      const caption = range.getBoundingClientRect();
      return { width: rect.width, height: rect.height, gap: caption.left - rect.right,
        centerDifference: Math.abs((caption.top + caption.bottom - rect.top - rect.bottom) / 2) };
    });
    expect(bounds.width).toBeGreaterThanOrEqual(12);
    expect(bounds.width).toBeLessThanOrEqual(24);
    expect(bounds.height).toBeLessThanOrEqual(24);
    expect(bounds.gap).toBeGreaterThanOrEqual(4);
    expect(bounds.centerDifference).toBeLessThan(3);
  }
  await add.check();
  await expect(overwrite).not.toBeChecked();
  await expect(dialog.getByLabel("표준 이름", { exact: true })).toHaveValue("합성 비교 기준 18pt 사본");
  await expect(dialog.getByRole("button", { name: "새 표준 등록", exact: true })).toBeEnabled();
  await overwrite.check();
  await expect(add).not.toBeChecked();
  await expect(dialog.getByLabel("표준 이름", { exact: true })).toHaveValue("합성 비교 기준 18pt");
  await expect(dialog.getByRole("button", { name: "현재 표준 덮어쓰기", exact: true })).toBeEnabled();
  await dialog.screenshot({ path: info.outputPath("standard-save-method.png") });
});

type EngineLifecycle = {
  mounts: number;
  destroyed: number[];
  loads: Array<{ id: number; size: number; options: { skipUnsavedGuard?: boolean; suppressDialogs?: boolean } }>;
  holdNext: boolean;
  held: { id: number; release: () => void; completed: boolean } | null;
};
type ImageGate = { entered: boolean; completed: boolean; release?: () => void };
type TestWindow = Window & { __standardEngineTest: EngineLifecycle; __standardImageGate: ImageGate };

async function editableSize(page: Page) {
  await page.getByLabel("설정 찾기", { exact: true }).fill("글자 크기");
  return page.locator('[data-setting-key="square_size_pt"] input[type="number"]');
}

test("MOCK standards: a superseded image decode cannot block or replace the latest preview", async ({ page }) => {
  await mountDesigner(page);
  const size = await editableSize(page), pane = page.locator(".standard-preview-pages");
  const after = page.getByRole("img", { name: "문서 표준 예시 1쪽", exact: true });
  const verifiedImage = await after.getAttribute("src");
  await page.evaluate(() => {
    const original = Image.prototype.decode;
    const gate = (window as TestWindow).__standardImageGate = { entered: false, completed: false } as ImageGate;
    Image.prototype.decode = function () {
      if (gate.entered) return original.call(this);
      gate.entered = true;
      const decoded = original.call(this);
      return new Promise<void>((resolve, reject) => {
        decoded.then(() => { gate.release = () => { gate.completed = true; resolve(); }; }, reject);
      });
    };
  });
  await size.fill("18");
  await expect.poll(() => page.evaluate(() => !!(window as TestWindow).__standardImageGate.release)).toBe(true);
  await expect(pane).toHaveAttribute("aria-busy", "true");
  await expect(after).toHaveAttribute("src", verifiedImage!);
  await expect.poll(() => imageSvg(after)).toContain('font-size="14"');

  await size.fill("22");
  await expect(pane).toHaveAttribute("data-settings-sha256", previewFor({ square_size_pt: 22 }).settings_sha256);
  await expect(pane).toHaveAttribute("aria-busy", "false");
  await expect(size).toHaveValue("22");
  await expect.poll(() => imageSvg(after)).toContain('font-size="22"');
  const latestImage = await after.getAttribute("src");
  await page.evaluate(async () => {
    (window as TestWindow).__standardImageGate.release!();
    await new Promise(resolve => setTimeout(resolve, 0));
  });
  await expect(after).toHaveAttribute("src", latestImage!);
  await expect(pane).toHaveAttribute("data-settings-sha256", previewFor({ square_size_pt: 22 }).settings_sha256);
  await expect(pane).toHaveAttribute("aria-busy", "false");
  await expect(size).toHaveValue("22");
});

test("MOCK standards: a superseded native load is disposed and its late reply cannot overwrite current settings", async ({ page }) => {
  await mountDesigner(page);
  const size = await editableSize(page), pane = page.locator(".standard-preview-pages");
  const after = page.getByRole("img", { name: "문서 표준 예시 1쪽", exact: true });
  const verifiedImage = await after.getAttribute("src");
  const initialMounts = await page.evaluate(() => (window as TestWindow).__standardEngineTest.mounts);
  await page.evaluate(() => { (window as TestWindow).__standardEngineTest.holdNext = true; });
  await size.fill("18");
  await expect.poll(() => page.evaluate(() => !!(window as TestWindow).__standardEngineTest.held)).toBe(true);
  await expect(pane).toHaveAttribute("aria-busy", "true");
  await expect(after).toHaveAttribute("src", verifiedImage!);
  await expect.poll(() => imageSvg(after)).toContain('font-size="14"');

  await size.fill("22");
  await expect(pane).toHaveAttribute("data-settings-sha256", previewFor({ square_size_pt: 22 }).settings_sha256);
  await expect(pane).toHaveAttribute("aria-busy", "false");
  await expect(size).toHaveValue("22");
  await expect.poll(() => imageSvg(after)).toContain('font-size="22"');
  const lifecycle = await page.evaluate(() => {
    const state = (window as TestWindow).__standardEngineTest;
    return { mounts: state.mounts, destroyed: state.destroyed, heldId: state.held!.id, loads: state.loads };
  });
  expect(lifecycle.mounts).toBeGreaterThan(initialMounts);
  expect(lifecycle.destroyed).toContain(lifecycle.heldId);
  const newestLoad = lifecycle.loads.findLast(load => load.size === 22)!;
  expect(newestLoad.id).not.toBe(lifecycle.heldId);
  expect(newestLoad.options).toMatchObject({ skipUnsavedGuard: true, suppressDialogs: true });
  const latestImage = await after.getAttribute("src");
  await page.evaluate(async () => {
    (window as TestWindow).__standardEngineTest.held!.release();
    await new Promise(resolve => setTimeout(resolve, 0));
  });
  await expect.poll(() => page.evaluate(() => (window as TestWindow).__standardEngineTest.held!.completed)).toBe(true);
  await expect(after).toHaveAttribute("src", latestImage!);
  await expect(pane).toHaveAttribute("data-settings-sha256", previewFor({ square_size_pt: 22 }).settings_sha256);
  await expect(pane).toHaveAttribute("aria-busy", "false");
  await expect(size).toHaveValue("22");
});
