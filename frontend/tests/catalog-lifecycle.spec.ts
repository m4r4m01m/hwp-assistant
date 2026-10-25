import { test, expect, type Page, type Route } from "@playwright/test";

// HTTP and the document engine are mocked. These cases exercise catalog UI
// state and retry fences; they do not validate native HWPX rendering.
const respond = (route: Route, value: unknown, status = 200) => route.fulfill({
  status, contentType: "application/json", body: JSON.stringify(value),
});

async function catalogPage(page: Page) {
  await page.route("**/catalog-lifecycle-test.html*", route => route.fulfill({
    contentType: "text/html", body: '<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/catalog-lifecycle.tsx"></script></body></html>',
  }));
}

test("MOCK catalogs: design-library navigation preserves an unfinished template registration and its retry", async ({ page }) => {
  const source = { id: "source-document", name: "합성 양식 원본.hwpx", current_version: "source-version", role: "owner", created_at: 1 };
  const requests: unknown[] = [];
  await catalogPage(page);
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    if (path === "/design-fragments") return respond(route, { items: [] });
    if (path === "/template-market") return respond(route, { items: [], total: 0, categories: [] });
    if (path === "/documents") return respond(route, [source]);
    if (path === "/documents/source-document/versions") return respond(route, [{ id: "source-version", created_at: 1 }]);
    if (path === "/templates" && request.method() === "POST") {
      requests.push(request.postDataJSON());
      return respond(route, { detail: "합성 응답 유실" }, 503);
    }
    return respond(route, { detail: `Unexpected request: ${path}` }, 404);
  });
  await page.goto("/catalog-lifecycle-test.html?screen=templates");
  await page.getByRole("button", { name: "기존 문서 양식", exact: true }).click();
  const back = page.getByRole("button", { name: "← 표·제목 디자인 보관함", exact: true });
  await expect(back).toBeEnabled();
  await page.getByRole("button", { name: "양식 등록", exact: true }).click();
  await expect(back).toBeDisabled();
  await page.getByLabel("양식 원본 문서", { exact: true }).selectOption(source.id);
  const save = page.getByRole("button", { name: "이 저장본을 양식으로 등록", exact: true });
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByText("양식 등록 결과 확인 대기", { exact: true })).toBeVisible();
  await expect(back).toBeDisabled();
  await expect(page.getByRole("button", { name: "취소", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "같은 요청 재확인", exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toEqual(requests[0]);
  await expect(back).toBeDisabled();
  await expect(page.getByLabel("양식 원본 문서", { exact: true })).toHaveValue(source.id);
});

test("MOCK catalogs: deleting a legacy standard resets the current editor contract and comparison", async ({ page }) => {
  const base = { id: "synthetic-base", reference_sha256: "a".repeat(64), profile_sha256: "b".repeat(64) };
  const legacy = { id: "legacy-standard", revision: 1, name: "이전 형식 표준", settings: { body_size_pt: 15 },
    settings_sha256: "c".repeat(64), preview_sha256: "d".repeat(64), base_standard: base };
  const defaults = { square_size_pt: 14 };
  const field = (key: string, group: string) => ({ key, group, label: "글자 크기", type: "number", min: 8, max: 30, step: 1, unit: "pt" });
  let items = [legacy];
  const requests: Record<string, unknown>[] = [];
  await catalogPage(page);
  await page.route("**/src/editor.ts*", route => route.fulfill({ contentType: "text/javascript", body: `
    export class EditorAdapter {
      static async mount() { return new EditorAdapter(); }
      bridge = { newDocument: async () => {} };
      sdk = { pageCount: async () => 1, getPageSvg: async () => '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="1100" viewBox="0 0 800 1100"><text x="20" y="30">합성예시</text></svg>' };
      exportHwpx() { return { bytes: new Uint8Array([1, 2, 3]), contentLoss: { count: 0 } }; }
      async load() {}
      destroy() {}
    }
  ` }));
  await page.route("**/src/report-toc.ts*", route => route.fulfill({ contentType: "text/javascript", body: `
    export const captureReport = () => ({ source: { bytes: new Uint8Array([1, 2, 3]) } });
    export const fenceReport = () => {};
  ` }));
  await page.route("**/src/standard-preview-fonts.ts*", route => route.fulfill({ contentType: "text/javascript", body: `
    export const embedStandardPreviewFonts = async value => value;
    export const standardPreviewFontNames = [];
  ` }));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    if (path === "/visual-standards" && request.method() === "GET") return respond(route, {
      defaults, fields: [field("square_size_pt", "문단 내용 · □ 항목")], items,
      presets: [{ ...legacy, id: "default-standard", revision: 3, name: "새 기본값", settings: defaults, description: "합성 새 기본값" }],
      legacy: { "1": { defaults: { body_size_pt: 15 }, fields: [field("body_size_pt", "일반 본문")] } },
    });
    if (path === "/design-fragments") return respond(route, { items: [] });
    if (path === "/visual-standards/legacy-standard" && request.method() === "DELETE") {
      items = [];
      return respond(route, { hidden: true });
    }
    if (path === "/visual-standards/sample-preview") {
      const settings = request.postDataJSON().settings;
      requests.push(settings);
      return respond(route, { settings_sha256: "e".repeat(64), preview_sha256: "039058c6f2c0cb492c533b0a4d14ef77cc0f78abccced5287d84a1a2011cfb81",
        preview_base64: "AQID", base_standard: base, max_pages: 10, context: "synthetic-context", phase: "ready", sample_id: "synthetic-sample",
        sample_sha256: "f".repeat(64), sample_regions: [{ role: "square", texts: ["합성예시"] }], needs_toc: false,
        expires_at: Date.now() / 1000 + 600, page_count: 1 });
    }
    return respond(route, { detail: `Unexpected request: ${path}` }, 404);
  });
  await page.goto("/catalog-lifecycle-test.html?screen=standard");
  const register = page.getByRole("button", { name: "새 표준 등록", exact: true });
  await expect(register).toBeEnabled();
  await page.getByLabel("저장한 표준", { exact: true }).selectOption(legacy.id);
  await page.getByRole("button", { name: "선택한 표준 편집", exact: true }).click();
  await expect(page.getByRole("button", { name: "변경사항 저장", exact: true })).toBeEnabled();
  expect(requests.at(-1)).toEqual(legacy.settings);
  await page.getByRole("button", { name: "현재 표준 삭제", exact: true }).click();
  await page.getByRole("alertdialog", { name: "사용자 표준 삭제", exact: true }).getByRole("button", { name: "삭제 확인", exact: true }).click();
  await expect(register).toBeEnabled();
  expect(requests.at(-1)).toEqual(defaults);
  await expect(page.locator(".standard-compare-note")).toContainText("0개 설정 변경");
  await register.click();
  await expect(page.getByLabel("표준 이름", { exact: true })).toHaveValue("");
});
