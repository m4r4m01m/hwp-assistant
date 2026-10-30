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
  const back = page.getByRole("button", { name: "표·제목 디자인 보관함", exact: true });
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

test("MOCK catalogs: confirmed design deletion refreshes the gallery and fences only the matching picker", async ({ page }) => {
  const fragment = (id: string, name: string) => ({ id, name, kind: "table" as const, settings: { table_header_fill: "#eeeeee" }, sha256: "a".repeat(64) });
  let items = [fragment("fragment-a", "합성 디자인 A"), fragment("fragment-b", "합성 디자인 B"), fragment("fragment-c", "합성 디자인 C"), fragment("fragment-d", "합성 디자인 D")];
  const builtin = { ...fragment("reference-table", "기준 표 디자인"), sha256: "b".repeat(64) };
  let catalogReads = 0, failDelete = true;
  const writes: string[] = [];
  await catalogPage(page);
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    if (request.method() !== "GET") writes.push(`${request.method()} ${path}`);
    if (path === "/editor-designs") {
      catalogReads++;
      return respond(route, { designs: [builtin, ...items], standards: [] });
    }
    if (path === "/design-fragments") return respond(route, { items });
    if (path === "/documents") return respond(route, []);
    if (path.startsWith("/design-fragments/") && request.method() === "DELETE") {
      if (failDelete) { failDelete = false; return respond(route, { detail: "합성 삭제 실패" }, 503); }
      items = items.filter(item => item.id !== path.split("/").at(-1));
      if (path.endsWith("/fragment-d")) items = items.map(item => item.id === "fragment-b" ? { ...item, sha256: "c".repeat(64), settings: { table_header_fill: "#cccccc" } } : item);
      return respond(route, { hidden: true });
    }
    return respond(route, { detail: `Unexpected request: ${path}` }, 404);
  });
  await page.goto("/catalog-lifecycle-test.html?screen=templates");
  const shelf = page.getByRole("region", { name: "표·제목 예시", exact: true });
  await expect(shelf.getByText("합성 디자인 A", { exact: true })).toBeVisible();
  await page.getByText("저장한 디자인 관리", { exact: true }).click();
  const library = page.getByRole("region", { name: "디자인 보관함", exact: true });
  const remove = async (name: string) => {
    await library.locator("article").filter({ has: page.getByText(name, { exact: true }) }).getByRole("button", { name: "삭제", exact: true }).click();
    await page.getByRole("alertdialog", { name: "디자인 삭제", exact: true }).getByRole("button", { name: "삭제 확인", exact: true }).click();
  };
  await remove("합성 디자인 A");
  await expect(library.getByRole("alert")).toContainText("서버에서 요청을 처리하지 못했습니다.");
  await expect(shelf.getByText("합성 디자인 A", { exact: true })).toBeVisible();
  expect(catalogReads).toBe(1);
  await page.getByRole("alertdialog", { name: "디자인 삭제", exact: true }).getByRole("button", { name: "삭제 확인", exact: true }).click();
  await expect(page.getByText("합성 디자인 A", { exact: true })).toHaveCount(0);
  await expect(library.getByRole("alert")).toHaveCount(0);
  await expect(shelf.getByText(builtin.name, { exact: true })).toBeVisible();
  expect(catalogReads).toBe(2);
  expect(writes).toEqual(["DELETE /design-fragments/fragment-a", "DELETE /design-fragments/fragment-a"]);

  await shelf.getByRole("button").filter({ has: page.getByText("합성 디자인 B", { exact: true }) }).click();
  const picker = page.getByRole("region", { name: "디자인을 적용할 문서", exact: true });
  await expect(picker.getByRole("heading")).toHaveText("‘합성 디자인 B’을 사용할 문서");
  await remove("합성 디자인 C");
  await expect(library.getByText("합성 디자인 C", { exact: true })).toHaveCount(0);
  await expect(picker.getByRole("heading")).toHaveText("‘합성 디자인 B’을 사용할 문서");
  await expect.poll(() => catalogReads).toBe(3);
  await remove("합성 디자인 D");
  await expect(picker).toHaveCount(0); // Same ID with a different hash is no longer the selected design.
  await expect(shelf.getByText("합성 디자인 B", { exact: true })).toBeVisible();
  expect(catalogReads).toBe(4);
  await remove("합성 디자인 B");
  await expect(picker).toHaveCount(0);
  await expect(page.getByText("합성 디자인 B", { exact: true })).toHaveCount(0);
  await expect(shelf.getByText(builtin.name, { exact: true })).toBeVisible();
  expect(catalogReads).toBe(5);
  expect(writes).toEqual(["DELETE /design-fragments/fragment-a", "DELETE /design-fragments/fragment-a", "DELETE /design-fragments/fragment-c", "DELETE /design-fragments/fragment-d", "DELETE /design-fragments/fragment-b"]);
});

