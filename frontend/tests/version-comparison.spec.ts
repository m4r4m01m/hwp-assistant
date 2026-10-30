import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { createHash } from "node:crypto";
import { unzipSync, zipSync, strFromU8, strToU8 } from "fflate";
import init, { HwpDocument } from "@rhwp/core";

// Run against a dedicated DB with these three accounts. No AI provider is used.
const tokens: Record<string, string> = JSON.parse(
  process.env.HWP_COMPARE_TOKENS || "{}",
);
test.skip(
  !tokens.alice || !tokens.bob || !tokens.reader,
  "Requires isolated comparison account fixture; see docs/version-comparison.md",
);
const hash = (data: Uint8Array) =>
  createHash("sha256").update(data).digest("hex");
let base: Buffer;
test.beforeAll(async () => {
  const engine = resolve(process.env.HWP_COMPARE_PUBLIC || "public", "rhwp");
  const info = JSON.parse(
    await readFile(resolve(engine, "build-info.json"), "utf8"),
  );
  const wasmPath = Object.keys(info.bundle).find((n) =>
    /^assets\/rhwp_bg-.*\.wasm$/.test(n),
  )!;
  const wasm = await readFile(resolve(engine, wasmPath));
  expect(hash(wasm)).toBe(info.coreBuild.wasm_sha256);
  expect(
    hash(
      await readFile(
        new URL("../node_modules/@rhwp/core/rhwp.js", import.meta.url),
      ),
    ),
  ).toBe(info.coreBuild.binding_sha256);
  await init({ module_or_path: wasm });
  const doc = HwpDocument.createEmpty();
  doc.createBlankDocument();
  const texts = [
    "사업 예산은 1,000만 원입니다.",
    "담당 부서는 기획부입니다.",
    "유지되는 마지막 문단입니다.",
  ];
  texts.forEach((text, i) => {
    if (i) doc.splitParagraph(0, i - 1, texts[i - 1].length);
    doc.insertText(0, i, 0, text);
  });
  base = Buffer.from(doc.exportHwpx());
  doc.free();
});

