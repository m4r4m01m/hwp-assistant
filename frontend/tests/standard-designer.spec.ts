import { test, expect, type Page, type APIRequestContext } from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { unzipSync, strFromU8 } from "fflate";

const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const registerButton = (page: Page) => page.locator(".standard-preview-toolbar").getByRole("button", { name: "새 표준 등록", exact: true });
const submitRegistration = (page: Page) => page.getByRole("dialog").getByRole("button", { name: "새 표준 등록", exact: true });
const documentListButton = (page: Page) => page.locator(".workspace-nav").getByRole("button", { name: "문서함", exact: true });
async function showSettings(page: Page) {
  if (!(await page.getByRole("complementary", { name: "문서 표준 설정", exact: true }).isVisible()))
    await page.getByRole("button", { name: "문서 표준 설정 열기", exact: true }).click();
}
async function confirmSettingsChange(page: Page) {
  const dialog = page.getByRole("alertdialog", { name: "서식 설정 바꾸기", exact: true });
  if (await dialog.isVisible()) await dialog.getByRole("button", { name: "설정 바꾸기", exact: true }).click();
}
async function loadSavedStandard(page: Page, id: string) {
  await showSettings(page);
  const summary = page.getByText("저장한 표준에서 시작", { exact: true });
  if (await summary.locator("..").getAttribute("open") === null) await summary.click();
  await page.getByLabel("저장한 표준", { exact: true }).selectOption(id);
  await page.getByRole("button", { name: "불러오기", exact: true }).click();
  await confirmSettingsChange(page);
}
async function restoreDefaults(page: Page) {
  await page.getByRole("button", { name: "새 표준 기본값 복원", exact: true }).click();
  await confirmSettingsChange(page);
}
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
  else if (field.type === "select" || await control.evaluate(element => element.tagName === "SELECT")) {
    await control.focus();
    await control.selectOption(String(value));
  }
  else await control.fill(String(value));
}
async function expectDesignerReady(page: Page) {
  const alerts = page.locator('[role="alert"]:visible');
  await expect.poll(async () => await registerButton(page).isEnabled().catch(() => false) || await alerts.count() > 0,
    { timeout: 90000 }).toBe(true);
  expect(await alerts.allTextContents(), "The actual preview must finish without a server/engine error").toEqual([]);
  await expect(registerButton(page)).toBeEnabled();
}
async function openDesigner(page: Page) {
  const preview = page.waitForRequest(value => value.method() === "POST" &&
    ["/api/visual-standards/preview", "/api/visual-standards/sample-preview"].includes(new URL(value.url()).pathname));
  await page.goto("/");
  await page.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
  await expect(page.getByRole("heading", { name: "문서 표준 편집", exact: true })).toBeVisible();
  await expectDesignerReady(page);
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
  await loadSavedStandard(page, registered.id);
  await page.getByRole("tab", { name: "상세 설정", exact: true }).click();
  for (const group of ["대제목", "절 제목", "본문"]) await openGroup(page, group);
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  return { catalog, definition, registered, seed };
}
async function openGroup(page: Page, group: string) {
  await showSettings(page);
  await page.getByRole("tab", { name: "상세 설정", exact: true }).click();
  const section = page.locator(".standard-setting-group").filter({ has: page.locator("summary", { hasText: new RegExp(`^${group}$`) }) });
  if (!(await section.getAttribute("open")) && await section.getAttribute("open") !== "")
    await section.locator("summary").click();
}

