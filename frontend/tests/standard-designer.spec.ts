import { test, expect, type Page } from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const registerButton = (page: Page) => page.getByRole("button", { name: "표준 템플릿으로 등록", exact: true });
async function setCatalogField(page: Page, catalog: any, key: string, value: string | number | boolean) {
  const field = catalog.fields.find((item: any) => item.key === key);
  expect(field, `catalog field ${key}`).toBeTruthy();
  await openGroup(page, field.group);
  const control = page.getByLabel(`${field.group} ${field.label}`, { exact: true });
  if (field.type === "boolean") await control.setChecked(value as boolean);
  else if (field.type === "select") {
    await control.focus();
    await control.selectOption(String(value));
  }
  else await control.fill(String(value));
}
async function openDesigner(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
  await expect(page.getByRole("heading", { name: "문서 표준 편집", exact: true })).toBeVisible();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await expect(page.getByRole("img", { name: "문서 표준 예시 1쪽", exact: true })).toBeVisible();
}
async function openGroup(page: Page, group: string) {
  const section = page.locator(".standard-setting-group").filter({ has: page.locator("summary", { hasText: new RegExp(`^${group}$`) }) });
  if (!(await section.getAttribute("open")) && await section.getAttribute("open") !== "")
    await section.locator("summary").click();
}
async function previewImageHashes(page: Page) {
  const images = await page.locator(".standard-preview-pages figure img").all();
  const hashes: string[] = [];
  for (const image of images) hashes.push(hash(await image.screenshot()));
  return hashes;
}
async function expectOuterViewportContained(page: Page) {
  const bounds = await page.evaluate(() => ({
    height: window.innerHeight,
    scrollHeight: document.scrollingElement!.scrollHeight,
    scrollY: window.scrollY,
  }));
  expect(bounds.scrollHeight).toBeLessThanOrEqual(bounds.height + 1);
  expect(bounds.scrollY).toBe(0);
}
async function expectDesignerScrollContained(page: Page) {
  const previewBounds = (await page.locator(".standard-preview").boundingBox())!;
  expect(previewBounds.y).toBe(0);
  expect(previewBounds.height).toBe(page.viewportSize()!.height);
  await expectOuterViewportContained(page);
  for (const selector of [".standard-preview-pages", ".standard-settings"]) {
    const pane = page.locator(selector);
    const openedSettings = selector === ".standard-settings" && !(await pane.isVisible());
    if (openedSettings)
      await page.getByRole("button", { name: "문서 표준 설정 열기", exact: true }).click();
    await expect(pane).toBeVisible();
    const scroll = await pane.evaluate(element => {
      element.scrollTop = element.scrollHeight;
      return { top: element.scrollTop, maximum: element.scrollHeight - element.clientHeight };
    });
    expect(scroll.maximum).toBeGreaterThan(0);
    expect(scroll.top).toBeGreaterThan(0);
    expect(Math.abs(scroll.maximum - scroll.top)).toBeLessThanOrEqual(1);
    await expectOuterViewportContained(page);
    await pane.evaluate(element => { element.scrollTop = 0; });
    if (openedSettings)
      await page.getByRole("button", { name: "문서 표준 설정 닫기", exact: true }).click();
  }
}