function edit(source: Buffer, from: string, to: string) {
  const files = unzipSync(source),
    xml = strFromU8(files["Contents/section0.xml"]);
  expect(xml).toContain(from);
  files["Contents/section0.xml"] = strToU8(xml.replace(from, to));
  return Buffer.from(zipSync(files));
}
function bold(source: Buffer) {
  const files = unzipSync(source),
    original = strFromU8(files["Contents/header.xml"]);
  files["Contents/header.xml"] = strToU8(
    original.replace(
      /<hh:charPr\b[^>]*\bid="0"[^>]*>[\s\S]*?<\/hh:charPr>/,
      (m) => m.replace("</hh:charPr>", "<hh:bold/></hh:charPr>"),
    ),
  );
  return Buffer.from(zipSync(files));
}
async function client(
  request: APIRequestContext,
  user: string,
  path: string,
  method: string,
  data?: unknown,
  headers: Record<string, string> = {},
) {
  return request.fetch(path, {
    method,
    data: data as any,
    headers: { Authorization: `Bearer ${tokens[user]}`, ...headers },
  });
}
async function seed(request: APIRequestContext, bytes = base) {
  const name = `버전 비교 검증 ${crypto.randomUUID()}.hwpx`;
  const response = await client(
    request,
    "alice",
    `/api/documents?name=${encodeURIComponent(name)}&document_id=${crypto.randomUUID()}`,
    "POST",
    bytes,
    {
      "Idempotency-Key": crypto.randomUUID(),
      "Content-Type": "application/octet-stream",
    },
  );
  expect(response.ok()).toBeTruthy();
  const doc = await response.json();
  for (const [user, role] of [
    ["bob", "editor"],
    ["reader", "reader"],
  ]) {
    expect(
      (
        await client(
          request,
          "alice",
          `/api/documents/${doc.id}/access`,
          "PUT",
          { user_id: user, role },
        )
      ).ok(),
    ).toBeTruthy();
  }
  return doc;
}
async function save(
  request: APIRequestContext,
  doc: any,
  bytes: Buffer,
  user: string,
) {
  const lease = await (
    await client(request, user, `/api/documents/${doc.id}/session`, "POST")
  ).json();
  const res = await client(
    request,
    user,
    `/api/documents/${doc.id}/versions?base_version=${doc.current_version}&cause=web-edit`,
    "POST",
    bytes,
    {
      "X-Edit-Session": lease.token,
      "Idempotency-Key": crypto.randomUUID(),
      "Content-Type": "application/octet-stream",
    },
  );
  expect(res.ok(), await res.text()).toBeTruthy();
  await client(
    request,
    user,
    `/api/documents/${doc.id}/session`,
    "DELETE",
    undefined,
    { "X-Edit-Session": lease.token },
  );
  const version = await res.json();
  doc.current_version = version.id;
  return version;
}
async function login(page: Page, user: string) {
  await page.request.post("/api/auth/login", { data: { token: tokens[user] } });
  await page.goto("/");
}
async function history(page: Page, name: string) {
  const row = page
    .getByRole("row")
    .filter({ has: page.getByRole("button", { name, exact: true }) });
  await row.getByRole("button", { name: "문서 이력", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "문서 이력", exact: true })).toBeVisible();
  await expect(page.locator(".history-save-node").first()).toBeVisible();
}
async function chooseVersion(page: Page, ordinal: number) {
  // These fixtures have one linear document and no branches. The tree is newest
  // first; assertions below still use immutable IDs rather than display dates.
  const nodes = page.locator(".history-save-node");
  const node = nodes.nth(await nodes.count() - ordinal);
  await node.click();
  await expect(node).toHaveAttribute("aria-pressed", "true");
  return (await node.getAttribute("data-version-id"))!;
}
async function preparePair(page: Page, left: number, right: number) {
  const leftId = await chooseVersion(page, left);
  const more = page.locator(".history-more");
  if (await more.getAttribute("open") === null) {
    await more.locator(":scope > summary").click();
  }
  await page.getByRole("button", { name: "비교 기준으로 지정", exact: true }).click();
  const rightId = await chooseVersion(page, right);
  return {leftId, rightId};
}
async function openPair(page: Page, left = 1, right = 3) {
  const {leftId, rightId} = await preparePair(page, left, right);
  const response = page.waitForResponse(r => r.url().includes("/compare?") && r.request().method() === "GET");
  await page
    .getByRole("button", { name: "선택한 두 저장본 비교", exact: true })
    .click();
  const comparisonUrl = new URL((await response).url());
  expect(comparisonUrl.searchParams.get("left")).toBe(leftId);
  expect(comparisonUrl.searchParams.get("right")).toBe(rightId);
  await expect(
    page.locator('.comparison-pane[data-render-ready="true"]'),
  ).toHaveCount(2, { timeout: 90000 });
}