test("MOCK catalogs: a refresh failure preserves the picker but fences use without undoing a confirmed deletion", async ({ page }) => {
  const design = (id: string) => ({ id, name: id, kind: "table", settings: {}, sha256: "a".repeat(64) });
  let items = [design("selected-design"), design("removed-design")], reads = 0;
  await catalogPage(page);
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname.replace(/^.*\/api/, ""), method = route.request().method();
    if (path === "/editor-designs") return ++reads === 2 ? respond(route, { detail: "refresh failed" }, 503) : respond(route, { designs: items, standards: [] });
    if (path === "/design-fragments") return respond(route, { items });
    if (path === "/documents") return respond(route, [{ id: "target", name: "합성 대상 문서", current_version: "v1", role: "owner" }]);
    if (path === "/design-fragments/removed-design" && method === "DELETE") { items = items.filter(item => item.id !== "removed-design"); return respond(route, { hidden: true }); }
    return respond(route, {}, 404);
  });
  await page.goto("/catalog-lifecycle-test.html?screen=templates");
  await page.getByRole("region", { name: "표·제목 예시", exact: true }).getByRole("button").filter({ has: page.getByText("selected-design", { exact: true }) }).click();
  await page.getByText("저장한 디자인 관리", { exact: true }).click();
  const library = page.getByRole("region", { name: "디자인 보관함", exact: true });
  await library.locator("article").filter({ has: page.getByText("removed-design", { exact: true }) }).getByRole("button", { name: "삭제", exact: true }).click();
  await page.getByRole("alertdialog", { name: "디자인 삭제", exact: true }).getByRole("button", { name: "삭제 확인", exact: true }).click();
  await expect(library.getByText("removed-design", { exact: true })).toHaveCount(0);
  await expect(library.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("alert")).toContainText("디자인 목록을 새로 확인하지 못했습니다.");
  const picker = page.getByRole("region", { name: "디자인을 적용할 문서", exact: true });
  await expect(picker.getByRole("heading")).toHaveText("‘selected-design’을 사용할 문서");
  const use = picker.getByRole("button", { name: "합성 대상 문서", exact: true });
  await expect(use).toBeDisabled();
  expect(await page.evaluate(() => (window as any).__designUses || [])).toEqual([]);
  await page.getByRole("button", { name: "디자인 목록 다시 확인", exact: true }).click();
  await expect(use).toBeEnabled();
  await use.click();
  expect(await page.evaluate(() => (window as any).__designUses)).toEqual([{ document: "target", choice: { kind: "design", id: "selected-design", sha256: "a".repeat(64) } }]);
  expect(reads).toBe(3);
});

for (const oldOutcome of ["success", "failure"] as const) test(`MOCK catalogs: an aborted old GET ${oldOutcome} cannot revive deleted designs or release the current query gate`, async ({ page }) => {
  const design = (id: string) => ({ id, name: id, kind: "table", settings: {}, sha256: "a".repeat(64) });
  let items = [design("removed-first"), design("selected-design")];
  await catalogPage(page);
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window), state = (window as any).__heldDesignQueries = { reads: 0, release: {} as Record<number, (fail?: boolean) => void> };
    window.fetch = ((input: RequestInfo | URL, options?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      if (!new URL(url, location.href).pathname.endsWith("/api/editor-designs") || ++state.reads === 1) return fetch(input, options);
      const index = state.reads;
      // Deliberately ignore abort to exercise the component's own result/error/finally fences.
      return new Promise<Response>((resolve, reject) => {
        void fetch(input, { ...options, signal: undefined }).then(async response => {
          const body = await response.text();
          state.release[index] = (fail = false) => fail ? reject(new Error("stale catalog failure")) : resolve(new Response(body, { status: response.status, headers: response.headers }));
        }).catch(reject);
      });
    }) as typeof window.fetch;
  });
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname.replace(/^.*\/api/, "");
    if (path === "/editor-designs") return respond(route, { designs: items, standards: [] });
    if (path === "/design-fragments") return respond(route, { items });
    if (path === "/documents") return respond(route, [{ id: "target", name: "합성 대상 문서", current_version: "v1", role: "owner" }]);
    if (path.startsWith("/design-fragments/") && route.request().method() === "DELETE") { items = items.filter(item => item.id !== path.split("/").at(-1)); return respond(route, { hidden: true }); }
    return respond(route, {}, 404);
  });
  await page.goto("/catalog-lifecycle-test.html?screen=templates");
  await page.getByRole("region", { name: "표·제목 예시", exact: true }).getByRole("button").filter({ has: page.getByText("selected-design", { exact: true }) }).click();
  await page.getByText("저장한 디자인 관리", { exact: true }).click();
  const remove = async (id: string) => {
    await page.getByRole("region", { name: "디자인 보관함", exact: true }).locator("article").filter({ has: page.getByText(id, { exact: true }) }).getByRole("button", { name: "삭제", exact: true }).click();
    await page.getByRole("alertdialog", { name: "디자인 삭제", exact: true }).getByRole("button", { name: "삭제 확인", exact: true }).click();
  };
  await remove("removed-first");
  await expect.poll(() => page.evaluate(() => !!(window as any).__heldDesignQueries.release[2])).toBe(true);
  await remove("selected-design");
  await expect.poll(() => page.evaluate(() => !!(window as any).__heldDesignQueries.release[3])).toBe(true);
  await page.evaluate(outcome => (window as any).__heldDesignQueries.release[2](outcome === "failure"), oldOutcome);
  const picker = page.getByRole("region", { name: "디자인을 적용할 문서", exact: true });
  await expect(picker.getByRole("heading")).toHaveText("‘selected-design’을 사용할 문서");
  await expect(picker.getByRole("button", { name: "합성 대상 문서", exact: true })).toBeDisabled();
  await expect(page.getByRole("status")).toContainText("디자인을 새로 확인하는 중…");
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__designUses || [])).toEqual([]);
  await page.evaluate(() => (window as any).__heldDesignQueries.release[3]());
  await expect(picker).toHaveCount(0);
  await expect(page.getByRole("region", { name: "표·제목 예시", exact: true }).getByText("selected-design", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "표·제목 예시", exact: true })).toHaveAttribute("aria-busy", "false");
  await expect(page.getByRole("alert")).toHaveCount(0);
});