test("REAL rhwp standard designer: common settings expose automatic notes and detailed settings remain searchable", async ({ page, request }, info) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const citation = page.getByLabel(fieldLabel(catalog, "citation_style"), { exact: true });
  await expect(page.getByRole("tab", { name: "자주 쓰는 설정", exact: true })).toHaveAttribute("aria-selected", "true");
  const citationGroup = page.locator(".standard-setting-group").filter({ has: page.locator("summary", {
    hasText: new RegExp(`^${catalog.fields.find((field: any) => field.key === "citation_style").group}$`),
  }) });
  if (await citationGroup.getAttribute("open") === null) await citationGroup.locator("summary").click();
  await expect(citation).toBeVisible();
  await expect(citation).toHaveValue("AUTO");
  await expect(page.getByLabel(fieldLabel(catalog, "square_first_line_indent_pt"), { exact: true })).toHaveCount(0);
  const automaticHashes = await previewImageHashes(page);
  await citation.selectOption("STARS");
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  const fixedHashes = await previewImageHashes(page);
  expect(fixedHashes).not.toEqual(automaticHashes);
  await citation.selectOption("AUTO");
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await page.getByLabel("설정 찾기", { exact: true }).fill("첫 줄 들여쓰기");
  await expect(page.getByLabel(fieldLabel(catalog, "square_first_line_indent_pt"), { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "설정 검색 지우기", exact: true }).click();
  await page.getByRole("tab", { name: "상세 설정", exact: true }).click();
  await openGroup(page, "□ 기호 항목");
  await expect(page.getByLabel(fieldLabel(catalog, "square_first_line_indent_pt"), { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "자주 쓰는 설정", exact: true }).click();
  await expect(citation).toHaveValue("AUTO");
  await page.screenshot({ path: info.outputPath("standard-common-auto-notes.png"), fullPage: true });
});
test("REAL rhwp standard designer: saved settings load only after explicit confirmation and keep legacy preview policy", async ({ page, request }, info) => {
  const posted: any[] = [];
  page.on("request", value => { if (value.method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/sample-preview") posted.push(value.postDataJSON()); });
  const seed = await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  expect(catalog.defaults.preview_layout_policy).toBe("NATIVE_V1");
  expect(posted.at(-1).settings.preview_layout_policy).toBe("NATIVE_V1");
  // A historical revision-3 registration predates the new preview-only policy.
  // Its absence is part of the frozen selection, not permission to add defaults.
  const previousSettings = { ...catalog.defaults };
  delete previousSettings.preview_layout_policy;
  delete previousSettings.body_parenthetical_scope;
  const previousResponse = await request.post("/api/visual-standards/preview", { data: { settings: previousSettings, seed_base64: seed } });
  expect(previousResponse.ok(), await previousResponse.text()).toBeTruthy();
  const previousPreview = await previousResponse.json();
  const created = await request.post("/api/visual-standards", { data: {
    id: randomUUID(), name: `이전 별첨 예시 표준 ${Date.now()}`, settings: previousSettings, seed_base64: seed,
    settings_sha256: previousPreview.settings_sha256, preview_sha256: previousPreview.preview_sha256,
    base_standard: previousPreview.base_standard,
  } });
  expect(created.ok(), await created.text()).toBeTruthy();
  const saved = await created.json();
  expect(saved.revision).toBe(3);
  expect(saved.settings).not.toHaveProperty("preview_layout_policy");
  expect(saved.settings).not.toHaveProperty("body_parenthetical_scope");
  const frozenFile = await (await request.get(`/api/visual-standards/${saved.id}/file`)).body();
  await documentListButton(page).click();
  await openDesigner(page);
  const modifiedSize = catalog.defaults.body_size_pt + 1;
  await setCatalogField(page, catalog, "body_size_pt", modifiedSize);
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  const before = posted.at(-1).settings;
  const summary = page.getByText("저장한 표준에서 시작", { exact: true });
  await expect(page.getByLabel("저장한 표준", { exact: true })).toBeHidden();
  await summary.click();
  await page.getByLabel("저장한 표준", { exact: true }).selectOption(saved.id);
  await expect(page.getByLabel(fieldLabel(catalog, "body_size_pt"), { exact: true })).toHaveValue(String(modifiedSize));
  await page.getByRole("button", { name: "불러오기", exact: true }).click();
  const confirm = page.getByRole("alertdialog", { name: "서식 설정 바꾸기", exact: true });
  await expect(confirm).toBeVisible();
  await confirm.getByRole("button", { name: "계속 편집", exact: true }).click();
  await expect(page.getByLabel(fieldLabel(catalog, "body_size_pt"), { exact: true })).toHaveValue(String(modifiedSize));
  expect(posted.at(-1).settings).toEqual(before);
  await page.getByRole("button", { name: "불러오기", exact: true }).click();
  await expect(confirm).toBeVisible();
  const loaded = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/sample-preview" &&
    value.request().postDataJSON().settings.body_size_pt === previousSettings.body_size_pt &&
    !("preview_layout_policy" in value.request().postDataJSON().settings));
  await confirm.getByRole("button", { name: "설정 바꾸기", exact: true }).click();
  expect((await loaded).ok()).toBeTruthy();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  expect(posted.at(-1).settings).toEqual(saved.settings);
  expect(posted.at(-1).settings).not.toHaveProperty("body_parenthetical_scope");
  expect(hash(await (await request.get(`/api/visual-standards/${saved.id}/file`)).body())).toBe(hash(frozenFile));
  await writeFile(info.outputPath("saved-settings-explicit-load.json"), JSON.stringify({ actual_model_calls: 0,
    choice_kept_unsaved_settings: true, cancel_kept_unsaved_settings: true, confirmed_load: saved.id,
    prior_policy_remains_absent: true, prior_parenthetical_scope_remains_absent: true,
    frozen_settings_sha256: saved.settings_sha256, frozen_file_sha256: hash(frozenFile) }, null, 2));
});
test("REAL rhwp standard designer: dirty prompts preserve invalid edits and recognize normalized saved settings", async ({ page, request }) => {
  test.setTimeout(240000);
  await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const size = page.getByLabel(fieldLabel(catalog, "body_size_pt"), { exact: true });
  const leave = page.getByRole("alertdialog", { name: "서식 설정을 닫을까요?", exact: true });
  await openGroup(page, catalog.fields.find((field: any) => field.key === "body_size_pt").group);
  await size.fill("");
  await documentListButton(page).click();
  await expect(leave).toBeVisible();
  await leave.getByRole("button", { name: "계속 편집", exact: true }).click();
  await expect(size).toHaveValue("");

  // A different numeric spelling of the same default is not an unsaved edit.
  await size.fill(Number(catalog.defaults.body_size_pt).toFixed(1));
  await expectDesignerReady(page);
  await documentListButton(page).click();
  await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
  await expect(leave).toBeHidden();

  await openDesigner(page);
  const changedSize = Number(catalog.defaults.body_size_pt) + 1;
  await setCatalogField(page, catalog, "body_size_pt", changedSize.toFixed(1));
  await expectDesignerReady(page);
  await documentListButton(page).click();
  await expect(leave).toBeVisible();
  await leave.getByRole("button", { name: "계속 편집", exact: true }).click();
  await registerButton(page).click();
  const name = `수치 정규화 표준 ${Date.now()}`;
  await page.getByLabel("표준 이름", { exact: true }).fill(name);
  const registration = page.waitForResponse(response => response.request().method() === "POST" &&
    new URL(response.url()).pathname === "/api/visual-standards/sample-register");
  await submitRegistration(page).click();
  const response = await registration;
  expect(response.ok(), await response.text()).toBeTruthy();
  expect((await response.json()).settings.body_size_pt).toBe(changedSize);
  await expect(page.getByRole("heading", { name: `‘${name}’ 등록 완료`, exact: true })).toBeVisible();
  await expect(size).toHaveValue(changedSize.toFixed(1));
  await documentListButton(page).click();
  await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
  await expect(leave).toBeHidden();
});

async function previewImageHashes(page: Page, outputPath?: (index: number) => string) {
  const hashes: string[] = [];
  await forEachPreviewPage(page, async index => {
    const image = page.locator(".standard-preview-pages figure img").first();
    hashes.push(hash(await image.screenshot(outputPath ? { path: outputPath(index) } : {})));
  });
  return hashes;
}
async function forEachPreviewPage(page: Page, visit: (index: number) => Promise<void>) {
  const picker = page.getByLabel("미리보기 쪽", { exact: true });
  const previous = await picker.inputValue();
  const values = await picker.locator("option").evaluateAll(options => options.map(option => (option as HTMLOptionElement).value));
  expect(values.length).toBeGreaterThan(0);
  try {
    for (const value of values) {
      await picker.selectOption(value);
      await expect(page.locator(".standard-preview-pages figure")).toHaveCount(1);
      await expect(page.locator(".standard-preview-pages figure")).toHaveAttribute("data-page-index", String(Number(value) - 1));
      await expect.poll(() => page.locator(".standard-preview-pages figure img").evaluate(image => (image as HTMLImageElement).complete)).toBe(true);
      await visit(Number(value) - 1);
    }
  } finally { await picker.selectOption(previous); }
}
async function previewSvgPages(page: Page): Promise<string[]> {
  const svgs: string[] = [];
  await forEachPreviewPage(page, async index => {
    svgs[index] = await page.locator(".standard-preview-pages figure img").evaluate(async image => {
      const blob = ((window as any).__standardTestBlobs as Map<string, Blob>).get((image as HTMLImageElement).src);
      if (!blob) throw new Error("현재 미리보기 쪽의 실제 SVG가 없습니다.");
      return blob.text();
    });
  });
  return svgs;
}
async function expectWholePreviewPage(page: Page) {
  const pane = page.locator(".standard-preview-pages"), image = pane.locator("figure img");
  await expect(image).toHaveCount(1);
  await expect.poll(async () => {
    const sheet = (await image.boundingBox())!, area = (await pane.boundingBox())!;
    return sheet.width > 100 && sheet.height > 100 && sheet.x >= area.x - 1 && sheet.y >= area.y - 1 &&
      sheet.x + sheet.width <= area.x + area.width + 1 && sheet.y + sheet.height <= area.y + area.height + 1;
  }, { message: "The entire natural page must fit inside the preview in both dimensions" }).toBe(true);
  const overflow = await pane.evaluate(element => ({ horizontal: element.scrollWidth - element.clientWidth,
    vertical: element.scrollHeight - element.clientHeight }));
  expect(overflow.horizontal).toBeLessThanOrEqual(1);
  expect(overflow.vertical).toBeLessThanOrEqual(1);
  await expectOuterViewportContained(page);
  return { pane: await pane.boundingBox(), page: await image.boundingBox(), overflow };
}
async function observeSharedSample(page: Page) {
  const replies: any[] = [], requests: { path: string; body: any }[] = [];
  await page.addInitScript(() => {
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL), blobs = new Map<string, Blob>();
    (window as any).__standardTestBlobs = blobs;
    URL.createObjectURL = object => { const url = create(object); if (object instanceof Blob && object.type === "image/svg+xml") blobs.set(url, object); return url; };
    URL.revokeObjectURL = url => { revoke(url); blobs.delete(url); };
  });
  page.on("request", value => {
    const path = new URL(value.url()).pathname;
    if (value.method() === "POST" && path.startsWith("/api/visual-standards/sample-")) requests.push({ path, body: value.postDataJSON() });
  });
  page.on("response", async value => {
    const path = new URL(value.url()).pathname;
    if (value.request().method() === "POST" && path.startsWith("/api/visual-standards/sample-preview") && value.ok()) {
      try { replies.push(await value.json()); }
      catch { /* A superseded preview can be aborted after its response headers. */ }
    }
  });
  return { replies, requests };
}
async function sharedSampleSnapshot(page: Page, observed: Awaited<ReturnType<typeof observeSharedSample>>) {
  await expectDesignerReady(page);
  const display = page.locator(".standard-preview-pages");
  await expect(display).toHaveAttribute("aria-busy", "false");
  const sha = await display.getAttribute("data-preview-sha256");
  await expect.poll(() => observed.replies.some(item => item.phase === "ready" && item.preview_sha256 === sha)).toBe(true);
  const receipt = [...observed.replies].reverse().find(item => item.phase === "ready" && item.preview_sha256 === sha);
  await expect(display).toHaveAttribute("data-sample-id", receipt.sample_id);
  await expect(display).toHaveAttribute("data-sample-sha256", receipt.sample_sha256);
  const frame = await (await page.locator(".standard-renderer iframe").elementHandle())!.contentFrame();
  if (!frame) throw new Error("합성 예시의 실제 한글 엔진이 없습니다.");
  const bytes = Buffer.from(await frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]));
  expect(hash(bytes)).toBe(receipt.preview_sha256);
  expect(await frame.evaluate(() => (window as any).hwpAssistantBridge.documentState().pageCount)).toBe(receipt.page_count);
  const svgs = await previewSvgPages(page);
  expect(svgs.length).toBe(receipt.page_count);
  const archive = unzipSync(bytes);
  const content = await page.evaluate(({ sections, header, svgs }) => {
    const parser = new DOMParser(), normalize = (value: string) => value.replace(/\s/g, "");
    const documents = sections.map(xml => parser.parseFromString(xml, "application/xml"));
    const paragraphs = documents.flatMap(document => Array.from(document.getElementsByTagNameNS("*", "p")));
    const textOf = (node: Element) => Array.from(node.getElementsByTagNameNS("*", "t")).map(text => text.textContent || "").join("");
    const text = paragraphs.map(textOf);
    const fonts = parser.parseFromString(header, "application/xml");
    const body = paragraphs.find(node => textOf(node).includes("이 문서는 서식 비교를 위한 고정 합성 업무 자료"));
    const charId = body?.getElementsByTagNameNS("*", "run")[0]?.getAttribute("charPrIDRef");
    const char = Array.from(fonts.getElementsByTagNameNS("*", "charPr")).find(node => node.getAttribute("id") === charId);
    const fontId = char?.getElementsByTagNameNS("*", "fontRef")[0]?.getAttribute("hangul");
    const family = Array.from(fonts.getElementsByTagNameNS("*", "fontface")).find(node => node.getAttribute("lang") === "HANGUL");
    const face = family && Array.from(family.getElementsByTagNameNS("*", "font")).find(node => node.getAttribute("id") === fontId)?.getAttribute("face");
    const rendered = svgs.map(svg => normalize(Array.from(parser.parseFromString(svg, "image/svg+xml").querySelectorAll("text"))
      .map(node => node.textContent || "").join("")));
    return { paragraphs: text, text: normalize(text.join("\n")), rendered, body_font: face || null };
  }, { sections: Object.keys(archive).filter(key => /^Contents\/section\d+\.xml$/.test(key)).sort().map(key => strFromU8(archive[key])),
    header: strFromU8(archive["Contents/header.xml"]), svgs });
  return { receipt, bytes, svgs, content, frame };
}
async function changeSharedSample(page: Page, change: () => Promise<unknown>, matches: (settings: any) => boolean = () => true) {
  const prepared = page.waitForResponse(value => value.request().method() === "POST" &&
    new URL(value.url()).pathname === "/api/visual-standards/sample-preview" && matches(value.request().postDataJSON().settings));
  await change();
  const response = await prepared;
  expect(response.ok(), await response.text()).toBeTruthy();
  const receipt = await response.json();
  await expectDesignerReady(page);
  await expect(page.locator(".standard-preview-pages")).toHaveAttribute("data-settings-sha256", receipt.settings_sha256, { timeout: 90000 });
  await expect(page.locator(".standard-preview-pages")).toHaveAttribute("aria-busy", "false");
}
const sampleFacts = [
  "합성 점검 대상 20건 중 5건의 필수 항목을 확인함.",
  "나머지 15건은 동일한 기준으로 점검할 예정임.",
  "완료 여부(필수 항목 충족 시)를 기록함.",
  "2026년 10월 30일까지 합성 점검 대상 20건의 확인을 완료할 계획임.",
  "2026년 10월 23일 기준 완료 5건과 미완료 15건",
  "담당별 정비 항목", "문서명·부서 확인", "검토 결과 기록", "합성 문서 점검 기준", "합성 검토 기록 기준",
];
function expectSharedSampleFacts(snapshot: Awaited<ReturnType<typeof sharedSampleSnapshot>>) {
  for (const fact of sampleFacts) {
    const normalized = fact.replace(/\s/g, "");
    expect(snapshot.content.text, `Saved HWPX includes ${fact}`).toContain(normalized);
    expect(snapshot.content.rendered.join(""), `Actual SVG includes ${fact}`).toContain(normalized);
  }
  expect(snapshot.receipt.sample_id).toBe("standard-business-report-v2");
  expect(snapshot.receipt.sample_sha256).toMatch(/^[0-9a-f]{64}$/);
}
test("REAL rhwp standard designer: shared sample keeps facts across three compositions and saved settings", async ({ page, request }, info) => {
  test.setTimeout(240000);
  await page.setViewportSize({ width: 1366, height: 900 });
  const observed = await observeSharedSample(page), modelPosts: string[] = [];
  page.on("request", value => { if (value.method() === "POST" && /\/(report-tasks|assistant-turns|rewrite-tasks)$/.test(new URL(value.url()).pathname)) modelPosts.push(value.url()); });
  await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const original = await (await request.get("/api/document-standard")).json();
  const originalSha = hash(await (await request.get(original.reference_url)).body());
  const proof: any[] = [];
  let snapshot = await sharedSampleSnapshot(page, observed);
  const sampleSha = snapshot.receipt.sample_sha256;
  for (const preset of ["appendix-1", "appendix-2", "appendix-3"]) {
    if (preset !== "appendix-1") {
      await changeSharedSample(page, () => page.getByLabel("기본 양식", { exact: true }).selectOption(preset), settings => settings.preset_id === preset);
      snapshot = await sharedSampleSnapshot(page, observed);
    }
    expectSharedSampleFacts(snapshot);
    expect(snapshot.receipt.sample_sha256).toBe(sampleSha);
    expect(snapshot.receipt.needs_toc).toBe(preset !== "appendix-1");
    const tocPage = snapshot.content.rendered.findIndex(text => text.includes("<목차>"));
    const bodyPage = snapshot.content.rendered.findIndex(text => text.includes("합성점검대상20건중5건의필수항목을확인함."));
    expect(bodyPage).toBeGreaterThanOrEqual(0);
    if (preset === "appendix-1") expect(tocPage).toBe(-1);
    else {
      expect(tocPage).toBe(preset === "appendix-2" ? 1 : 0);
      expect(bodyPage).toBeGreaterThan(tocPage);
      expect(snapshot.receipt.verification.converged).toBe(true);
      const frozen = JSON.parse(Buffer.from(snapshot.receipt.context.split(".")[0], "base64url").toString());
      const verification = [...observed.requests].reverse().find(item => item.path.endsWith("/verify") && item.body.layout_evidence &&
        JSON.parse(Buffer.from(item.body.context.split(".")[0], "base64url").toString()).id === frozen.id)!.body.layout_evidence;
      const current = await snapshot.frame.evaluate(headings => (window as any).hwpAssistantBridge.layoutEvidence(headings), verification.headings);
      expect(current.export_sha256).toBe(snapshot.receipt.preview_sha256);
      expect(current.page_count).toBe(snapshot.receipt.page_count);
      expect(current.headings).toEqual(verification.headings);
    }
    for (const [index, svg] of snapshot.svgs.entries()) {
      await writeFile(info.outputPath(`shared-${preset}-page-${index + 1}.svg`), svg);
      await measureSampleSvg(page, svg, info.outputPath(`shared-${preset}-page-${index + 1}.png`));
    }
    proof.push({ preset, sample_sha256: sampleSha, pages: snapshot.receipt.page_count, body_page: bodyPage + 1,
      toc_page: tocPage < 0 ? null : tocPage + 1, preview_sha256: snapshot.receipt.preview_sha256,
      toc_converged: snapshot.receipt.verification?.converged ?? null });
  }
  const registration = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/sample-register");
  await registerButton(page).click();
  await page.getByLabel("표준 이름", { exact: true }).fill(`같은 내용 별첨 비교 ${Date.now()}`);
  await submitRegistration(page).click();
  const response = await registration;
  expect(response.ok(), await response.text()).toBeTruthy();
  const saved = await response.json();
  expect(saved.settings).toEqual({ ...catalog.defaults, preset_id: "appendix-3" });
  const registeredBytes = await (await request.get(`/api/visual-standards/${saved.id}/file`)).body();
  expect(hash(registeredBytes)).toBe(snapshot.receipt.preview_sha256);
  await page.getByRole("tab", { name: "기준서 전체", exact: true }).click();
  await expect(page.getByRole("img", { name: "기준서 1쪽", exact: true })).toBeVisible({ timeout: 90000 });
  await expect(page.locator(".standard-preview-pages")).toHaveAttribute("aria-busy", "false");
  await page.getByRole("tab", { name: "서식 미리보기", exact: true }).click();
  await expect(page.locator(".standard-preview-pages")).toHaveAttribute("data-preview-sha256", saved.preview_sha256);
  await expect(page.locator(".standard-preview-pages")).toHaveAttribute("aria-busy", "false");
  await expect(page.getByRole("button", { name: "등록 완료", exact: true })).toBeDisabled();
  await documentListButton(page).click();
  await openDesigner(page);
  await changeSharedSample(page, () => loadSavedStandard(page, saved.id), settings => settings.preset_id === "appendix-3");
  const reloaded = await sharedSampleSnapshot(page, observed);
  expectSharedSampleFacts(reloaded);
  expect(reloaded.receipt.sample_sha256).toBe(sampleSha);
  expect(reloaded.receipt.settings_sha256).toBe(saved.settings_sha256);
  expect(hash(await (await request.get(`/api/visual-standards/${saved.id}/file`)).body())).toBe(hash(registeredBytes));
  expect(hash(await (await request.get(original.reference_url)).body())).toBe(originalSha);
  expect(modelPosts).toEqual([]);
  await writeFile(info.outputPath("shared-sample-composition-proof.json"), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    fixed_sample_id: snapshot.receipt.sample_id, presets: proof, saved_id: saved.id,
    saved_file_sha256: hash(registeredBytes), original_sha256: originalSha, original_and_saved_files_preserved: true,
    saved_sample_return_from_reference_is_ready: true }, null, 2));
});

