import { test, expect, type Page, type APIRequestContext } from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const registerButton = (page: Page) => page.locator(".standard-preview-toolbar").getByRole("button", { name: "새 표준 등록", exact: true });
const submitRegistration = (page: Page) => page.getByRole("dialog").getByRole("button", { name: "새 표준 등록", exact: true });
const documentListButton = (page: Page) => page.locator(".workspace-nav").getByRole("button", { name: "문서함", exact: true });
const fieldLabel = (catalog: any, key: string) => {
  const field = catalog.fields.find((item: any) => item.key === key);
  if (!field) throw new Error(`catalog field ${key} is missing`);
  return `${field.group} ${field.label}`;
};
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
  const preview = page.waitForRequest(value => value.method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/preview");
  await page.goto("/");
  await page.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
  await expect(page.getByRole("heading", { name: "문서 표준 편집", exact: true })).toBeVisible();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await expect(page.getByRole("img", { name: "문서 표준 예시 1쪽", exact: true })).toBeVisible();
  return (await preview).postDataJSON().seed_base64 as string;
}
async function legacyDesigner(page: Page, request: APIRequestContext, revision: 1 | 2 = 2) {
  const seed = await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const definition = catalog.legacy[String(revision)];
  const rendered = await request.post("/api/visual-standards/preview", { data: { settings: definition.defaults, seed_base64: seed } });
  expect(rendered.ok(), await rendered.text()).toBeTruthy();
  const preview = await rendered.json();
  const created = await request.post("/api/visual-standards", { data: {
    id: randomUUID(), name: `기존 ${revision}형식 시험 ${Date.now()}`, settings: definition.defaults, seed_base64: seed,
    settings_sha256: preview.settings_sha256, preview_sha256: preview.preview_sha256, base_standard: preview.base_standard,
  } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const registered = await created.json();
  expect(registered.revision).toBe(revision);
  await documentListButton(page).click();
  await openDesigner(page);
  await page.getByLabel("설정 불러오기", { exact: true }).selectOption(registered.id);
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  return { catalog, definition, registered, seed };
}
async function openGroup(page: Page, group: string) {
  const section = page.locator(".standard-setting-group").filter({ has: page.locator("summary", { hasText: new RegExp(`^${group}$`) }) });
  if (!(await section.getAttribute("open")) && await section.getAttribute("open") !== "")
    await section.locator("summary").click();
}
async function previewImageHashes(page: Page, outputPath?: (index: number) => string) {
  const images = await page.locator(".standard-preview-pages figure img").all();
  const hashes: string[] = [];
  for (const [index, image] of images.entries()) hashes.push(hash(await image.screenshot(outputPath ? { path: outputPath(index) } : {})));
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
  const expectReflow = async () => {
    const preview = (await page.locator(".standard-preview").boundingBox())!;
    const nav = (await page.locator(".standard-workspace-rail").boundingBox())!;
    const navigationRight = nav.x + nav.width;
    const settings = page.getByRole("complementary", { name: "문서 표준 설정", exact: true });
    const visible = await settings.isVisible();
    const bounds = visible ? (await settings.boundingBox())! : null;
    if (bounds) { expect(bounds.x).toBeCloseTo(navigationRight, 1); expect(bounds.height).toBe(page.viewportSize()!.height); }
    expect(preview.x).toBeCloseTo(bounds ? bounds.x + bounds.width : navigationRight, 1);
    expect(preview.width).toBeCloseTo(page.viewportSize()!.width - preview.x, 1);
  };
  const previewBounds = (await page.locator(".standard-preview").boundingBox())!;
  expect(previewBounds.y).toBe(0);
  expect(previewBounds.height).toBe(page.viewportSize()!.height);
  await expectOuterViewportContained(page);
  await expectReflow();
  for (const selector of [".standard-preview-pages", ".standard-settings"]) {
    const pane = page.locator(selector);
    const openedSettings = selector === ".standard-settings" && !(await pane.isVisible());
    if (openedSettings)
      await page.getByRole("button", { name: "문서 표준 설정 열기", exact: true }).click();
    await expect(pane).toBeVisible();
    await expectReflow();
    const scroll = await pane.evaluate(element => {
      element.scrollTop = element.scrollHeight;
      return { top: element.scrollTop, maximum: element.scrollHeight - element.clientHeight };
    });
    expect(scroll.maximum).toBeGreaterThanOrEqual(0);
    if (selector === ".standard-settings") expect(scroll.maximum).toBeGreaterThan(0);
    if (scroll.maximum > 0) expect(scroll.top).toBeGreaterThan(0);
    expect(Math.abs(scroll.maximum - scroll.top)).toBeLessThanOrEqual(1);
    await expectOuterViewportContained(page);
    await pane.evaluate(element => { element.scrollTop = 0; });
    if (openedSettings)
      await page.getByRole("button", { name: "문서 표준 설정 닫기", exact: true }).click();
  }
  const images = await page.locator(".standard-preview-pages figure img").evaluateAll(nodes => nodes.map(node => (node as HTMLImageElement).src));
  const settingsWasOpen = await page.getByRole("complementary", { name: "문서 표준 설정", exact: true }).isVisible();
  await page.getByRole("button", { name: `문서 표준 설정 ${settingsWasOpen ? "닫기" : "열기"}`, exact: true }).click();
  await expectReflow();
  await expectOuterViewportContained(page);
  expect(await page.locator(".standard-preview-pages figure img").evaluateAll(nodes => nodes.map(node => (node as HTMLImageElement).src))).toEqual(images);
  await page.getByRole("button", { name: `문서 표준 설정 ${settingsWasOpen ? "열기" : "닫기"}`, exact: true }).click();
  await expectReflow();
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
  const modelPosts: string[] = [];
  page.on("request", value => { if (value.method() === "POST" && new URL(value.url()).pathname === "/api/report-tasks") modelPosts.push(value.url()); });
  const { catalog: fullCatalog, definition: catalog, registered: legacy } = await legacyDesigner(page, request);
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
  await page.getByRole("button", { name: "새 표준 기본값 복원", exact: true }).click();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  expect(await page.evaluate(() => document.activeElement?.getAttribute("aria-label"))).not.toBe("본문 줄 간격");
  expect(await page.evaluate(() => document.activeElement?.getAttribute("aria-label"))).not.toBe("절 제목 글자 크기");
  await page.getByLabel("설정 불러오기", { exact: true }).selectOption(legacy.id);
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
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
  await submitRegistration(page).click();
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
  expect((await (await request.get("/api/visual-standards")).json()).defaults).toEqual(fullCatalog.defaults);
  expect(await (await request.get("/api/document-standard")).json()).toEqual(original);
  await page.getByRole("button", { name: "이 표준으로 보고서 작성", exact: true }).click();
  await expect(page.getByLabel("문서 양식", { exact: true })).toHaveValue(saved.id);
  await expect(page.getByLabel("문서 양식", { exact: true })).toContainText(name);
  await expect(page.getByLabel("새 요청에 사용할 서식", { exact: true })).toContainText("항목 요약 말머리 숨김 · 핵심 요약 미포함");
  await expect(page.getByLabel("새 요청에 사용할 서식", { exact: true })).not.toContainText("말머리는 굵게 표시");
  await expect(page.getByRole("button", { name: "문서작성 기준 열기", exact: true })).toBeEnabled();
  await expect(page.getByRole("link", { name: /기준서.*(다운로드|사본)/ })).toHaveCount(0);
  expect(modelPosts).toHaveLength(0);
  await documentListButton(page).click();
  await page.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
  await page.getByLabel("설정 불러오기", { exact: true }).selectOption(saved.id);
  await expect(page.getByLabel("본문 글자 크기", { exact: true })).toHaveValue("16");
  const labelField = catalog.fields.find((field: any) => field.key === "body_parenthetical_labels");
  await openGroup(page, labelField.group);
  await expect(page.getByLabel(`${labelField.group} ${labelField.label}`, { exact: true })).not.toBeChecked();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await page.getByRole("button", { name: "새 표준 기본값 복원", exact: true }).click();
  await expect(page.getByLabel(fieldLabel(fullCatalog, "body_size_pt"), { exact: true })).toHaveValue(String(fullCatalog.defaults.body_size_pt));
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await page.getByLabel("설정 찾기", { exact: true }).fill(fullCatalog.fields.find((field: any) => field.key === "page_number_style").group);
  expect(await page.locator(".standard-setting-group").count()).toBeGreaterThan(0);
  await expect(page.getByLabel(fieldLabel(fullCatalog, "body_size_pt"), { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "설정 검색 지우기", exact: true }).click();
  await expect(page.getByLabel(fieldLabel(fullCatalog, "body_size_pt"), { exact: true })).toHaveCount(1);
  await page.setViewportSize({ width: 400, height: 760 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(400);
  await expectDesignerScrollContained(page);
  await page.screenshot({ path: info.outputPath("standard-designer-mobile.png"), fullPage: true });
});

test("REAL rhwp standard designer: stale preview cannot register and invalid values keep the last sample", async ({ page, request }) => {
  await legacyDesigner(page, request);
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
  await submitRegistration(page).click();
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
  await submitRegistration(page).click();
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
  expect(catalog.items.find((item: { id: string }) => item.id === registeredId).revision).toBe(3);
  expect(JSON.parse(bodies[0]).settings.preset_id).toBe(catalog.defaults.preset_id);
});

test("REAL rhwp standard designer: original rules, three presets, full reference and revision 3 registration", async ({ page, request }, info) => {
  test.setTimeout(180000);
  await page.addInitScript(() => {
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL), blobs = new Map<string, Blob>();
    (window as any).__standardTestBlobs = blobs;
    URL.createObjectURL = object => { const url = create(object); if (object instanceof Blob && object.type === "image/svg+xml") blobs.set(url, object); return url; };
    URL.revokeObjectURL = url => { revoke(url); blobs.delete(url); };
  });
  const posted: any[] = [];
  page.on("request", value => { if (value.method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/preview") posted.push(value.postDataJSON()); });
  await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const original = await (await request.get("/api/document-standard")).json();
  const originalFile = await (await request.get(original.reference_url)).body();
  expect(hash(originalFile)).toBe(catalog.source.reference_sha256);
  expect(catalog.presets).toHaveLength(3);
  expect(catalog.presets.every((preset: any) => preset.revision === 3 && preset.base_standard.reference_sha256 === hash(originalFile))).toBe(true);
  expect(catalog.rules.length).toBeGreaterThan(0);
  for (const field of catalog.fields.filter((field: any) => field.key !== "preset_id"))
    await expect(page.getByLabel(`${field.group} ${field.label}`, { exact: true })).toHaveCount(1);
  const ruleFields = catalog.fields.filter((field: any) => field.type === "textarea");
  expect(ruleFields.length).toBeGreaterThan(0);
  for (const field of ruleFields)
    await expect(page.getByLabel(`${field.group} ${field.label}`, { exact: true })).toHaveValue(catalog.defaults[field.key]);
  const settingKey = (value: any) => JSON.stringify(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)));
  const settle = async (expected: any, change: () => Promise<void>) => {
    const before = await page.locator(".standard-preview-pages figure img").evaluateAll(images => images.map(image => (image as HTMLImageElement).src));
    const completed = page.waitForResponse(value => value.request().method() === "POST" &&
      new URL(value.url()).pathname === "/api/visual-standards/preview" &&
      settingKey(value.request().postDataJSON().settings) === settingKey(expected));
    await change();
    const response = await completed;
    expect(response.ok()).toBeTruthy();
    const rendered = await response.json();
    await expect(page.locator(".standard-preview-pages")).toHaveAttribute("data-preview-sha256", rendered.preview_sha256, { timeout: 90000 });
    await expect(page.locator(".standard-preview-pages")).toHaveAttribute("data-settings-sha256", rendered.settings_sha256);
    await expect(page.locator(".standard-preview-pages")).toHaveAttribute("aria-busy", "false");
    await expect(registerButton(page)).toBeEnabled();
    await expect.poll(() => page.locator(".standard-preview-pages figure img").evaluateAll(images => images.map(image => (image as HTMLImageElement).src))).not.toEqual(before);
    return rendered;
  };
  const customized = { ...catalog.defaults, table_header_fill: "#DDEFE5" };
  await settle(customized, () => setCatalogField(page, catalog, "table_header_fill", "#DDEFE5"));
  const presetProof: any[] = [];
  for (const preset of catalog.presets) {
    const rendered = await settle({ ...customized, preset_id: preset.settings.preset_id },
      async () => { await page.getByLabel("기본 양식", { exact: true }).selectOption(String(preset.settings.preset_id)); });
    const current = posted[posted.length - 1].settings;
    expect(current).toEqual({ ...customized, preset_id: preset.settings.preset_id });
    const svgs = await page.locator(".standard-preview-pages figure img").evaluateAll(async images => {
      const blobs = (window as any).__standardTestBlobs as Map<string, Blob>;
      return Promise.all(images.map(async image => {
        const blob = blobs.get((image as HTMLImageElement).src);
        if (!blob) throw new Error("반영된 예시 SVG를 확인할 수 없습니다.");
        const svg = await blob.text();
        return { svg, text: new DOMParser().parseFromString(svg, "image/svg+xml").documentElement.textContent || "" };
      }));
    });
    for (const [index, svg] of svgs.entries()) await writeFile(info.outputPath(`${preset.settings.preset_id}-page-${index + 1}.svg`), svg.svg);
    const images = await previewImageHashes(page, index => info.outputPath(`${preset.settings.preset_id}-page-${index + 1}.png`));
    expect(images.length).toBeGreaterThan(0);
    expect(images.length).toBeLessThanOrEqual(catalog.max_pages);
    presetProof.push({ id: preset.id, preset_id: preset.settings.preset_id, page_count: images.length,
      preview_sha256: rendered.preview_sha256, settings_sha256: rendered.settings_sha256,
      rendered_svg: svgs.map(svg => ({ sha256: hash(Buffer.from(svg.svg)), text: svg.text })), rendered_image_sha256: images });
  }
  await writeFile(info.outputPath("reference-standard-v3-presets.json"), JSON.stringify(presetProof, null, 2));
  expect(new Set(presetProof.map(item => JSON.stringify(item.rendered_image_sha256))).size).toBe(3);
  const selected = posted[posted.length - 1].settings;
  const readonly = page.waitForResponse(value => new URL(value.url()).pathname === catalog.reference_preview_url && value.request().method() === "GET");
  await page.getByRole("tab", { name: "기준서 전체", exact: true }).click();
  const reference = await (await readonly).json();
  await expect(page.locator(".standard-preview-pages")).toHaveAttribute("aria-busy", "false", { timeout: 90000 });
  await expect(registerButton(page)).toHaveCount(0);
  await expect(page.getByLabel("설정 불러오기", { exact: true })).toHaveCount(0);
  const referenceImages = page.getByRole("img", { name: /^기준서 \d+쪽$/ });
  const pages = await referenceImages.count();
  expect(pages).toBeGreaterThan(0); expect(pages).toBeLessThanOrEqual(reference.max_pages);
  const frame = await (await page.locator(".standard-renderer iframe").elementHandle())!.contentFrame();
  if (!frame) throw new Error("기준서를 읽는 실제 한글 엔진이 없습니다.");
  expect(await frame.evaluate(() => (window as any).hwpAssistantBridge.documentState().pageCount)).toBe(pages);
  const renderedText = await referenceImages.evaluateAll(async images => {
    const blobs = (window as any).__standardTestBlobs as Map<string, Blob>;
    const texts = await Promise.all(images.map(async image => {
      const blob = blobs.get((image as HTMLImageElement).src);
      if (!blob) throw new Error("기준서 SVG 원문을 확인할 수 없습니다.");
      return new DOMParser().parseFromString(await blob.text(), "image/svg+xml").documentElement.textContent || "";
    }));
    return texts.join("").replace(/\s+/g, "");
  });
  expect(renderedText).toContain(catalog.rules[0].text.replace(/\s+/g, ""));
  await page.screenshot({ path: info.outputPath("reference-full-document.png"), fullPage: true });
  await page.getByRole("tab", { name: "서식 미리보기", exact: true }).click();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  expect(posted[posted.length - 1].settings).toEqual(selected);
  await registerButton(page).click();
  const name = `원문 기반 표준 ${Date.now()}`;
  await page.getByLabel("표준 이름", { exact: true }).fill(name);
  const registration = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards");
  await submitRegistration(page).click();
  const saved = await (await registration).json();
  expect(saved.revision).toBe(3); expect(saved.settings).toEqual(selected);
  expect(hash(await (await request.get(`/api/visual-standards/${saved.id}/file`)).body())).toBe(saved.preview_sha256);
  await documentListButton(page).click();
  await openDesigner(page);
  await page.getByLabel("설정 불러오기", { exact: true }).selectOption(saved.id);
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await expect(page.getByLabel("기본 양식", { exact: true })).toHaveValue(String(selected.preset_id));
  await expect(page.getByLabel(fieldLabel(catalog, "table_header_fill"), { exact: true })).toHaveValue("#DDEFE5");
  expect(hash(await (await request.get(original.reference_url)).body())).toBe(hash(originalFile));
  await writeFile(info.outputPath("reference-standard-v3-proof.json"), JSON.stringify({ real_engine: true,
    actual_model_calls: 0, readonly_reference: { original_sha256: hash(originalFile), preview_sha256: reference.preview_sha256, pages },
    presets: presetProof, registered: saved, original_unchanged: true }, null, 2));
});

test("REAL rhwp + MOCK standard designer: a fresh report freezes the original appendix 1 default and reopens", async ({ page, request }, info) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== "1" || !!process.env.HWP_REPORT_FIXTURES,
    "Synthetic report content with the actual rhwp engine; no model call.");
  test.setTimeout(180000);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const preset = catalog.presets.find((item: any) => item.id === catalog.default_id);
  expect(preset.revision).toBe(3);
  expect(preset.settings.preset_id).toBe("appendix-1");
  const { id, revision, name, settings, settings_sha256, base_standard, preview_sha256 } = preset;
  const selection = { id, revision, name, settings, settings_sha256, base_standard, preview_sha256 };
  await page.goto("/");
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  const selected = page.getByLabel("문서 양식", { exact: true });
  await expect(selected).toBeEnabled();
  await expect(selected).toHaveValue(catalog.default_id);
  await selected.selectOption("");
  const refreshed = page.waitForResponse(value => value.request().method() === "GET" && new URL(value.url()).pathname === "/api/visual-standards");
  await page.getByText("양식 관리·연결 정보", { exact: true }).click();
  await page.getByRole("button", { name: "문서 표준 목록 다시 확인", exact: true }).click();
  await refreshed;
  await expect(selected).toHaveValue(""); // Refresh must preserve an explicit legacy choice.
  await selected.selectOption(catalog.default_id);
  await expect(page.getByRole("link", { name: "선택한 등록 표준 예시", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: /기준서.*(원본|사본|다운로드)/ })).toHaveCount(0);
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill("[RICH] 기준서 별첨 1 기본 양식으로 합성 자료 정비 방안과 역할을 정리");
  await page.getByLabel("공개 웹 자료를 검색하고 출처 포함").uncheck();
  const accepted = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/report-tasks");
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  const response = await accepted, sent = response.request().postDataJSON(), job = await response.json();
  expect(response.ok()).toBeTruthy();
  expect(sent.visual_standard).toEqual(selection);
  expect(Object.keys(sent.visual_standard).sort()).toEqual(Object.keys(selection).sort());
  expect(job.input.visual_standard).toEqual(selection);
  await page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true }).click();
  await expect.poll(async () => (await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).isEnabled().catch(() => false)) ||
    (await page.getByRole("alert").isVisible().catch(() => false)), { timeout: 90000 }).toBeTruthy();
  expect(await page.getByRole("alert").allTextContents()).toEqual([]);
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeEnabled();
  await expect(page.getByLabel("초안 목차 상태", { exact: true })).toHaveCount(0);
  const frozen = await (await request.get(`/api/report-tasks/${job.id}/preview`)).json();
  expect(frozen.visual_standard).toEqual(selection);
  expect(frozen.toc_policy).toBeUndefined(); // Appendix 1 follows the original without an automatic TOC.
  const engine = await (await page.locator(".report-editor-slot.active iframe").elementHandle())!.contentFrame();
  if (!engine) throw new Error("별첨 1 보고서를 표시한 실제 엔진이 없습니다.");
  const bytes = Buffer.from(await engine.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  const layout = Buffer.from(frozen.layout_base64, "base64");
  expect(hash(layout)).toBe(frozen.layout_sha256);
  // Appendix 1 has no TOC verification pass that adopts the rhwp round trip.
  // Verify its content/settings while retaining the original layout baseline.
  const semantic = await page.evaluate(({ layoutSection, layoutHeader, exportedSection, exportedHeader }) => {
    const parse = (source: string) => new DOMParser().parseFromString(source, "application/xml");
    const read = (section: string, header: string) => {
      const xml = parse(section), styles = parse(header);
      const directText = (paragraph: Element) => Array.from(paragraph.children).filter(node => node.localName === "run")
        .flatMap(run => Array.from(run.children).filter(node => node.localName === "t").map(node => node.textContent || "")).join("");
      const marker = Array.from(styles.getElementsByTagNameNS("*", "style")).find(node => node.getAttribute("name") === "HWP Assistant Reference Standard:v3");
      return { paragraphs: Array.from(xml.getElementsByTagNameNS("*", "p")).map(directText).filter(text => text !== ""),
        cells: Array.from(xml.getElementsByTagNameNS("*", "tc")).map(cell => Array.from(cell.getElementsByTagNameNS("*", "t")).map(node => node.textContent || "").join("")),
        settings: marker ? JSON.parse(marker.getAttribute("engName") || "null") : null };
    };
    return { baseline: read(layoutSection, layoutHeader), exported: read(exportedSection, exportedHeader) };
  }, { layoutSection: strFromU8(unzipSync(layout)["Contents/section0.xml"]), layoutHeader: strFromU8(unzipSync(layout)["Contents/header.xml"]),
    exportedSection: strFromU8(unzipSync(bytes)["Contents/section0.xml"]), exportedHeader: strFromU8(unzipSync(bytes)["Contents/header.xml"]) });
  expect(semantic.exported).toEqual(semantic.baseline);
  expect(semantic.exported.paragraphs.length).toBeGreaterThan(0);
  expect(semantic.exported.settings).toEqual(Object.fromEntries(Object.entries(selection.settings).filter(([key]) => key.startsWith("toc_") || key === "preset_id")));
  const pages = await engine.evaluate(() => (window as any).hwpAssistantBridge.documentState().pageCount);
  expect(pages).toBeGreaterThan(0);
  await page.screenshot({ path: info.outputPath("original-appendix1-report.png"), fullPage: true });
  await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("HWPX 서버 저장 완료");
  const href = (await page.getByRole("link", { name: "완성 보고서 다운로드", exact: true }).getAttribute("href"))!;
  const saved = await (await request.get(href)).body();
  expect(hash(saved)).toBe(hash(bytes));
  const retained = await (await request.get(`/api/report-tasks/${job.id}/preview`)).json();
  expect(retained.layout_sha256).toBe(frozen.layout_sha256);
  expect(retained.layout_base64).toBe(frozen.layout_base64);
  expect(retained.visual_standard).toEqual(selection);
  await page.getByRole("button", { name: "보고서 편집하기", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const reopened = await (await page.locator(".block-editor-slot.active iframe").elementHandle())!.contentFrame();
  if (!reopened) throw new Error("별첨 1 보고서의 일반 편집기 재열기에 실패했습니다.");
  expect(hash(Buffer.from(await reopened.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[])))).toBe(hash(saved));
  expect(await reopened.evaluate(() => (window as any).hwpAssistantBridge.documentState().pageCount)).toBe(pages);
  await writeFile(info.outputPath("original-appendix1-report.hwpx"), saved);
  await writeFile(info.outputPath("original-appendix1-proof.json"), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    human_approval: false, automated_approval: true, builtin_default: selection, pages,
    frozen_layout_sha256: frozen.layout_sha256, engine_export_sha256: hash(bytes), semantic_content_and_settings_preserved: true,
    frozen_layout_preserved_after_save: true, saved_sha256: hash(saved),
    no_extra_catalog_metadata_in_input: true, saved_and_reopened: true }, null, 2));
});