test("REAL rhwp: arbitrary pair, per-paragraph savers, native pages, reverse and no writes", async ({
  page,
  request,
}) => {
  const doc = await seed(request),
    first = doc.current_version;
  const secondBytes = edit(base, "1,000", "1,500");
  await save(request, doc, secondBytes, "bob");
  const finalBytes = edit(secondBytes, "기획부", "운영부");
  await save(request, doc, finalBytes, "alice");
  await login(page, "reader");
  await history(page, doc.name);
  await chooseVersion(page, 1);
  await expect(
    page.getByRole("button", { name: "이 저장본 복원", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator(".history-save-node")).toHaveCount(3);
  const compareButton = page.getByRole("button", {
    name: "선택한 두 저장본 비교",
    exact: true,
  });
  await expect(compareButton).toHaveCount(0);
  await preparePair(page, 2, 2);
  await expect(compareButton).toBeDisabled(); // A version cannot be compared with itself.
  await chooseVersion(page, 1);
  await expect(compareButton).toBeEnabled();
  await chooseVersion(page, 3);
  await expect(page.locator('.history-save-node[aria-pressed="true"]')).toHaveCount(1);
  await expect(compareButton).toBeEnabled();
  await page
    .getByRole("dialog", { name: "문서 이력", exact: true })
    .screenshot({ path: "test-results/version-baseline-selection.png" });
  await page.getByRole("button", { name: "기준 해제", exact: true }).click();
  await expect(compareButton).toHaveCount(0);
  const writes: string[] = [],
    external: string[] = [],
    errors: string[] = [];
  page.on("request", (r) => {
    if (/\/api\//.test(r.url()) && !["GET", "HEAD"].includes(r.method()))
      writes.push(r.url());
    if (
      /^https?:/.test(r.url()) &&
      !r.url().startsWith(new URL(page.url()).origin)
    )
      external.push(r.url());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  await openPair(page);
  await expect(page.locator(".comparison-toolbar")).toContainText("변경 2건");
  await expect(
    page.locator(".comparison-change-list button").nth(0),
  ).toContainText("bob");
  await expect(
    page.locator(".comparison-change-list button").nth(1),
  ).toContainText("alice");
  await expect(page.locator(".comparison-detail")).toContainText("1,500");
  const leftFrame = page.frameLocator(
    'iframe[title="기준 버전 읽기 전용 페이지"]',
  );
  const rightFrame = page.frameLocator(
    'iframe[title="비교 버전 읽기 전용 페이지"]',
  );
  await expect(leftFrame.locator("#page svg")).toBeVisible();
  await expect(leftFrame.locator(".change-mark").first()).toBeVisible();
  await expect(rightFrame.locator(".change-mark").first()).toBeVisible();
  await page.getByLabel("문서 비교 배율").selectOption("1.25");
  await expect(rightFrame.locator("#page svg")).toHaveAttribute(
    "style",
    /width: 992/,
  );
  await page.getByLabel("문서 비교 배율").selectOption("1");
  const geometry = await page
    .locator(".comparison-modal > section")
    .evaluate((el) => {
      const dialog = el.getBoundingClientRect(),
        sidebar = el
          .querySelector(".comparison-sidebar")!
          .getBoundingClientRect();
      const title = el.querySelector("h2")!.getBoundingClientRect();
      return {
        scroll: el.scrollLeft,
        left: title.left - dialog.left,
        right: dialog.right - sidebar.right,
      };
    });
  expect(geometry.scroll).toBe(0);
  expect(geometry.left).toBeGreaterThanOrEqual(10);
  expect(geometry.right).toBeGreaterThanOrEqual(10);
  await page.locator(".comparison-modal").screenshot({
    path: "test-results/version-comparison.png",
  });
  await page.getByRole("button", { name: "다음 변경", exact: true }).click();
  await expect(page.locator(".comparison-detail")).toContainText("운영부");
  await expect(page.locator(".comparison-detail")).toContainText("alice");
  await page.getByRole("button", { name: "비교 닫기" }).click();
  await openPair(page, 3, 1);
  await expect(page.locator(".comparison-toolbar")).toContainText("역순 비교");
  await page.getByRole("button", { name: "비교 닫기" }).click();
  expect(writes).toEqual([]);
  expect(external).toEqual([]);
  expect(errors).toEqual([]);
  const unchanged = await client(
    request,
    "alice",
    `/api/documents/${doc.id}/versions/${first}/file`,
    "GET",
  );
  expect(hash(await unchanged.body())).toBe(hash(base));
  expect(
    (
      await (
        await client(
          request,
          "alice",
          `/api/documents/${doc.id}/versions`,
          "GET",
        )
      ).json()
    ).length,
  ).toBe(3);
});

test("REAL rhwp: formatting-only comparison and single-selection guard", async ({
  page,
  request,
}) => {
  const doc = await seed(request);
  await save(request, doc, bold(base), "bob");
  await login(page, "alice");
  await history(page, doc.name);
  await preparePair(page, 2, 2);
  await expect(
    page.getByRole("button", { name: "선택한 두 저장본 비교" }),
  ).toBeDisabled();
  await openPair(page, 1, 2);
  await expect(
    page.locator(".comparison-change-list button").first(),
  ).toContainText("서식 수정");
  await expect(page.locator(".comparison-detail")).toContainText("문구는 같고");
  const right = page.frameLocator('iframe[title="비교 버전 읽기 전용 페이지"]');
  await expect(right.locator('text[font-weight="bold"]').first()).toBeVisible();
});

test("REAL rhwp: table cell diff locates native cell and identifies saver", async ({
  page,
  request,
}) => {
  const native = HwpDocument.createEmpty();
  native.createBlankDocument();
  const table = JSON.parse(native.createTable(0, 0, 0, 1, 2));
  expect(table.ok).toBeTruthy();
  native.insertTextInCell(
    0,
    table.paraIdx,
    table.controlIdx,
    0,
    0,
    0,
    "담당팀",
  );
  native.insertTextInCell(
    0,
    table.paraIdx,
    table.controlIdx,
    1,
    0,
    0,
    "100만원",
  );
  const before = Buffer.from(native.exportHwpx());
  native.free();
  const doc = await seed(request, before);
  await save(request, doc, edit(before, "100만원", "150만원"), "bob");
  await login(page, "alice");
  await history(page, doc.name);
  await openPair(page, 1, 2);
  await expect(page.locator(".comparison-toolbar")).toContainText("변경 1건");
  await expect(page.locator(".comparison-detail")).toContainText("150만원");
  await expect(page.locator(".comparison-sidebar")).toContainText("1행 2열");
  await expect(
    page
      .frameLocator('iframe[title="비교 버전 읽기 전용 페이지"]')
      .locator(".change-mark")
      .first(),
  ).toBeVisible();
});

test("REAL rhwp: history protects unsaved editing, then releases the editor and compares saved versions without writes", async ({
  page,
  request,
}) => {
  const doc = await seed(request);
  await save(request, doc, edit(base, "1,000", "1,500"), "bob");
  await login(page, "alice");
  await page.getByRole("button", { name: doc.name, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  const editor = page
    .frames()
    .find((f) => f.url().includes("/rhwp/index.html"))!;
  await editor
    .locator("#scroll-container canvas")
    .first()
    .click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(" 아직 저장하지 않은 메모");
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
  const before = await editor.evaluate(() => {
    const b = (window as any).hwpAssistantBridge;
    return {
      bytes: Array.from(b.exportSnapshot().bytes),
      revision: b.revision(),
      state: b.documentState(),
    };
  });
  await page.getByLabel("파일 작업", { exact: true }).click();
  await page.getByRole("button", { name: "문서 이력", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("문서 이력을 열기 전에 변경사항을 저장");
  await expect(page.getByRole("dialog", { name: "문서 이력", exact: true })).toHaveCount(0);
  const after = await editor.evaluate(() => {
    const b = (window as any).hwpAssistantBridge;
    return {
      bytes: Array.from(b.exportSnapshot().bytes),
      revision: b.revision(),
      state: b.documentState(),
    };
  });
  expect(after).toEqual(before);
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const savedUrl = (await page.getByRole("link", { name: "저장본 다운로드", exact: true }).getAttribute("href"))!;
  const savedBytes = await (await client(request, "alice", savedUrl, "GET")).body();
  expect(strFromU8(unzipSync(savedBytes)["Contents/section0.xml"])).toContain("아직 저장하지 않은 메모");
  await page.getByLabel("파일 작업", { exact: true }).click();
  await page.getByRole("button", { name: "문서 이력", exact: true }).click();
  await expect(page.locator(".block-editor-slot.active iframe")).toHaveCount(0);
  await expect.poll(async () => (await (await client(request, "alice", `/api/documents/${doc.id}/session`, "GET")).json()).active).toBe(false);
  await openPair(page, 1, 2);
  await page.getByRole("button", { name: "비교 닫기" }).click();
  await page.getByRole("dialog", { name: "문서 이력", exact: true }).getByRole("button", { name: "닫기", exact: true }).click();
  expect(await (await client(request, "alice", savedUrl, "GET")).body()).toEqual(savedBytes);
  expect(await (await client(request, "alice", `/api/documents/${doc.id}/versions`, "GET")).json()).toHaveLength(3);
  await page.getByRole("button", { name: doc.name, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  await expect(page.getByRole("link", { name: "저장본 다운로드", exact: true })).toHaveAttribute("href", savedUrl);
});

test("comparison handles permission errors and closing while loading without stale results", async ({
  page,
  request,
}) => {
  const doc = await seed(request);
  await save(request, doc, edit(base, "1,000", "1,500"), "bob");
  await login(page, "reader");
  await history(page, doc.name);
  await preparePair(page, 1, 2);
  await page.route("**/api/documents/*/compare?*", (route) =>
    route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "문서를 찾을 수 없습니다." }),
    }),
  );
  await page.getByRole("button", { name: "선택한 두 저장본 비교" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "문서를 찾을 수 없습니다",
  );
  await expect(page.locator(".comparison-pane")).toHaveCount(0);
  await page.getByRole("button", { name: "비교 닫기" }).click();
  await page.unroute("**/api/documents/*/compare?*");
  await page.route("**/api/documents/*/compare?*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 800));
    await route.continue().catch(() => {});
  });
  await page.getByRole("button", { name: "선택한 두 저장본 비교" }).click();
  await page.getByRole("button", { name: "비교 닫기" }).click();
  await expect(
    page.getByRole("heading", { name: "문서 이력", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".comparison-modal")).toHaveCount(0);
});

test("REAL rhwp: inserted pages locate each version independently", async ({
  page,
  request,
}) => {
  const make = (inserted: number, last: string) => {
    const native = HwpDocument.createEmpty();
    native.createBlankDocument();
    const paragraphs = [
      ...Array.from(
        { length: inserted },
        (_, i) => `새로 추가한 앞부분 문단 ${i + 1}입니다.`,
      ),
      ...Array.from({ length: 80 }, (_, i) =>
        i === 79 ? last : `여러 쪽 비교를 위한 고유 문단 ${i + 1}입니다.`,
      ),
    ];
    paragraphs.forEach((text, i) => {
      if (i)
        native.splitParagraph(0, i - 1, Array.from(paragraphs[i - 1]).length);
      native.insertText(0, i, 0, text);
    });
    const result = Buffer.from(native.exportHwpx());
    native.free();
    return result;
  };
  const doc = await seed(request, make(0, "마지막 검토 금액은 100만원입니다."));
  await save(
    request,
    doc,
    make(50, "마지막 검토 금액은 150만원입니다."),
    "bob",
  );
  await login(page, "alice");
  await history(page, doc.name);
  await openPair(page, 1, 2);
  await page
    .locator(".comparison-change-list button")
    .filter({ hasText: "마지막 검토 금액은 150만원" })
    .click();
  const left = page.locator('.comparison-pane[data-side="left"]'),
    right = page.locator('.comparison-pane[data-side="right"]');
  await expect(left.locator(".comparison-location")).toContainText(
    "실제 렌더링에서 확인",
  );
  await expect(right.locator(".comparison-location")).toContainText(
    "실제 렌더링에서 확인",
  );
  const position = async (pane: typeof left) =>
    (await pane.locator(".comparison-pages").innerText())
      .match(/(\d+) \/ (\d+)쪽/)!
      .slice(1)
      .map(Number);
  const l = await position(left),
    r = await position(right);
  expect(l[0]).toBeGreaterThan(1);
  expect(r[0]).toBeGreaterThan(l[0]);
  expect(r[1]).toBeGreaterThan(l[1]);
  await expect(
    page
      .frameLocator('iframe[title="비교 버전 읽기 전용 페이지"]')
      .locator(".change-mark")
      .first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "비교 문서 이전 쪽" }).click();
  await expect(right.getByRole("button", { name: "변경 위치" })).toBeVisible();
  await right.getByRole("button", { name: "변경 위치" }).click();
  await expect(
    page
      .frameLocator('iframe[title="비교 버전 읽기 전용 페이지"]')
      .locator(".change-mark")
      .first(),
  ).toBeVisible();
});