test("REAL rhwp standard designer: shared sample composition controls change actual content without losing facts", async ({ page, request }, info) => {
  test.setTimeout(240000);
  const observed = await observeSharedSample(page);
  await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const before = await sharedSampleSnapshot(page, observed);
  expectSharedSampleFacts(before);
  expect(before.content.text).not.toContain("핵심요약");
  expect(before.content.text).not.toContain("(현황)");
  expect(before.content.text).not.toContain("(핵심)");
  await changeSharedSample(page, async () => {
    await setCatalogField(page, catalog, "show_core_summary", true);
    await setCatalogField(page, catalog, "body_parenthetical_labels", true);
  }, settings => settings.show_core_summary && settings.body_parenthetical_labels);
  const enabled = await sharedSampleSnapshot(page, observed);
  expectSharedSampleFacts(enabled);
  for (const text of ["핵심요약", "(요약)", "(현황)", "(핵심)"])
    expect(enabled.content.rendered.join("")).toContain(text);
  expect(enabled.content.text).toContain("완료여부(필수항목충족시)를기록함.");
  const markers = { numbered_1_marker: "[1]", numbered_2_marker: "{가}", numbered_3_marker: "1)",
    numbered_4_marker: "가)", numbered_5_marker: "(①)", numbered_6_marker: "(㉮)" };
  await changeSharedSample(page, async () => {
    for (const [key, value] of Object.entries(markers)) await setCatalogField(page, catalog, key, value);
    await setCatalogField(page, catalog, "numbering_style", "NUMBERED");
  }, settings => settings.numbering_style === "NUMBERED" && Object.entries(markers).every(([key, value]) => settings[key] === value));
  const numbered = await sharedSampleSnapshot(page, observed);
  expectSharedSampleFacts(numbered);
  for (const text of ["[1] 정비 필요성", "{가} 적용 범위", "1) 검토 대상", "가) 자료 분류", "(①) 확인 순서", "(㉮) 세부 기준",
    "{가} (현황) 합성 점검 대상 20건", "1) (조치) 작성 담당자"])
    expect(numbered.content.rendered.join("")).toContain(text.replace(/\s/g, ""));
  await changeSharedSample(page, async () => {
    await setCatalogField(page, catalog, "show_core_summary", false);
    await setCatalogField(page, catalog, "body_parenthetical_labels", false);
  }, settings => !settings.show_core_summary && !settings.body_parenthetical_labels);
  const disabled = await sharedSampleSnapshot(page, observed);
  expectSharedSampleFacts(disabled);
  for (const text of ["핵심요약", "(요약)", "(현황)", "(핵심)"])
    expect(disabled.content.text).not.toContain(text);
  expect(disabled.content.text).toContain("완료여부(필수항목충족시)를기록함.");
  for (const snapshot of [enabled, numbered, disabled]) expect(snapshot.receipt.sample_sha256).toBe(before.receipt.sample_sha256);
  await writeFile(info.outputPath("shared-sample-controls-proof.json"), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    sample_sha256: before.receipt.sample_sha256, markers, six_numbering_levels_rendered: true,
    summary_and_labels_toggle: true, facts_and_parenthetical_condition_preserved: true,
    snapshots: [before, enabled, numbered, disabled].map(item => ({ preview_sha256: item.receipt.preview_sha256, pages: item.receipt.page_count, paragraphs: item.content.paragraphs })) }, null, 2));
});