for (const composition of [{ preset: "appendix-2", tocPage: 2, firstBodyPage: 3 }, { preset: "appendix-3", tocPage: 1, firstBodyPage: 2 }]) {
  test(`REAL rhwp + MOCK standard designer: original ${composition.preset} cover, verified TOC pages, save and reopen`, async ({ page, request }, info) => {
    test.skip(process.env.HWP_E2E_REPORT_MOCK !== "1" || !!process.env.HWP_REPORT_FIXTURES,
      "Synthetic report content with the actual rhwp engine; no model call.");
    test.setTimeout(180000);
    const catalog = await (await request.get("/api/visual-standards")).json();
    const preset = catalog.presets.find((item: any) => item.settings.preset_id === composition.preset);
    const { id, revision, name, settings, settings_sha256, base_standard, preview_sha256 } = preset;
    const selection = { id, revision, name, settings, settings_sha256, base_standard, preview_sha256 };
    await page.goto("/");
    await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
    await expect(page.getByLabel("문서 양식", { exact: true })).toBeEnabled();
    await page.getByLabel("문서 양식", { exact: true }).selectOption(preset.id);
    await page.getByLabel("보고서 작성 요청", { exact: true }).fill(`[RICH] 기준서 ${composition.preset} 양식으로 합성 자료 정비 방안과 역할을 정리`);
    await page.getByLabel("공개 웹 자료를 검색하고 출처 포함").uncheck();
    const accepted = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/report-tasks");
    await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
    const response = await accepted, job = await response.json();
    expect(response.ok()).toBeTruthy();
    expect(response.request().postDataJSON().visual_standard).toEqual(selection);
    await page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true }).click();
    await expect.poll(async () => (await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).isEnabled().catch(() => false)) ||
      (await page.getByRole("alert").isVisible().catch(() => false)), { timeout: 90000 }).toBeTruthy();
    expect(await page.getByRole("alert").allTextContents()).toEqual([]);
    await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeEnabled();
    await expect(page.getByLabel("초안 목차 상태", { exact: true })).toContainText("검증 완료");
    const frozen = await (await request.get(`/api/report-tasks/${job.id}/preview`)).json();
    expect(frozen.visual_standard).toEqual(selection);
    expect(frozen.toc_policy).toBe("report-native-toc-v1");
    expect(frozen.toc_verified).toBe(true);
    expect(frozen.toc.headings.length).toBeGreaterThan(0);
    const completed = await (await request.get(`/api/report-tasks/${job.id}`)).json();
    const engine = await (await page.locator(".report-editor-slot.active iframe").elementHandle())!.contentFrame();
    if (!engine) throw new Error("별첨 보고서의 실제 한글 엔진이 없습니다.");
    const bytes = Buffer.from(await engine.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
    expect(hash(bytes)).toBe(frozen.layout_sha256);
    const headingTargets = frozen.toc.headings.map(({ id, section, paragraph, length, text_sha256 }: any) => ({ id, section, paragraph, length, text_sha256 }));
    const evidence = await engine.evaluate(headings => (window as any).hwpAssistantBridge.layoutEvidence(headings), headingTargets);
    expect(evidence.export_sha256).toBe(frozen.layout_sha256);
    expect(evidence.page_count).toBe(frozen.toc.page_count);
    expect(Object.fromEntries(evidence.headings.map((heading: any) => [heading.id, heading.page]))).toEqual(frozen.toc.pages);
    expect(Math.min(...evidence.headings.map((heading: any) => heading.page))).toBe(composition.firstBodyPage);
    const zip = unzipSync(bytes), section = strFromU8(zip["Contents/section0.xml"]), header = strFromU8(zip["Contents/header.xml"]);
    const document = await page.evaluate(({ section, title }) => {
      const xml = new DOMParser().parseFromString(section, "application/xml");
      const paragraphs = Array.from(xml.documentElement.children);
      const text = (node: Element) => Array.from(node.getElementsByTagNameNS("*", "t")).map(value => value.textContent || "").join("");
      return { title: paragraphs.findIndex(node => text(node).includes(title)),
        toc: paragraphs.findIndex(node => Array.from(node.getElementsByTagNameNS("*", "t")).some(value => value.textContent?.replace(/\s/g, "") === "<목차>")),
        text: paragraphs.map(text).join("\n") };
    }, { section, title: completed.draft.title });
    expect(document.title).toBeGreaterThanOrEqual(0);
    expect(document.toc).toBeGreaterThanOrEqual(0);
    expect(document.text).not.toContain("2015");
    expect(document.text).not.toContain("○○팀");
    expect(document.text).not.toContain("REPORT_SLOT_");
    const flow = await engine.evaluate(indices => indices.map(index => (window as any).hwpAssistantBridge.paragraphPage(0, index)), [document.title, document.toc]);
    expect(flow).toEqual([1, composition.tocPage]);
    for (const heading of frozen.toc.headings) {
      expect(heading.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
      expect(hash(Buffer.from(heading.text))).toBe(heading.text_sha256);
      expect(header).toContain(heading.id);
    }
    await page.screenshot({ path: info.outputPath(`${composition.preset}-report.png`), fullPage: true });
    await page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("HWPX 서버 저장 완료");
    const href = (await page.getByRole("link", { name: "완성 보고서 다운로드", exact: true }).getAttribute("href"))!;
    const saved = await (await request.get(href)).body();
    expect(hash(saved)).toBe(hash(bytes));
    await page.getByRole("button", { name: "보고서 편집하기", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    const reopened = await (await page.locator(".block-editor-slot.active iframe").elementHandle())!.contentFrame();
    if (!reopened) throw new Error("별첨 보고서의 일반 편집기 재열기에 실패했습니다.");
    expect(hash(Buffer.from(await reopened.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[])))).toBe(hash(saved));
    const reopenedEvidence = await reopened.evaluate(headings => (window as any).hwpAssistantBridge.layoutEvidence(headings), headingTargets);
    expect(reopenedEvidence.export_sha256).toBe(hash(saved));
    expect(reopenedEvidence.page_count).toBe(evidence.page_count);
    expect(reopenedEvidence.headings).toEqual(evidence.headings);
    expect(await reopened.evaluate(indices => indices.map(index => (window as any).hwpAssistantBridge.paragraphPage(0, index)), [document.title, document.toc])).toEqual(flow);
    await writeFile(info.outputPath(`${composition.preset}-report.hwpx`), saved);
    await writeFile(info.outputPath(`${composition.preset}-report-proof.json`), JSON.stringify({ real_engine: true,
      actual_model_calls: 0, human_approval: false, automated_approval: true, visual_standard: selection,
      cover_page: flow[0], toc_page: flow[1], first_body_page: composition.firstBodyPage, original_example_metadata_absent: true,
      initial_evidence: evidence, reopened_evidence: reopenedEvidence, saved_sha256: hash(saved) }, null, 2));
  });
}