test("REAL rhwp standard designer: settings change the sample and immutable registration is selectable in new reports", async ({ page, request }, info) => {
  await page.setViewportSize({ width: 1240, height: 900 });
  // Keep the real object URL lifecycle and CSP. Retain only the SVG Blob
  // references so this test can inspect the engine's image without fetching a
  // blob: URL through connect-src.
  await page.addInitScript(() => {
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    const blobs = new Map<string, Blob>();
    (window as any).__standardTestBlobs = blobs;
    URL.createObjectURL = object => {
      const url = create(object);
      if (object instanceof Blob && object.type === "image/svg+xml") blobs.set(url, object);
      return url;
    };
    URL.revokeObjectURL = url => { revoke(url); blobs.delete(url); };
  });
  const original = await (await request.get("/api/document-standard")).json();
  const catalog = await (await request.get("/api/visual-standards")).json();
  const modelPosts: string[] = [];
  page.on("request", value => { if (value.method() === "POST" && new URL(value.url()).pathname === "/api/report-tasks") modelPosts.push(value.url()); });
  await openDesigner(page);
  const initial = await previewImageHashes(page);
  await page.getByLabel("본문 글자 크기", { exact: true }).fill("16");
  await expect(registerButton(page)).toBeDisabled();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await expect(page.getByLabel("본문 글자 크기", { exact: true })).toBeFocused();
  const nextPreview = page.waitForRequest(request => request.method() === "POST" && new URL(request.url()).pathname === "/api/visual-standards/preview");
  await page.getByLabel("본문 줄 간격", { exact: true }).fill("180");
  await nextPreview;
  await page.getByLabel("절 제목 글자 크기", { exact: true }).focus();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await expect(page.getByLabel("절 제목 글자 크기", { exact: true })).toBeFocused();
  await page.getByRole("button", { name: "기본값으로 되돌리기", exact: true }).click();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await expect(page.getByLabel("본문 줄 간격", { exact: true })).not.toBeFocused();
  await expect(page.getByLabel("절 제목 글자 크기", { exact: true })).not.toBeFocused();
  await page.getByLabel("본문 글자 크기", { exact: true }).fill("16");
  await page.getByLabel("본문 줄 간격", { exact: true }).fill("180");
  await page.getByLabel("절 제목 글자 크기", { exact: true }).fill("18");
  await openGroup(page, "표");
  await page.getByLabel("표 머리글 채움색", { exact: true }).fill("#DDEFE5");
  await setCatalogField(page, catalog, "table_horizontal_align", "RIGHT");
  await setCatalogField(page, catalog, "table_vertical_align", "CENTER");
  await setCatalogField(page, catalog, "table_border_style", "DASH");
  await setCatalogField(page, catalog, "table_border_width_mm", 0.12);
  const borderWidthField = catalog.fields.find((field: any) => field.key === "table_border_width_mm");
  await expect(page.getByRole("combobox", { name: `${borderWidthField.group} ${borderWidthField.label}`, exact: true })).toHaveValue("0.12");
  await setCatalogField(page, catalog, "table_border_scope", "NONE");
  const borderStyleField = catalog.fields.find((field: any) => field.key === "table_border_style");
  await expect(page.getByLabel(`${borderStyleField.group} ${borderStyleField.label}`, { exact: true })).toBeDisabled();
  await setCatalogField(page, catalog, "table_border_scope", "OUTER");
  await expect(page.getByLabel(`${borderStyleField.group} ${borderStyleField.label}`, { exact: true })).toBeEnabled();
  await expect(page.getByLabel(`${borderStyleField.group} ${borderStyleField.label}`, { exact: true })).toHaveValue("DASH");
  await setCatalogField(page, catalog, "table_cell_padding_mm", 2.5);
  await setCatalogField(page, catalog, "title_vertical_offset_pt", 8);
  await setCatalogField(page, catalog, "body_parenthetical_labels", false);
  await setCatalogField(page, catalog, "show_core_summary", false);
  await setCatalogField(page, catalog, "page_number_style", "NONE");
  const positionField = catalog.fields.find((field: any) => field.key === "page_number_position");
  await expect(page.getByLabel(`${positionField.group} ${positionField.label}`, { exact: true })).toBeDisabled();
  await setCatalogField(page, catalog, "page_number_style", "ROMAN");
  await setCatalogField(page, catalog, "page_number_position", "BOTTOM_RIGHT");
  await setCatalogField(page, catalog, "citation_style", "NOTE");
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  const citationField = catalog.fields.find((field: any) => field.key === "citation_style");
  await expect(page.getByLabel(`${citationField.group} ${citationField.label}`, { exact: true })).toBeFocused();
  const changed = await previewImageHashes(page);
  expect(changed).not.toEqual(initial);
  expect(await page.locator(".standard-preview-pages figure img").count()).toBeGreaterThan(0);
  expect(await page.locator(".standard-preview-pages figure img").count()).toBeLessThanOrEqual(6);
  // Check rendered glyphs: native footer PAGE fields can retain ROMAN metadata
  // while the engine still draws Arabic digits. XML alone misses that failure.
  const pageNumbers = await page.locator(".standard-preview-pages figure img").evaluateAll(async images => Promise.all(images.map(async (image, index) => {
    const blob = ((window as any).__standardTestBlobs as Map<string, Blob>).get((image as HTMLImageElement).src);
    if (!blob) throw new Error(`예시 ${index + 1}쪽의 실제 SVG Blob을 확인할 수 없습니다.`);
    const document = new DOMParser().parseFromString(await blob.text(), "image/svg+xml");
    if (document.querySelector("parsererror")) throw new Error(`예시 ${index + 1}쪽 SVG가 올바르지 않습니다.`);
    const height = Number(document.documentElement.getAttribute("viewBox")?.trim().split(/\s+/)[3]);
    if (!Number.isFinite(height) || height <= 0) throw new Error(`예시 ${index + 1}쪽 SVG의 높이를 확인할 수 없습니다.`);
    const footer = Array.from(document.getElementsByTagNameNS("*", "text"))
      .filter(text => Number(text.getAttribute("y")) > height - 90)
      .map(text => ({ y: Number(text.getAttribute("y")), text: text.textContent || "" }));
    return { page: index + 1, height, footer, text: footer.map(item => item.text).join("").replace(/\s+/g, "") };
  })));
  expect(pageNumbers.length).toBeGreaterThan(0);
  expect(pageNumbers[0].text).toBe("-I-");
  if (pageNumbers.length > 1) expect(pageNumbers[1].text).toBe("-II-");
  await writeFile(info.outputPath("standard-designer-roman-page-numbers.json"), JSON.stringify({
    actual_engine_svg: true, actual_model_calls: 0, style: "ROMAN", page_count: pageNumbers.length, pages: pageNumbers,
  }, null, 2));
  await expectDesignerScrollContained(page);
  await page.screenshot({ path: info.outputPath("standard-designer-custom.png"), fullPage: true });
  await registerButton(page).click();
  const name = `문서 표준 검증 ${Date.now()}`;
  await page.getByLabel("표준 이름", { exact: true }).fill(name);
  const registered = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/visual-standards");
  await page.getByRole("button", { name: "새 표준 등록", exact: true }).click();
  const response = await registered;
  expect(response.ok()).toBeTruthy();
  const saved = await response.json();
  expect(saved.name).toBe(name);
  expect(saved.settings.body_size_pt).toBe(16);
  expect(saved.settings.body_line_percent).toBe(180);
  expect(saved.settings.table_header_fill).toBe("#DDEFE5");
  expect(saved.revision).toBe(2);
  expect(saved.settings).toMatchObject({ table_horizontal_align: "RIGHT", table_vertical_align: "CENTER",
    table_border_style: "DASH", table_border_width_mm: 0.12, table_border_scope: "OUTER", table_cell_padding_mm: 2.5,
    title_vertical_offset_pt: 8, body_parenthetical_labels: false, show_core_summary: false,
    page_number_style: "ROMAN", page_number_position: "BOTTOM_RIGHT", citation_style: "NOTE" });
  await expect(page.getByRole("heading", { name: `‘${name}’ 등록 완료`, exact: true })).toBeVisible();
  await expect(page.getByLabel("설정 불러오기", { exact: true })).toHaveValue(saved.id);
  const file = await (await request.get(`/api/visual-standards/${saved.id}/file`)).body();
  expect(hash(file)).toBe(saved.preview_sha256);
  const zip = unzipSync(file);
  const header = strFromU8(zip["Contents/header.xml"]);
  expect(header).toContain('faceColor="#DDEFE5"');
  expect(header).toContain('height="1600"');
  const section = strFromU8(zip["Contents/section0.xml"]);
  expect(section).not.toContain("(핵심 항목)");
  expect(section).not.toContain("핵심 항목");
  expect(section).toContain("하나의 논점은 한 항목으로 작성하고");
  expect(section).not.toContain(">핵심 요약<");
  expect(section).toContain("주1)");
  expect((await (await request.get("/api/visual-standards")).json()).defaults).toEqual(catalog.defaults);
  expect(await (await request.get("/api/document-standard")).json()).toEqual(original);
  await page.getByRole("button", { name: "이 표준으로 보고서 작성", exact: true }).click();
  await expect(page.getByLabel("문서 서식 표준", { exact: true })).toHaveValue(saved.id);
  await expect(page.getByLabel("문서 서식 표준", { exact: true })).toContainText(name);
  expect(modelPosts).toHaveLength(0);
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  await page.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
  await page.getByLabel("설정 불러오기", { exact: true }).selectOption(saved.id);
  await expect(page.getByLabel("본문 글자 크기", { exact: true })).toHaveValue("16");
  const labelField = catalog.fields.find((field: any) => field.key === "body_parenthetical_labels");
  await openGroup(page, labelField.group);
  await expect(page.getByLabel(`${labelField.group} ${labelField.label}`, { exact: true })).not.toBeChecked();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await page.getByRole("button", { name: "기본값으로 되돌리기", exact: true }).click();
  await expect(page.getByLabel("본문 글자 크기", { exact: true })).toHaveValue(String(catalog.defaults.body_size_pt));
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await page.getByLabel("설정 찾기", { exact: true }).fill("쪽번호");
  await expect(page.locator(".standard-setting-group")).toHaveCount(1);
  await expect(page.getByLabel("본문 글자 크기", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "설정 검색 지우기", exact: true }).click();
  await expect(page.getByLabel("본문 글자 크기", { exact: true })).toHaveCount(1);
  await page.setViewportSize({ width: 400, height: 760 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
  await expectDesignerScrollContained(page);
  await page.screenshot({ path: info.outputPath("standard-designer-mobile.png"), fullPage: true });
});

test("REAL rhwp standard designer: stale preview cannot register and invalid values keep the last sample", async ({ page }) => {
  await openDesigner(page);
  let firstStarted!: () => void;
  const started = new Promise<void>(resolve => { firstStarted = resolve; });
  let releaseFirst!: () => void;
  const release = new Promise<void>(resolve => { releaseFirst = resolve; });
  let delayed = false;
  await page.route("**/api/visual-standards/preview", async route => {
    const input = route.request().postDataJSON();
    if (!delayed && input.settings.body_size_pt === 17) {
      delayed = true;
      const response = await route.fetch();
      firstStarted();
      await release;
      await route.fulfill({ response }).catch(() => {});
    } else await route.continue();
  });
  await page.getByLabel("본문 글자 크기", { exact: true }).fill("17");
  await started;
  await expect(registerButton(page)).toBeDisabled();
  await page.getByLabel("본문 글자 크기", { exact: true }).fill("18");
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  const latestSources = await page.locator(".standard-preview-pages figure img").evaluateAll(elements => elements.map(element => (element as HTMLImageElement).src));
  releaseFirst();
  await page.unroute("**/api/visual-standards/preview");
  await expect(page.getByLabel("본문 글자 크기", { exact: true })).toHaveValue("18");
  await expect.poll(async () => page.locator(".standard-preview-pages figure img").evaluateAll(elements => elements.map(element => (element as HTMLImageElement).src))).toEqual(latestSources);
  // An equivalent numeric spelling still settles, rather than getting stuck
  // after the current render generation was invalidated.
  await page.getByLabel("본문 글자 크기", { exact: true }).fill("18.0");
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  const settledSources = await page.locator(".standard-preview-pages figure img").evaluateAll(elements => elements.map(element => (element as HTMLImageElement).src));
  await page.getByLabel("본문 글자 크기", { exact: true }).fill("99");
  await expect(registerButton(page)).toBeDisabled();
  await expect(page.getByRole("alert")).toContainText("범위의 수치");
  expect(await page.locator(".standard-preview-pages figure img").evaluateAll(elements => elements.map(element => (element as HTMLImageElement).src))).toEqual(settledSources);
  await page.getByLabel("본문 글자 크기", { exact: true }).fill("18");
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await registerButton(page).click();
  await page.getByLabel("표준 이름", { exact: true }).fill(`최신 예시 검증 ${Date.now()}`);
  const registered = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/visual-standards");
  await page.getByRole("button", { name: "새 표준 등록", exact: true }).click();
  expect((await (await registered).json()).settings.body_size_pt).toBe(18);
});

test("REAL rhwp standard designer: lost registration response retries the same frozen request", async ({ page, request }) => {
  await openDesigner(page);
  const bodies: string[] = [];
  let registeredId = "";
  await page.route("**/api/visual-standards", async route => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    bodies.push(route.request().postData()!);
    const response = await route.fetch();
    registeredId = (await response.json()).id;
    if (bodies.length === 1) await route.abort("failed");
    else await route.fulfill({ response });
  });
  await registerButton(page).click();
  const name = `등록 재확인 ${Date.now()}`;
  await page.getByLabel("표준 이름", { exact: true }).fill(name);
  await page.getByRole("button", { name: "새 표준 등록", exact: true }).click();
  await expect(page.getByRole("button", { name: "같은 등록 결과 재확인", exact: true })).toBeEnabled();
  await expect(page.getByLabel("표준 이름", { exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "취소", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "같은 등록 결과 재확인", exact: true }).click();
  await expect(page.getByRole("heading", { name: `‘${name}’ 등록 완료`, exact: true })).toBeVisible();
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toBe(bodies[0]);
  const catalog = await (await request.get("/api/visual-standards")).json();
  expect(catalog.items.filter((item: { id: string }) => item.id === registeredId)).toHaveLength(1);
});

test("REAL rhwp standard designer: a legacy registration opens with new defaults without rewriting its frozen snapshot", async ({ page, request }, info) => {
  const firstPreview = page.waitForRequest(value => value.method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/preview");
  await openDesigner(page);
  const seed = (await firstPreview).postDataJSON().seed_base64;
  const catalog = await (await request.get("/api/visual-standards")).json();
  const added = new Set(["table_horizontal_align", "table_vertical_align", "table_border_style", "table_border_width_mm",
    "table_border_scope", "table_cell_padding_mm", "title_vertical_offset_pt", "body_parenthetical_labels",
    "show_core_summary", "page_number_style", "page_number_position", "citation_style"]);
  const settings = Object.fromEntries(Object.entries(catalog.defaults).filter(([key]) => !added.has(key)));
  expect(Object.keys(settings)).toHaveLength(23);
  const rendered = await request.post("/api/visual-standards/preview", { data: { settings, seed_base64: seed } });
  expect(rendered.ok(), await rendered.text()).toBeTruthy();
  const preview = await rendered.json();
  const created = await request.post("/api/visual-standards", { data: {
    id: randomUUID(), name: `이전 표준 보존 검증 ${Date.now()}`, settings, seed_base64: seed,
    settings_sha256: preview.settings_sha256, preview_sha256: preview.preview_sha256, base_standard: preview.base_standard,
  } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const legacy = await created.json();
  expect(legacy.revision).toBe(1);
  expect(legacy.settings).toEqual(settings);
  const oldFile = await (await request.get(`/api/visual-standards/${legacy.id}/file`)).body();
  expect(hash(oldFile)).toBe(legacy.preview_sha256);
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  await openDesigner(page);
  await page.getByLabel("설정 불러오기", { exact: true }).selectOption(legacy.id);
  await expect(page.getByRole("note")).toContainText("새 옵션은 기본값으로 보완");
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  for (const key of added) {
    const field = catalog.fields.find((item: any) => item.key === key);
    await openGroup(page, field.group);
    const control = page.getByLabel(`${field.group} ${field.label}`, { exact: true });
    if (field.type === "boolean") await expect(control).toBeChecked({ checked: catalog.defaults[key] });
    else await expect(control).toHaveValue(String(catalog.defaults[key]));
  }
  const copyResponse = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards");
  await registerButton(page).click();
  await page.getByRole("button", { name: "새 표준 등록", exact: true }).click();
  const copy = await (await copyResponse).json();
  expect(copy.id).not.toBe(legacy.id);
  expect(copy.revision).toBe(2);
  expect(copy.settings).toEqual(catalog.defaults);
  const after = await (await request.get("/api/visual-standards")).json();
  expect(after.items.find((item: any) => item.id === legacy.id)).toEqual(legacy);
  expect(hash(await (await request.get(`/api/visual-standards/${legacy.id}/file`)).body())).toBe(hash(oldFile));
  await writeFile(info.outputPath("legacy-registration-compatibility.json"), JSON.stringify({ real_engine: true,
    actual_model_calls: 0, legacy, copy, legacy_file_sha256: hash(oldFile), legacy_settings_preserved: true }, null, 2));
});

test("REAL rhwp + MOCK standard designer: registered settings survive report TOC, save and ordinary editor reopen", async ({ page, request }, info) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== "1" || !!process.env.HWP_REPORT_FIXTURES,
    "Synthetic report content with the actual rhwp engine; no model call.");
  test.setTimeout(180000);
  await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  await page.getByLabel("본문 글자 크기", { exact: true }).fill("16");
  await page.getByLabel("본문 줄 간격", { exact: true }).fill("180");
  await openGroup(page, "표");
  await page.getByLabel("표 머리글 채움색", { exact: true }).fill("#DDEFE5");
  await setCatalogField(page, catalog, "table_horizontal_align", "RIGHT");
  await setCatalogField(page, catalog, "table_vertical_align", "CENTER");
  await setCatalogField(page, catalog, "table_border_style", "DASH");
  await setCatalogField(page, catalog, "table_border_scope", "OUTER");
  await setCatalogField(page, catalog, "title_vertical_offset_pt", 8);
  await setCatalogField(page, catalog, "body_parenthetical_labels", false);
  await setCatalogField(page, catalog, "show_core_summary", false);
  await setCatalogField(page, catalog, "page_number_style", "ROMAN");
  await setCatalogField(page, catalog, "page_number_position", "BOTTOM_RIGHT");
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await registerButton(page).click();
  const name = `보고서 적용 표준 ${Date.now()}`;
  await page.getByLabel("표준 이름", { exact: true }).fill(name);
  const registered = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/visual-standards");
  await page.getByRole("button", { name: "새 표준 등록", exact: true }).click();
  const standard = await (await registered).json();
  const templates = await (await request.get("/api/report-templates")).json();
  const oldTask = await request.post("/api/report-tasks", { data: { id: randomUUID(),
    prompt: "[RICH] 기존 미확정 합성 보고서 보존", research: false, template: templates.items[0] } });
  expect(oldTask.ok()).toBeTruthy();
  const oldId = (await oldTask.json()).id;
  await expect.poll(async () => (await (await request.get(`/api/report-tasks/${oldId}`)).json()).status).toBe("ready");
  await page.evaluate(id => sessionStorage.setItem("hwp-report-task", id), oldId);
  await page.getByRole("button", { name: "이 표준으로 보고서 작성", exact: true }).click();
  await expect(page.getByLabel("문서 서식 표준", { exact: true })).toHaveValue(standard.id);
  await expect(page.getByLabel("보고서 작성 요청", { exact: true })).toBeEmpty();
  expect((await (await request.get(`/api/report-tasks/${oldId}`)).json()).approval).toBeNull();
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill("[RICH] 합성 표준 적용: 자료 정리와 검토 절차를 표와 흐름도로 정리");
  await page.getByLabel("공개 웹 자료를 검색하고 출처 포함").uncheck();
  const posted = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/report-tasks");
  await page.getByRole("button", { name: "Codex로 보고서 초안 만들기", exact: true }).click();
  const job = await (await posted).json();
  expect(job.input.visual_standard).toEqual(standard);
  await expect.poll(async () => (await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).isEnabled().catch(() => false)) ||
    (await page.getByRole("alert").isVisible().catch(() => false)), { timeout: 90000 }).toBeTruthy();
  expect(await page.getByRole("alert").allTextContents()).toEqual([]);
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeEnabled();
  await expect(page.getByLabel("초안 목차 상태", { exact: true })).toContainText("검증 완료");
  let frozen = await (await request.get(`/api/report-tasks/${job.id}/preview`)).json();
  expect(frozen.visual_standard).toEqual(standard);
  expect(frozen.toc_verified).toBe(true);
  expect(frozen.toc.headings.some((heading: any) => heading.text === "핵심 요약")).toBe(false);
  expect((await (await request.get(`/api/report-tasks/${job.id}`)).json()).draft.summary).toContain("□ (요약) 핵심 서비스 보호");
  let engine = await (await page.locator(".report-editor-slot.active iframe").elementHandle())!.contentFrame();
  if (!engine) throw new Error("보고서 미리보기 엔진이 없습니다.");
  let evidence = await engine.evaluate(headings => (window as any).hwpAssistantBridge.layoutEvidence(headings),
    frozen.toc.headings.map(({ id, section, paragraph, length, text_sha256 }: any) => ({ id, section, paragraph, length, text_sha256 })));
  expect(evidence.export_sha256).toBe(frozen.layout_sha256);
  expect(evidence.page_count).toBe(frozen.toc.page_count);
  expect(Object.fromEntries(evidence.headings.map((heading: any) => [heading.id, heading.page]))).toEqual(frozen.toc.pages);
  let exported = Buffer.from(await engine.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  expect(hash(exported)).toBe(frozen.layout_sha256);
  const beforeZip = unzipSync(exported);
  const header = strFromU8(beforeZip["Contents/header.xml"]), section = strFromU8(beforeZip["Contents/section0.xml"]);
  expect(header).toContain('faceColor="#DDEFE5"');
  expect(section).not.toContain("(조치)");
  expect(section).not.toContain("□ 조치:");
  expect(section).toContain("우선순위별 실행 항목과 책임을 표로 관리할 필요");
  expect(section).not.toContain(">핵심 요약<");
  const headerXml = await page.evaluate(source => {
    const doc = new DOMParser().parseFromString(source, "text/xml");
    const chars = Object.fromEntries(Array.from(doc.getElementsByTagNameNS("*", "charPr")).map(node => [node.getAttribute("id"), node.getAttribute("height")]));
    const paras = Object.fromEntries(Array.from(doc.getElementsByTagNameNS("*", "paraPr")).map(node => [node.getAttribute("id"), Array.from(node.getElementsByTagNameNS("*", "lineSpacing")).map(line => line.getAttribute("value"))]));
    return { chars, paras };
  }, header);
  const styledBody = await page.evaluate(({ source, styles }) => {
    const doc = new DOMParser().parseFromString(source, "text/xml");
    return Array.from(doc.getElementsByTagNameNS("*", "p")).filter(node => node.textContent?.startsWith("□ ")).map(node => ({
      sizes: Array.from(node.getElementsByTagNameNS("*", "run")).map(run => styles.chars[run.getAttribute("charPrIDRef")!]),
      lines: styles.paras[node.getAttribute("paraPrIDRef")!],
    }));
  }, { source: section, styles: headerXml });
  expect(styledBody.length).toBeGreaterThan(0);
  expect(styledBody.every(body => body.sizes.every(size => size === "1600") && body.lines.every(line => line === "180"))).toBe(true);
  const initialEvidence = evidence, initialToc = frozen.toc;
  await engine.getByRole("textbox", { name: "문서 편집 입력", exact: true }).focus();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(" 표준 옵션 적용 후 미저장 편집 보존 확인");
  await expect(page.getByLabel("초안 목차 상태", { exact: true })).toContainText("갱신 필요");
  await page.getByRole("button", { name: "목차 쪽수 갱신", exact: true }).click();
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeEnabled({ timeout: 90000 });
  await expect(page.getByLabel("초안 목차 상태", { exact: true })).toContainText("검증 완료");
  frozen = await (await request.get(`/api/report-tasks/${job.id}/preview`)).json();
  expect(frozen.visual_standard).toEqual(standard);
  expect(frozen.toc.id).toBe(initialToc.id);
  expect(frozen.toc.headings.map((heading: any) => heading.id)).toEqual(initialToc.headings.map((heading: any) => heading.id));
  engine = await (await page.locator(".report-editor-slot.active iframe").elementHandle())!.contentFrame();
  if (!engine) throw new Error("목차 갱신 후 보고서 미리보기 엔진이 없습니다.");
  evidence = await engine.evaluate(headings => (window as any).hwpAssistantBridge.layoutEvidence(headings),
    frozen.toc.headings.map(({ id, section, paragraph, length, text_sha256 }: any) => ({ id, section, paragraph, length, text_sha256 })));
  exported = Buffer.from(await engine.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  expect(hash(exported)).toBe(frozen.layout_sha256);
  expect(evidence.page_count).toBe(frozen.toc.page_count);
  expect(Object.fromEntries(evidence.headings.map((heading: any) => [heading.id, heading.page]))).toEqual(frozen.toc.pages);
  expect(strFromU8(unzipSync(exported)["Contents/section0.xml"])).toContain("표준 옵션 적용 후 미저장 편집 보존 확인");
  await page.screenshot({ path: info.outputPath("custom-standard-report.png"), fullPage: true });
  await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("HWPX 서버 저장 완료");
  const href = (await page.getByRole("link", { name: "완성 보고서 다운로드", exact: true }).getAttribute("href"))!;
  const saved = await (await request.get(href)).body();
  expect(hash(saved)).toBe(hash(exported));
  expect(await (await request.get(href.split("/versions/")[0] + "/versions")).json()).toHaveLength(1);
  await page.getByRole("button", { name: "보고서 편집하기", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const reopened = await (await page.locator(".block-editor-slot.active iframe").elementHandle())!.contentFrame();
  if (!reopened) throw new Error("일반 편집기 재열기에 실패했습니다.");
  const reopenedBytes = Buffer.from(await reopened.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  expect(hash(reopenedBytes)).toBe(hash(saved));
  const reopenedEvidence = await reopened.evaluate(headings => (window as any).hwpAssistantBridge.layoutEvidence(headings),
    frozen.toc.headings.map(({ id, section, paragraph, length, text_sha256 }: any) => ({ id, section, paragraph, length, text_sha256 })));
  expect(reopenedEvidence.page_count).toBe(evidence.page_count);
  expect(reopenedEvidence.headings).toEqual(evidence.headings);
  await writeFile(info.outputPath("custom-standard-report.hwpx"), saved);
  await writeFile(info.outputPath("custom-standard-proof.json"), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    human_approval: false, automated_approval: true, visual_standard: standard, initial_evidence: initialEvidence,
    refreshed_evidence: evidence,
    reopened_evidence: reopenedEvidence, saved_sha256: hash(saved), versions: 1 }, null, 2));
});