test("REAL rhwp standard designer: selected parenthetical scopes preserve facts and survive registration", async ({ page, request }, info) => {
  test.setTimeout(240000);
  const observed = await observeSharedSample(page);
  await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  expect(catalog.defaults).not.toHaveProperty("body_parenthetical_scope");
  await changeSharedSample(page, async () => {
    await setCatalogField(page, catalog, "show_core_summary", true);
    await setCatalogField(page, catalog, "body_parenthetical_labels", true);
  }, settings => settings.show_core_summary && settings.body_parenthetical_labels);
  const initial = await sharedSampleSnapshot(page, observed);
  expect(observed.requests.filter(item => item.path.endsWith("/sample-preview")).at(-1)!.body.settings)
    .not.toHaveProperty("body_parenthetical_scope");
  const scopeOrder = ["square", "circle", "dash", "dot", "body", "summary"];
  const targets = [
    { scope: "square", label: "현황", text: "합성 점검 대상 20건 중 5건의 필수 항목을 확인함." },
    { scope: "circle", label: "조치", text: "작성 담당자가 문서명과 담당 부서를 확인하고 검토 담당자가 확인 결과를 기록함." },
    { scope: "dash", label: "결론", text: "필수 항목이 모두 확인된 자료만 정비 완료로 분류함." },
    { scope: "dot", label: "핵심", text: "확인 일자와 담당자를 함께 기록함." },
    { scope: "body", label: "목적", text: "이 문서는 서식 비교를 위한 고정 합성 업무 자료이며 실제 업무 실적을 나타내지 않음." },
    { scope: "summary", label: "요약", text: "문서 수집과 검토 절차를 정비하여 자료의 누락을 줄이고 담당자의 확인 기준을 통일함." },
  ];
  const citationText = (snapshot: Awaited<ReturnType<typeof sharedSampleSnapshot>>) => snapshot.content.paragraphs
    .filter((text: string) => text.includes("https://example.invalid/")).sort();
  expect(citationText(initial).length).toBeGreaterThan(0);
  const verifyScopes = (snapshot: Awaited<ReturnType<typeof sharedSampleSnapshot>>, selected: string[], master = true) => {
    expectSharedSampleFacts(snapshot);
    expect(snapshot.receipt.sample_sha256).toBe(initial.receipt.sample_sha256);
    expect(citationText(snapshot)).toEqual(citationText(initial));
    for (const target of targets) {
      const text = target.text.replace(/\s/g, ""), labeled = `(${target.label})${text}`;
      for (const source of [snapshot.content.text, snapshot.content.rendered.join("")]) {
        expect(source).toContain(text);
        if (master && selected.includes(target.scope)) expect(source, `${target.scope} label is selected`).toContain(labeled);
        else expect(source, `${target.scope} label is not selected`).not.toContain(labeled);
      }
    }
    expect(snapshot.content.text).toContain("완료여부(필수항목충족시)를기록함.");
  };
  verifyScopes(initial, scopeOrder);
  const scopeGroup = page.getByRole("group", { name: "괄호 요약어 표시 위치", exact: true });
  const snapshots: any[] = [];
  const chooseScopes = async (selected: string[]) => {
    await openGroup(page, catalog.fields.find((field: any) => field.key === "body_parenthetical_labels").group);
    for (const key of scopeOrder) await scopeGroup.locator(`input[value="${key}"]`).setChecked(selected.includes(key));
  };
  for (const selected of [["square", "dot", "body"], ["circle", "dash", "summary"], []]) {
    const csv = scopeOrder.filter(key => selected.includes(key)).join(",");
    await changeSharedSample(page, () => chooseScopes(selected), settings => settings.body_parenthetical_scope === csv);
    const snapshot = await sharedSampleSnapshot(page, observed);
    verifyScopes(snapshot, selected);
    snapshots.push({ scope: csv, master: true, preview_sha256: snapshot.receipt.preview_sha256, paragraphs: snapshot.content.paragraphs });
  }
  await changeSharedSample(page, async () => {
    await chooseScopes(scopeOrder);
    await setCatalogField(page, catalog, "body_parenthetical_labels", false);
  }, settings => settings.body_parenthetical_scope === scopeOrder.join(",") && !settings.body_parenthetical_labels);
  const masterOff = await sharedSampleSnapshot(page, observed);
  verifyScopes(masterOff, scopeOrder, false);
  for (const key of scopeOrder) {
    await expect(scopeGroup.locator(`input[value="${key}"]`)).toBeChecked();
    await expect(scopeGroup.locator(`input[value="${key}"]`)).toBeDisabled();
  }
  await changeSharedSample(page, () => setCatalogField(page, catalog, "body_parenthetical_labels", true),
    settings => settings.body_parenthetical_scope === scopeOrder.join(",") && settings.body_parenthetical_labels);
  const all = await sharedSampleSnapshot(page, observed);
  verifyScopes(all, scopeOrder);
  // Persist a partial choice, so reopening cannot silently turn it into all.
  const finalScope = ["circle", "dash", "summary"];
  await changeSharedSample(page, () => chooseScopes(finalScope), settings => settings.body_parenthetical_scope === finalScope.join(","));
  const selected = await sharedSampleSnapshot(page, observed);
  verifyScopes(selected, finalScope);
  await writeFile(info.outputPath("selected-scope-sample.hwpx"), selected.bytes);
  await page.screenshot({ path: info.outputPath("selected-scope-sample.png"), fullPage: true });
  const registering = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/sample-register");
  await registerButton(page).click();
  await page.getByLabel("표준 이름", { exact: true }).fill(`요약어 범위 검증 ${Date.now()}`);
  await submitRegistration(page).click();
  const saved = await (await registering).json();
  expect(saved.settings.body_parenthetical_scope).toBe(finalScope.join(","));
  expect(saved.preview_sha256).toBe(selected.receipt.preview_sha256);
  const frozen = await (await request.get(`/api/visual-standards/${saved.id}/file`)).body();
  expect(hash(frozen)).toBe(saved.preview_sha256);
  await documentListButton(page).click();
  await openDesigner(page);
  await loadSavedStandard(page, saved.id);
  const reloaded = await sharedSampleSnapshot(page, observed);
  verifyScopes(reloaded, finalScope);
  expect(reloaded.receipt.settings_sha256).toBe(saved.settings_sha256);
  expect(hash(await (await request.get(`/api/visual-standards/${saved.id}/file`)).body())).toBe(hash(frozen));
  await writeFile(info.outputPath("parenthetical-scope-proof.json"), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    sample_sha256: initial.receipt.sample_sha256, independent_scope_pairs: snapshots, master_off_hides_all_without_losing_choices: true,
    facts_conditions_citations_preserved: true, saved_id: saved.id, settings_sha256: saved.settings_sha256,
    frozen_file_sha256: hash(frozen), reloaded_scope: finalScope.join(",") }, null, 2));
});