test("REAL rhwp standard designer: a legacy registration keeps its own fields and frozen snapshot", async ({ page, request }, info) => {
  const { catalog, definition, registered: legacy } = await legacyDesigner(page, request, 1);
  const settings = definition.defaults;
  expect(Object.keys(settings)).toHaveLength(23);
  expect(legacy.revision).toBe(1);
  expect(legacy.settings).toEqual(settings);
  const oldFile = await (await request.get(`/api/visual-standards/${legacy.id}/file`)).body();
  expect(hash(oldFile)).toBe(legacy.preview_sha256);
  await expect(page.getByRole("note")).toContainText("이전 등록 표준의 설정을 그대로");
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await expect(page.getByLabel("기본 양식", { exact: true })).toHaveCount(0);
  for (const field of catalog.legacy["2"].fields.filter((item: any) => !(item.key in settings)))
    await expect(page.getByLabel(`${field.group} ${field.label}`, { exact: true })).toHaveCount(0);
  const copyResponse = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards");
  await registerButton(page).click();
  await submitRegistration(page).click();
  const copy = await (await copyResponse).json();
  expect(copy.id).not.toBe(legacy.id);
  expect(copy.revision).toBe(1);
  expect(copy.settings).toEqual(settings);
  expect(copy.settings_sha256).toBe(legacy.settings_sha256);
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
  const { definition: catalog } = await legacyDesigner(page, request);
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
  await submitRegistration(page).click();
  const standard = await (await registered).json();
  const templates = await (await request.get("/api/report-templates")).json();
  const oldTask = await request.post("/api/report-tasks", { data: { id: randomUUID(),
    prompt: "[RICH] 기존 미확정 합성 보고서 보존", research: false, template: templates.items[0] } });
  expect(oldTask.ok()).toBeTruthy();
  const oldId = (await oldTask.json()).id;
  await expect.poll(async () => (await (await request.get(`/api/report-tasks/${oldId}`)).json()).status).toBe("ready");
  await page.evaluate(id => sessionStorage.setItem("hwp-report-task", id), oldId);
  await page.getByRole("button", { name: "이 표준으로 보고서 작성", exact: true }).click();
  await expect(page.getByLabel("문서 양식", { exact: true })).toHaveValue(standard.id);
  await expect(page.getByLabel("보고서 작성 요청", { exact: true })).toBeEmpty();
  expect((await (await request.get(`/api/report-tasks/${oldId}`)).json()).approval).toBeNull();
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill("[RICH] 합성 표준 적용: 자료 정리와 검토 절차를 표와 흐름도로 정리");
  await page.getByLabel("공개 웹 자료를 검색하고 출처 포함").uncheck();
  const posted = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/report-tasks");
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  const job = await (await posted).json();
  expect(job.input.visual_standard).toEqual(standard);
  await page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true }).click();
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
  await page.getByRole("button", { name: "작성 요청", exact: true }).click();
  await expect(page.getByLabel("현재 보고서 고정 서식", { exact: true })).toContainText(standard.name);
  await expect(page.getByLabel("현재 보고서 고정 서식", { exact: true })).toContainText("항목 요약 말머리 숨김 · 핵심 요약 미포함");
  await page.getByLabel("문서 양식", { exact: true }).selectOption("");
  await expect(page.getByLabel("문서 양식", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("새 요청에 사용할 서식", { exact: true })).toContainText("항목 요약 말머리 표시 · 핵심 요약 포함");
  await expect(page.getByLabel("현재 보고서 고정 서식", { exact: true })).toContainText(standard.name);
  expect(hash(Buffer.from(await engine.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[])))).toBe(hash(saved));
  await page.getByRole("button", { name: "결과·출처", exact: true }).click();
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