test("REAL rhwp standard designer: shared sample fonts, real target highlights and both panes stay usable", async ({ page, request }, info) => {
  test.setTimeout(240000);
  await page.setViewportSize({ width: 876, height: 730 });
  const observed = await observeSharedSample(page);
  await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const layouts: any[] = [];
  const zoom = page.getByRole("group", { name: "미리보기 확대", exact: true });
  await expect(zoom.getByRole("button", { name: "한 쪽 보기", exact: true })).toHaveAttribute("aria-pressed", "true");
  for (const [width, height] of [[876, 730], [1024, 768], [1366, 900]]) {
    await page.setViewportSize({ width, height });
    const settings = page.getByRole("complementary", { name: "문서 표준 설정", exact: true });
    const preview = page.getByRole("region", { name: "문서 표준 실제 미리보기", exact: true });
    await expect(settings).toBeVisible();
    await expect(preview).toBeVisible();
    const left = (await settings.boundingBox())!, right = (await preview.boundingBox())!;
    expect(left.width).toBeGreaterThanOrEqual(250);
    expect(right.width).toBeGreaterThanOrEqual(360);
    expect(right.x).toBeCloseTo(left.x + left.width, 1);
    await expectOuterViewportContained(page);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    layouts.push({ width, height, settings: left, preview: right, full_page: await expectWholePreviewPage(page) });
    await page.screenshot({ path: info.outputPath(`whole-page-${width}x${height}.png`), fullPage: true });
  }
  const picker = page.getByLabel("미리보기 쪽", { exact: true });
  await changeSharedSample(page, () => page.getByLabel("기본 양식", { exact: true }).selectOption("appendix-2"),
    settings => settings.preset_id === "appendix-2");
  const expandedPages = await picker.locator("option").count();
  expect(expandedPages).toBeGreaterThan(2);
  await picker.selectOption(String(expandedPages));
  await expect(page.getByRole("button", { name: "다음 쪽", exact: true })).toBeDisabled();
  await expectWholePreviewPage(page);
  await page.getByRole("button", { name: "이전 쪽", exact: true }).click();
  await expect(picker).toHaveValue(String(expandedPages - 1));
  await page.getByRole("button", { name: "다음 쪽", exact: true }).click();
  await expect(picker).toHaveValue(String(expandedPages));
  await changeSharedSample(page, () => page.getByLabel("기본 양식", { exact: true }).selectOption("appendix-1"),
    settings => settings.preset_id === "appendix-1");
  const reducedPages = await picker.locator("option").count();
  expect(reducedPages).toBeLessThan(expandedPages);
  await expect(picker).toHaveValue(String(reducedPages));
  await expectWholePreviewPage(page);
  await picker.selectOption("1");
  await expect(page.getByRole("button", { name: "이전 쪽", exact: true })).toBeDisabled();
  const bodyField = catalog.fields.find((field: any) => field.key === "body_font");
  await openGroup(page, bodyField.group);
  const font = page.getByRole("combobox", { name: fieldLabel(catalog, "body_font"), exact: true });
  await expect(font).toHaveValue(catalog.defaults.body_font);
  for (const family of [catalog.defaults.body_font, "Noto Serif KR", "Nanum Gothic"])
    await expect(font.locator("option").filter({ hasText: new RegExp(`^${family}$`) })).toHaveCount(1);
  const requestsBeforeCustom = observed.requests.length;
  await font.selectOption("__custom__");
  await expect(page.getByLabel(`${fieldLabel(catalog, "body_font")} 직접 입력`, { exact: true })).toHaveValue(catalog.defaults.body_font);
  expect(observed.requests.length).toBe(requestsBeforeCustom);
  const fontProof: any[] = [];
  let snapshot: Awaited<ReturnType<typeof sharedSampleSnapshot>>;
  for (const family of ["Noto Serif KR", "Nanum Gothic"]) {
    await changeSharedSample(page, () => font.selectOption(family), settings => settings.body_font === family);
    snapshot = await sharedSampleSnapshot(page, observed);
    expectSharedSampleFacts(snapshot);
    expect(snapshot.content.body_font).toBe(family);
    const faces = await page.evaluate(({ svgs, family }) => svgs.map(svg => {
      const document = new DOMParser().parseFromString(svg, "image/svg+xml");
      return { embedded: Array.from(document.querySelectorAll("style")).some(style =>
        style.textContent?.includes(`font-family:'${family}'`) && style.textContent.includes("data:font/woff2;base64,")),
        text_family: Array.from(document.querySelectorAll("text")).some(node => node.getAttribute("font-family")?.includes(family)) };
    }), { svgs: snapshot.svgs, family });
    expect(faces.some(face => face.embedded && face.text_family)).toBe(true);
    fontProof.push({ family, hwpx_body_font: snapshot.content.body_font, embedded_svg_font: true,
      preview_sha256: snapshot.receipt.preview_sha256 });
  }
  const current = snapshot!;
  const geometry: SampleGeometry[] = [];
  for (const [index, svg] of current.svgs.entries()) geometry.push(await measureSampleSvg(page, svg, info.outputPath(`shared-font-page-${index + 1}.png`)));
  const interactionRequests = observed.requests.length, overlayProof: any[] = [];
  for (const target of [
    { key: "title_size_pt", role: "title", text: "문서 관리 개선 계획", trigger: "focus" },
    { key: "body_font", role: "body", text: "이 문서는 서식 비교를 위한 고정 합성 업무 자료이며 실제 업무 실적을 나타내지 않음.", trigger: "hover" },
    { key: "table_header_fill", role: "table", text: "완료 기준", trigger: "focus" },
  ]) {
    await picker.selectOption(String(await picker.locator("option").count()));
    const pageBefore = await picker.inputValue();
    const field = catalog.fields.find((field: any) => field.key === target.key);
    await openGroup(page, field.group);
    const control = page.getByLabel(fieldLabel(catalog, target.key), { exact: true });
    await page.mouse.move(1350, 20);
    if (target.trigger === "hover") await control.hover(); else await control.focus();
    await expect(page.locator(".standard-setting-impact")).toHaveAttribute("data-setting-key", target.key);
    const windowBefore = await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }));
    const locate = page.getByRole("button", { name: "예시에서 위치 보기", exact: true });
    await locate.hover();
    await expect(page.locator(".standard-setting-impact")).toHaveAttribute("data-setting-key", target.key);
    await locate.click();
    const rectangles = page.locator(`.standard-region-highlight rect[data-setting-key="${target.key}"][data-target-role="${target.role}"]`);
    await expect.poll(() => rectangles.count()).toBeGreaterThan(0);
    const actual = await rectangles.evaluateAll(nodes => nodes.map(node => ({ page: Number(node.getAttribute("data-page-index")),
      left: Number(node.getAttribute("x")), top: Number(node.getAttribute("y")),
      right: Number(node.getAttribute("x")) + Number(node.getAttribute("width")),
      bottom: Number(node.getAttribute("y")) + Number(node.getAttribute("height")) })));
    const normalized = target.text.replace(/\s/g, ""), pageIndex = geometry.findIndex(value => value.text.includes(normalized));
    expect(pageIndex, `${target.key} target exists in actual SVG`).toBeGreaterThanOrEqual(0);
    const glyphs = sampleRange(geometry[pageIndex], target.text).glyphs;
    for (const glyph of glyphs) expect(actual.some(rect => rect.page === pageIndex && rect.left <= glyph.left + 2 &&
      rect.right >= glyph.right - 2 && rect.top <= glyph.top + 2 && rect.bottom >= glyph.bottom - 2), `${target.key} covers actual glyph ${glyph.text}`).toBe(true);
    expect(actual.every(rect => (rect.right - rect.left) * (rect.bottom - rect.top) < geometry[rect.page].width * geometry[rect.page].height * .5)).toBe(true);
    await expect(picker).toHaveValue(String(pageIndex + 1));
    const fitted = await expectWholePreviewPage(page);
    expect(await page.evaluate(() => ({ x: window.scrollX, y: window.scrollY }))).toEqual(windowBefore);
    overlayProof.push({ ...target, page: pageIndex + 1, rects: actual,
      location_button_keeps_target: true, page_before: pageBefore, page_after: await picker.inputValue(),
      outer_scroll_unchanged: true, full_page: fitted });
  }
  const sources = await page.locator(".standard-preview-pages figure img").evaluateAll(images => images.map(image => (image as HTMLImageElement).src));
  await zoom.getByRole("button", { name: "100%", exact: true }).click();
  for (const image of await page.locator(".standard-preview-pages figure img").all()) {
    const measured = await image.evaluate(element => ({ width: element.getBoundingClientRect().width, natural: (element as HTMLImageElement).naturalWidth }));
    expect(Math.abs(measured.width - measured.natural)).toBeLessThanOrEqual(1);
  }
  await zoom.getByRole("button", { name: "폭 맞춤", exact: true }).click();
  const pane = (await page.locator(".standard-preview-pages").boundingBox())!;
  for (const image of await page.locator(".standard-preview-pages figure img").all()) {
    const box = (await image.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(pane.x - 1);
    expect(box.x + box.width).toBeLessThanOrEqual(pane.x + pane.width + 1);
  }
  expect(await page.locator(".standard-preview-pages figure img").evaluateAll(images => images.map(image => (image as HTMLImageElement).src))).toEqual(sources);
  await zoom.getByRole("button", { name: "한 쪽 보기", exact: true }).click();
  await expectWholePreviewPage(page);
  expect(observed.requests.length).toBe(interactionRequests);
  expect(hash(Buffer.from(await current.frame.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[])))).toBe(current.receipt.preview_sha256);
  await changeSharedSample(page, () => setCatalogField(page, catalog, "margin_left_mm", 30), settings => settings.margin_left_mm === 30);
  const marginSample = await sharedSampleSnapshot(page, observed);
  expectSharedSampleFacts(marginSample);
  const section = strFromU8(unzipSync(marginSample.bytes)["Contents/section0.xml"]);
  const actualMargin = await page.evaluate(xml => {
    const document = new DOMParser().parseFromString(xml, "application/xml");
    const page = document.getElementsByTagNameNS("*", "pagePr")[0];
    return Number(page.getElementsByTagNameNS("*", "margin")[0].getAttribute("left"));
  }, section);
  expect(Math.abs(actualMargin - Math.round(30 * 7200 / 25.4))).toBeLessThanOrEqual(1);
  await page.getByLabel(fieldLabel(catalog, "margin_left_mm"), { exact: true }).hover();
  const marginRects = page.locator('.standard-region-highlight rect[data-setting-key="margin_left_mm"]');
  await expect.poll(() => marginRects.count()).toBeGreaterThan(0);
  const bands = await marginRects.evaluateAll(nodes => nodes.map(node => ({ page: Number(node.getAttribute("data-page-index")),
    x: Number(node.getAttribute("x")), y: Number(node.getAttribute("y")),
    width: Number(node.getAttribute("width")), height: Number(node.getAttribute("height")),
    page_width: (node as SVGElement).ownerSVGElement!.viewBox.baseVal.width,
    page_height: (node as SVGElement).ownerSVGElement!.viewBox.baseVal.height })));
  for (const band of bands) {
    expect(band.x).toBeCloseTo(0, 2);
    expect(band.y).toBeCloseTo(0, 2);
    expect(Math.abs(band.width - band.page_width * 30 / Number(catalog.defaults.page_width_mm))).toBeLessThanOrEqual(1);
    expect(band.height).toBeCloseTo(band.page_height, 2);
  }
  await page.setViewportSize({ width: 876, height: 730 });
  await expect(page.getByRole("complementary", { name: "문서 표준 설정", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "문서 표준 실제 미리보기", exact: true })).toBeVisible();
  await expectOuterViewportContained(page);
  await expectWholePreviewPage(page);
  await page.screenshot({ path: info.outputPath("shared-sample-designer-layout.png"), fullPage: true });
  await writeFile(info.outputPath("shared-sample-display-proof.json"), JSON.stringify({ real_engine: true, actual_model_calls: 0,
    layouts, pager: { expanded_pages: expandedPages, reduced_pages: reducedPages, clamped_after_settings_change: true },
    fonts: fontProof, highlights: overlayProof, hover_focus_zoom_do_not_rewrite_hwpx: true,
    left_margin_mm: 30, actual_hwpx_left_margin: actualMargin, margin_bands: bands,
    final_preview_sha256: marginSample.receipt.preview_sha256 }, null, 2));
});
test("REAL rhwp standard designer: locating a target stays stable after scrolling in actual and fit views", async ({ page, request }) => {
  await page.setViewportSize({ width: 876, height: 560 });
  await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const field = catalog.fields.find((item: any) => item.key === "table_header_fill");
  await openGroup(page, field.group);
  await page.getByLabel(fieldLabel(catalog, field.key), { exact: true }).focus();
  const locate = page.getByRole("button", { name: "예시에서 위치 보기", exact: true });
  await locate.click();
  const pane = page.locator(".standard-preview-pages");
  const highlight = pane.locator(`.standard-region-highlight rect[data-setting-key="${field.key}"]`).first();
  await expect(highlight).toBeVisible();
  const originalPage = await pane.getAttribute("data-page-index");
  const zoom = page.getByRole("group", { name: "미리보기 확대", exact: true });
  const scrollPosition = () => pane.evaluate(element => ({ top: element.scrollTop, left: element.scrollLeft }));

  for (const [mode, width] of [["100%", 876], ["폭 맞춤", 1366]] as const) {
    await page.setViewportSize({ width, height: 560 });
    await zoom.getByRole("button", { name: mode, exact: true }).click();
    await pane.evaluate(element => element.scrollTo(0, 0));
    await locate.click();
    const canonical = await scrollPosition();
    if (mode === "100%") expect(canonical.top > 0 || canonical.left > 0,
      "The target needs scrolling, so repeating locate cannot pass only by clamping to zero").toBe(true);
    const position = await highlight.boundingBox();
    expect(position).not.toBeNull();

    await pane.evaluate(element => element.scrollTo(element.scrollWidth, element.scrollHeight));
    expect((await scrollPosition()).top).toBeGreaterThan(0);
    for (let repeat = 0; repeat < 2; repeat++) {
      await locate.click();
      await expect.poll(async () => {
        const current = await scrollPosition();
        return Math.max(Math.abs(current.top - canonical.top), Math.abs(current.left - canonical.left));
      }).toBeLessThanOrEqual(1);
      await expect(pane).toHaveAttribute("data-page-index", originalPage!);
      const located = (await highlight.boundingBox())!, viewport = (await pane.boundingBox())!;
      expect(located.y).toBeCloseTo(position!.y, 0);
      expect(located.x).toBeCloseTo(position!.x, 0);
      expect(located.y).toBeGreaterThanOrEqual(viewport.y);
      expect(located.y + located.height).toBeLessThanOrEqual(viewport.y + viewport.height);
    }
  }
});

type SampleBox = { left: number; top: number; right: number; bottom: number };
type SampleGlyph = SampleBox & { text: string; start: number; end: number; baseline: number; clip: SampleBox | null };
type SampleGeometry = { width: number; height: number; text: string; glyphs: SampleGlyph[];
  lines: { x1: number; y1: number; x2: number; y2: number; stroke: string; dashed: boolean }[] };
async function renderOriginalExample(page: Page, bytes: Buffer): Promise<string[]> {
  // Original appendix fixtures remain a separate compatibility test after the
  // designer adopts one shared synthetic sample. Load the unchanged endpoint's
  // bytes into an isolated real SDK without replacing the designer's document.
  const laboratory = await page.context().newPage();
  const root = fileURLToPath(new URL("../../", import.meta.url));
  try {
    await laboratory.route("**/standard-original-lab/**", async route => {
      const name = new URL(route.request().url()).pathname.split("/").pop()!;
      if (name.endsWith(".js") && /^[a-z-]+\.js$/.test(name))
        return route.fulfill({ contentType: "application/javascript", body: await readFile(resolve(root, "frontend/node_modules/@rhwp/editor", name)) });
      return route.fulfill({ contentType: "text/html", body: '<html><body style="margin:0"><div id="editor" style="height:100vh"></div></body></html>' });
    });
    await laboratory.goto(new URL("/standard-original-lab/", page.url()).href);
    return await laboratory.evaluate(async encoded => {
      const { createEditor } = await import(/* @vite-ignore */ "/standard-original-lab/index.js");
      const sdk = await createEditor("#editor", { studioUrl: location.origin + "/rhwp/index.html?chrome=embed" });
      try {
        await sdk.loadFile(Uint8Array.from(atob(encoded), char => char.charCodeAt(0)).buffer, "original-appendix.hwpx", { suppressDialogs: true });
        const pages: string[] = [];
        for (let index = 0; index < await sdk.pageCount(); index++) pages.push(await sdk.getPageSvg(index));
        return pages;
      } finally { sdk.destroy(); }
    }, bytes.toString("base64"));
  } finally { await laboratory.close(); }
}
async function measureSampleSvg(page: Page, svg: string, screenshotPath: string): Promise<SampleGeometry> {
  // Measure the actual engine SVG in a separate read-only rendering surface.
  // Bounds assertions below apply only to the fixed source appendix examples,
  // never impose a new layout restriction on user documents or arbitrary text.
  const laboratory = await page.context().newPage();
  try {
    await laboratory.setContent(`<html><body style="margin:0;background:white">${svg}</body></html>`);
    const natural = await laboratory.locator("svg").evaluate(element => {
      const bounds = element.getBoundingClientRect();
      return { width: Math.ceil(bounds.width), height: Math.ceil(bounds.height) };
    });
    await laboratory.setViewportSize(natural);
    await laboratory.locator("svg").evaluate(element => { element.style.display = "block"; });
    await laboratory.evaluate(() => document.fonts.ready);
    // An image screenshot inside the application scroll pane can be clipped or
    // overlap another page. Render this exact engine SVG at its natural size so
    // the evidence contains the complete page without toolbar or pane clipping.
    await laboratory.screenshot({ path: screenshotPath, fullPage: true });
    return await laboratory.evaluate(async () => {
      await document.fonts.ready;
      const root = document.querySelector("svg")!;
      const viewBox = root.viewBox.baseVal, origin = root.getBoundingClientRect();
      const sx = viewBox.width / origin.width, sy = viewBox.height / origin.height;
      let offset = 0;
      const glyphs = Array.from(root.querySelectorAll("text")).map(node => {
        const text = (node.textContent || "").replace(/\s/g, ""), start = offset;
        offset += text.length;
        const box = node.getBoundingClientRect();
        const point = new DOMPoint(Number(node.getAttribute("x")), Number(node.getAttribute("y")))
          .matrixTransform(node.getCTM()!);
        const id = node.closest("[clip-path]")?.getAttribute("clip-path")?.match(/^url\(#([^)]*)\)$/)?.[1];
        const rect = id ? root.querySelector(`#${CSS.escape(id)} rect`) : null;
        const clip = rect ? { left: Number(rect.getAttribute("x")), top: Number(rect.getAttribute("y")),
          right: Number(rect.getAttribute("x")) + Number(rect.getAttribute("width")),
          bottom: Number(rect.getAttribute("y")) + Number(rect.getAttribute("height")) } : null;
        return { text, start, end: offset, left: (box.left - origin.left) * sx, top: (box.top - origin.top) * sy,
          right: (box.right - origin.left) * sx, bottom: (box.bottom - origin.top) * sy, baseline: point.y, clip };
      }).filter(glyph => glyph.text);
      const lines = Array.from(root.querySelectorAll("line")).map(line => ({
        x1: Number(line.getAttribute("x1")), y1: Number(line.getAttribute("y1")),
        x2: Number(line.getAttribute("x2")), y2: Number(line.getAttribute("y2")),
        stroke: line.getAttribute("stroke")?.toLowerCase() || "", dashed: line.hasAttribute("stroke-dasharray"),
      }));
      return { width: viewBox.width, height: viewBox.height, text: glyphs.map(glyph => glyph.text).join(""), glyphs, lines };
    });
  } finally { await laboratory.close(); }
}
function sampleRange(geometry: SampleGeometry, text: string, after = 0) {
  const normalized = text.replace(/\s/g, ""), start = geometry.text.indexOf(normalized, after);
  expect(start, `Fixed example text is present: ${text}`).toBeGreaterThanOrEqual(0);
  const end = start + normalized.length;
  const glyphs = geometry.glyphs.filter(glyph => glyph.end > start && glyph.start < end);
  expect(glyphs.length).toBeGreaterThan(0);
  return { start, end, glyphs, left: Math.min(...glyphs.map(glyph => glyph.left)),
    top: Math.min(...glyphs.map(glyph => glyph.top)), right: Math.max(...glyphs.map(glyph => glyph.right)),
    bottom: Math.max(...glyphs.map(glyph => glyph.bottom)) };
}
function expectSampleInsideCell(glyphs: SampleGlyph[], label: string) {
  for (const glyph of glyphs) {
    expect(glyph.clip, `${label}: native cell clipping boundary`).not.toBeNull();
    expect(glyph.left, `${label}: left glyph edge`).toBeGreaterThanOrEqual(glyph.clip!.left - 1);
    expect(glyph.right, `${label}: right glyph edge`).toBeLessThanOrEqual(glyph.clip!.right + 1);
    expect(glyph.top, `${label}: top glyph edge`).toBeGreaterThanOrEqual(glyph.clip!.top - 1);
    expect(glyph.bottom, `${label}: bottom glyph edge`).toBeLessThanOrEqual(glyph.clip!.bottom + 1);
  }
}

test("REAL rhwp standard designer: fixed appendix examples keep TOC numbers on rows and tables within their layout", async ({ page, request }, info) => {
  test.setTimeout(180000);
  await page.setViewportSize({ width: 1366, height: 900 });
  const modelPosts: string[] = [];
  page.on("request", value => { if (value.method() === "POST" && /\/(report-tasks|assistant-turns|rewrite-tasks)$/.test(new URL(value.url()).pathname)) modelPosts.push(value.url()); });
  const seed = await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const source = await (await request.get("/api/document-standard")).json();
  const originalHash = hash(await (await request.get(source.reference_url)).body());
  const proof: any[] = [];
  for (const composition of [
    { preset: "appendix-1", pages: 1, entries: [] as string[][], tail: "" },
    { preset: "appendix-2", pages: 2, entries: [["1. 개요", "1"], ["2. 2015년도 핀테크 이슈", "1"],
      ["3. 2015년도 핀테크 전망", "3"], ["4. 핀테크 발전 과제", "5"], ["가. 핀테크 이용자 확대에 대비", "5"],
      ["나. 보안 위협에 대처", "6"], ["다. 서비스 다양화", "7"], ["라. 규제 제한 완화", "8"], ["마. 창업 센타 지원", "9"],
      ["바. 기술 신용 보증 기금 확대", "10"], ["사. IT 리스크 점검", "11"], ["아. 이용자 보호 방안 마련", "12"],
      ["자. 서비스 제공자 교육", "13"], ["5. 실시 일정", "14"]], tail: "정부의 핀테크 관련 법안" },
    { preset: "appendix-3", pages: 1, entries: [["1. 개요", "1"], ["2. 현안 과제", "2"], ["3. 추진 방안", "5"]], tail: "2015 년 공공부문 추진 현황" },
  ]) {
    const response = await request.post("/api/visual-standards/preview", { data: {
      settings: { ...catalog.defaults, preset_id: composition.preset }, seed_base64: seed,
    } });
    expect(response.ok(), await response.text()).toBeTruthy();
    const originalPreview = await response.json();
    const bytes = Buffer.from(originalPreview.preview_base64, "base64");
    expect(hash(bytes)).toBe(originalPreview.preview_sha256);
    const svgs = await renderOriginalExample(page, bytes);
    expect(svgs, `${composition.preset} fixed example page count`).toHaveLength(composition.pages);
    const geometry: SampleGeometry[] = [];
    for (const [index, svg] of svgs.entries()) {
      await writeFile(info.outputPath(`${composition.preset}-layout-page-${index + 1}.svg`), svg);
      geometry.push(await measureSampleSvg(page, svg, info.outputPath(`${composition.preset}-layout-page-${index + 1}.png`)));
    }
    // Save measurements before assertions so a regression includes its actual
    // offending coordinates, not merely a screenshot or a changed image hash.
    await writeFile(info.outputPath(`${composition.preset}-geometry.json`), JSON.stringify(geometry, null, 2));
    const rowProof: any[] = [];
    let tableProof: any = null;
    if (composition.entries.length) {
      const toc = geometry.find(value => value.text.includes("<목차>"))!;
      expect(toc).toBeTruthy();
      for (const [entryIndex, [title, number]] of composition.entries.entries()) {
        const heading = sampleRange(toc, title);
        // The SVG text stream has no row separators: page 1 followed by title
        // 2 must not be mistaken for page 12. Bound this suffix by the next
        // known source heading (or the attachment label after the final row).
        const nextLabel = composition.entries[entryIndex + 1]?.[0] || "별첨";
        const next = sampleRange(toc, nextLabel, heading.end);
        const suffix = toc.text.slice(heading.end, next.start).match(/^[-·.…]*(\d+)$/);
        expect(suffix, `${composition.preset} page number follows ${title}`).not.toBeNull();
        expect(suffix![1]).toBe(number);
        const numberStart = heading.end + suffix![0].length - number.length;
        const pageNumber = sampleRange(toc, number, numberStart);
        const baselines = [...heading.glyphs, ...pageNumber.glyphs].map(glyph => glyph.baseline);
        expect(Math.max(...baselines) - Math.min(...baselines), `${composition.preset}: ${title} and ${number} share one row`).toBeLessThanOrEqual(1);
        expect(pageNumber.left, `${title}: number is to the right of its title`).toBeGreaterThan(heading.right);
        expectSampleInsideCell([...heading.glyphs, ...pageNumber.glyphs], title);
        rowProof.push({ title, number, title_bounds: heading, number_bounds: pageNumber });
      }
      const tail = sampleRange(toc, composition.tail);
      expectSampleInsideCell(tail.glyphs, composition.tail);
      rowProof.push({ last_attachment: composition.tail, bounds: tail });
    } else {
      const sample = geometry[0], caption = sampleRange(sample, "우리나라 현황"), unit = sampleRange(sample, "(단위: 억원)");
      const sourceNote = sampleRange(sample, "정보통신진흥협회");
      const expectedWidth = (catalog.defaults.page_width_mm - catalog.defaults.margin_left_mm - catalog.defaults.margin_right_mm) * 96 / 25.4;
      const lines = sample.lines.filter(line => line.stroke === "#000000" && !line.dashed && Math.abs(line.y1 - line.y2) < .01 &&
        Math.abs(line.x2 - line.x1) > expectedWidth * .8 && line.y1 >= caption.bottom - 1 && line.y1 < sourceNote.top);
      expect(lines.length, "The actual full-width table has visible horizontal borders").toBeGreaterThanOrEqual(2);
      const table = { left: Math.min(...lines.map(line => Math.min(line.x1, line.x2))), right: Math.max(...lines.map(line => Math.max(line.x1, line.x2))),
        top: Math.min(...lines.map(line => line.y1)), bottom: Math.max(...lines.map(line => line.y1)) };
      expect(Math.abs(table.right - table.left - expectedWidth), "Sample table uses the body width").toBeLessThanOrEqual(1);
      expect(Math.abs(table.left - catalog.defaults.margin_left_mm * 96 / 25.4), "Sample table starts at the body left margin").toBeLessThanOrEqual(1);
      expect(table.right).toBeLessThanOrEqual(sample.width - catalog.defaults.margin_right_mm * 96 / 25.4 + 1);
      expect(caption.bottom).toBeLessThan(table.top);
      expect(unit.bottom).toBeLessThan(table.top);
      expect(sourceNote.top).toBeGreaterThan(table.bottom);
      for (const text of ["구분", "대표기업", "서비스실적", "인증부문", "SKT", "123,456", "45,678"]) {
        const range = sampleRange(sample, text);
        expect(range.top, `${text}: table content below caption`).toBeGreaterThanOrEqual(table.top - 1);
        expect(range.bottom, `${text}: table content above notes`).toBeLessThanOrEqual(table.bottom + 1);
      }
      tableProof = { caption, unit, table, source_note: sourceNote, expected_width: expectedWidth };
    }
    proof.push({ preset: composition.preset, pages: svgs.length, rows: rowProof, table: tableProof,
      original_endpoint_preview_sha256: originalPreview.preview_sha256, svg_sha256: svgs.map(svg => hash(Buffer.from(svg))) });
  }
  expect(modelPosts).toEqual([]);
  expect(hash(await (await request.get(source.reference_url)).body())).toBe(originalHash);
  await writeFile(info.outputPath("fixed-appendix-layout-proof.json"), JSON.stringify({ actual_model_calls: 0,
    real_engine: true, scope: "Three fixed original appendix examples at default settings only",
    original_sha256: originalHash, original_preserved: true, presets: proof }, null, 2));
});
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
    const preview = await page.locator(".standard-preview").boundingBox();
    const nav = (await page.locator(".standard-workspace-rail").boundingBox())!;
    const navigationRight = nav.x + nav.width;
    const grid = page.locator(".standard-designer-grid");
    const gridSize = await grid.evaluate(element => ({ width: element.clientWidth, height: element.clientHeight,
      scrollWidth: element.scrollWidth, scrollLeft: element.scrollLeft, compact: element.classList.contains("compact-layout") }));
    const settings = page.getByRole("complementary", { name: "문서 표준 설정", exact: true });
    const visible = await settings.isVisible();
    const bounds = visible ? (await settings.boundingBox())! : null;
    if (bounds) {
      expect(bounds.x).toBeCloseTo(navigationRight - gridSize.scrollLeft, 1);
      expect(bounds.height).toBe(gridSize.height);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    }
    const wrapper = (await page.locator(".standard-designer-grid").boundingBox())!;
    expect(wrapper.x).toBeCloseTo(navigationRight, 1);
    expect(wrapper.x + wrapper.width).toBeCloseTo(page.viewportSize()!.width, 1);
    if (preview) {
      expect(preview.x).toBeCloseTo(bounds ? bounds.x + bounds.width : navigationRight - gridSize.scrollLeft, 1);
      const available = gridSize.width - (bounds?.width || 0);
      const expectedWidth = bounds && gridSize.compact ? Math.max(360, available) : available;
      expect(preview.width).toBeCloseTo(expectedWidth, 1);
      if (expectedWidth > available) expect(gridSize.scrollWidth).toBeGreaterThan(gridSize.width);
      else expect(preview.x + preview.width).toBeCloseTo(page.viewportSize()!.width - gridSize.scrollLeft, 1);
    } else {
      // At compact widths the settings replace the preview, rather than
      // squeezing both panes into unusable fractions of the workspace.
      expect(bounds).not.toBeNull();
      expect(bounds!.width).toBeCloseTo(wrapper.width, 1);
    }
    if (!bounds) {
      expect(preview!.width).toBeGreaterThan(0);
      await expect(page.getByRole("group", { name: "미리보기 확대", exact: true })).toBeInViewport();
    }
  };
  if (!(await page.locator(".standard-preview").isVisible()))
    await page.getByRole("button", { name: "문서 표준 설정 닫기", exact: true }).click();
  const previewBounds = (await page.locator(".standard-preview").boundingBox())!;
  expect(previewBounds.y).toBe(0);
  expect(previewBounds.height).toBe(await page.locator(".standard-designer-grid").evaluate(element => element.clientHeight));
  await expectOuterViewportContained(page);
  await expectReflow();
  for (const selector of [".standard-preview-pages", ".standard-settings-content"]) {
    const pane = page.locator(selector);
    const switchedPane = !(await pane.isVisible());
    if (switchedPane)
      await page.getByRole("button", { name: `문서 표준 설정 ${selector === ".standard-settings-content" ? "열기" : "닫기"}`, exact: true }).click();
    await expect(pane).toBeVisible();
    await expectReflow();
    const scroll = await pane.evaluate(element => {
      element.scrollTop = element.scrollHeight;
      return { top: element.scrollTop, maximum: element.scrollHeight - element.clientHeight };
    });
    expect(scroll.maximum).toBeGreaterThanOrEqual(0);
    if (selector === ".standard-settings-content") expect(scroll.maximum).toBeGreaterThan(0);
    if (scroll.maximum > 0) expect(scroll.top).toBeGreaterThan(0);
    expect(Math.abs(scroll.maximum - scroll.top)).toBeLessThanOrEqual(1);
    await expectOuterViewportContained(page);
    await pane.evaluate(element => { element.scrollTop = 0; });
    if (switchedPane)
      await page.getByRole("button", { name: `문서 표준 설정 ${selector === ".standard-settings-content" ? "닫기" : "열기"}`, exact: true }).click();
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
  const nextPreview = page.waitForRequest(request => request.method() === "POST" && new URL(request.url()).pathname === "/api/visual-standards/sample-preview");
  await page.getByLabel("본문 줄 간격", { exact: true }).fill("180");
  await nextPreview;
  await page.getByLabel("절 제목 글자 크기", { exact: true }).focus();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await expect(page.getByLabel("절 제목 글자 크기", { exact: true })).toBeFocused();
  await restoreDefaults(page);
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  expect(await page.evaluate(() => document.activeElement?.getAttribute("aria-label"))).not.toBe("본문 줄 간격");
  expect(await page.evaluate(() => document.activeElement?.getAttribute("aria-label"))).not.toBe("절 제목 글자 크기");
  await loadSavedStandard(page, legacy.id);
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await openGroup(page, "본문");
  await page.getByLabel("본문 글자 크기", { exact: true }).fill("16");
  await page.getByLabel("본문 줄 간격", { exact: true }).fill("180");
  await openGroup(page, "절 제목");
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
  const pageNumbers = await page.evaluate(svgs => svgs.map((svg, index) => {
    const document = new DOMParser().parseFromString(svg, "image/svg+xml");
    if (document.querySelector("parsererror")) throw new Error(`예시 ${index + 1}쪽 SVG가 올바르지 않습니다.`);
    const height = Number(document.documentElement.getAttribute("viewBox")?.trim().split(/\s+/)[3]);
    if (!Number.isFinite(height) || height <= 0) throw new Error(`예시 ${index + 1}쪽 SVG의 높이를 확인할 수 없습니다.`);
    const footer = Array.from(document.getElementsByTagNameNS("*", "text"))
      .filter(text => Number(text.getAttribute("y")) > height - 90)
      .map(text => ({ y: Number(text.getAttribute("y")), text: text.textContent || "" }));
    return { page: index + 1, height, footer, text: footer.map(item => item.text).join("").replace(/\s+/g, "") };
  }), await previewSvgPages(page));
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
  const registered = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/visual-standards/sample-register");
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
  await expect(page.getByLabel("저장한 표준", { exact: true })).toHaveValue(saved.id);
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
  await expect(page.getByLabel("새 요청에 사용할 서식", { exact: true })).toContainText("괄호 요약어: 사용 안 함 · 핵심 요약 미포함");
  await expect(page.getByLabel("새 요청에 사용할 서식", { exact: true })).not.toContainText("말머리는 굵게 표시");
  await page.getByText("양식 관리·연결 정보", { exact: true }).click();
  await expect(page.getByRole("button", { name: "문서작성 기준 열기", exact: true })).toBeEnabled();
  await expect(page.getByRole("link", { name: /기준서.*(다운로드|사본)/ })).toHaveCount(0);
  expect(modelPosts).toHaveLength(0);
  await documentListButton(page).click();
  await page.getByRole("button", { name: "문서 표준 편집", exact: true }).click();
  await loadSavedStandard(page, saved.id);
  await expect(page.getByLabel("본문 글자 크기", { exact: true })).toHaveValue("16");
  const labelField = catalog.fields.find((field: any) => field.key === "body_parenthetical_labels");
  await openGroup(page, labelField.group);
  await expect(page.getByLabel(`${labelField.group} ${labelField.label}`, { exact: true })).not.toBeChecked();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await restoreDefaults(page);
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
  await page.route("**/api/visual-standards/sample-preview", async route => {
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
  await page.unroute("**/api/visual-standards/sample-preview");
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
  const registered = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/visual-standards/sample-register");
  await submitRegistration(page).click();
  expect((await (await registered).json()).settings.body_size_pt).toBe(18);
});

test("REAL rhwp standard designer: lost registration response retries the same frozen request", async ({ page, request }) => {
  await openDesigner(page);
  const bodies: string[] = [];
  let registeredId = "";
  await page.route("**/api/visual-standards/sample-register", async route => {
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
  expect(catalog.items.find((item: any) => item.id === registeredId).settings.preset_id).toBe(catalog.defaults.preset_id);
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
  page.on("request", value => { if (value.method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/sample-preview") posted.push(value.postDataJSON()); });
  await openDesigner(page);
  const catalog = await (await request.get("/api/visual-standards")).json();
  const original = await (await request.get("/api/document-standard")).json();
  const originalFile = await (await request.get(original.reference_url)).body();
  expect(hash(originalFile)).toBe(catalog.source.reference_sha256);
  expect(catalog.presets).toHaveLength(3);
  expect(catalog.presets.every((preset: any) => preset.revision === 3 && preset.base_standard.reference_sha256 === hash(originalFile))).toBe(true);
  expect(catalog.rules.length).toBeGreaterThan(0);
  await page.getByRole("tab", { name: "상세 설정", exact: true }).click();
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
      new URL(value.url()).pathname === "/api/visual-standards/sample-preview" &&
      settingKey(value.request().postDataJSON().settings) === settingKey(expected));
    await change();
    const response = await completed;
    expect(response.ok()).toBeTruthy();
    const rendered = await response.json();
    // Shared sample preview first returns a layout stage. Its hash may change
    // when real-engine TOC measurements converge; compare the final receipt.
    await expect(page.locator(".standard-preview-pages")).toHaveAttribute("data-settings-sha256", rendered.settings_sha256, { timeout: 90000 });
    await expect(page.locator(".standard-preview-pages")).toHaveAttribute("aria-busy", "false");
    await expect(registerButton(page)).toBeEnabled();
    await expect.poll(() => page.locator(".standard-preview-pages figure img").evaluateAll(images => images.map(image => (image as HTMLImageElement).src))).not.toEqual(before);
    return { ...rendered, preview_sha256: await page.locator(".standard-preview-pages").getAttribute("data-preview-sha256") };
  };
  const customized = { ...catalog.defaults, table_header_fill: "#DDEFE5" };
  await settle(customized, () => setCatalogField(page, catalog, "table_header_fill", "#DDEFE5"));
  const presetProof: any[] = [];
  for (const preset of catalog.presets) {
    const rendered = await settle({ ...customized, preset_id: preset.settings.preset_id },
      async () => { await page.getByLabel("기본 양식", { exact: true }).selectOption(String(preset.settings.preset_id)); });
    const current = posted[posted.length - 1].settings;
    expect(current).toEqual({ ...customized, preset_id: preset.settings.preset_id });
    const svgs = await page.evaluate(values => values.map(svg => ({ svg,
      text: new DOMParser().parseFromString(svg, "image/svg+xml").documentElement.textContent || "" })), await previewSvgPages(page));
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
  await expect(page.getByLabel("저장한 표준", { exact: true })).toHaveCount(0);
  const referenceSvgs = await previewSvgPages(page);
  const pages = referenceSvgs.length;
  expect(pages).toBeGreaterThan(0); expect(pages).toBeLessThanOrEqual(reference.max_pages);
  const frame = await (await page.locator(".standard-renderer iframe").elementHandle())!.contentFrame();
  if (!frame) throw new Error("기준서를 읽는 실제 한글 엔진이 없습니다.");
  expect(await frame.evaluate(() => (window as any).hwpAssistantBridge.documentState().pageCount)).toBe(pages);
  const renderedText = await page.evaluate(svgs => svgs.map(svg =>
    new DOMParser().parseFromString(svg, "image/svg+xml").documentElement.textContent || "").join("").replace(/\s+/g, ""), referenceSvgs);
  expect(renderedText).toContain(catalog.rules[0].text.replace(/\s+/g, ""));
  await page.screenshot({ path: info.outputPath("reference-full-document.png"), fullPage: true });
  await page.getByRole("tab", { name: "서식 미리보기", exact: true }).click();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  expect(posted[posted.length - 1].settings).toEqual(selected);
  await registerButton(page).click();
  const name = `원문 기반 표준 ${Date.now()}`;
  await page.getByLabel("표준 이름", { exact: true }).fill(name);
  const registration = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/sample-register");
  await submitRegistration(page).click();
  const saved = await (await registration).json();
  expect(saved.revision).toBe(3); expect(saved.settings).toEqual(selected);
  expect(hash(await (await request.get(`/api/visual-standards/${saved.id}/file`)).body())).toBe(saved.preview_sha256);
  await documentListButton(page).click();
  await openDesigner(page);
  await loadSavedStandard(page, saved.id);
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
  expect(semantic.exported.settings).toEqual({
    ...Object.fromEntries(Object.entries(selection.settings).filter(([key]) => key.startsWith("toc_") || key === "preset_id")),
    body_spacing: "reference-body-spacing-v1",
  });
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
  await expect(page.getByRole("note").filter({ hasText: "이전 등록 표준의 설정을 그대로" })).toBeVisible();
  await expect(registerButton(page)).toBeEnabled({ timeout: 90000 });
  await expect(page.getByLabel("기본 양식", { exact: true })).toHaveCount(0);
  for (const field of catalog.legacy["2"].fields.filter((item: any) => !(item.key in settings)))
    await expect(page.getByLabel(`${field.group} ${field.label}`, { exact: true })).toHaveCount(0);
  const copyResponse = page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === "/api/visual-standards/sample-register");
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
  const registered = page.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === "/api/visual-standards/sample-register");
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
  await expect(page.getByLabel("현재 보고서 고정 서식", { exact: true })).toContainText("괄호 요약어: 사용 안 함 · 핵심 요약 미포함");
  await page.getByLabel("문서 양식", { exact: true }).selectOption("");
  await expect(page.getByLabel("문서 양식", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("새 요청에 사용할 서식", { exact: true })).toContainText("괄호 요약어: 모든 본문 항목 · 핵심 요약 포함");
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
