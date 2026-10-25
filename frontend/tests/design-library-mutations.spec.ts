import { test, expect, type Page, type Route } from "@playwright/test";

// Exercise the actual DesignLibrary component with synthetic API responses.
// No document engine, document save, AI request, or production account is used.
const settings = { table_header_fill: "#eeeeee", table_border_color: "#334455" };
const existing = { id: "synthetic-existing-design", name: "기존 합성 디자인", kind: "table", settings };
type Save = { id: string; name: string; kind: string; settings: typeof settings };
const respond = (route: Route, value: unknown, status = 200) => route.fulfill({
  status, contentType: "application/json", body: JSON.stringify(value),
});

async function fixture(page: Page) {
  await page.route("**/design-library-mutations-test.html", route => route.fulfill({
    contentType: "text/html",
    body: '<!doctype html><html lang="ko"><meta charset="utf-8"><div id="root"></div><script type="module" src="/tests/fixtures/design-library-mutations.tsx"></script></html>',
  }));
}

test("MOCK design library: saving notifies the parent only after the confirmed response", async ({ page }) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const requests: Save[] = [], writes: string[] = [];
  await fixture(page);
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    if (request.method() !== "GET") writes.push(`${request.method()} ${path}`);
    if (path === "/design-fragments" && request.method() === "GET") return respond(route, { items: [existing] });
    if (path === "/design-fragments" && request.method() === "POST") {
      const body = request.postDataJSON() as Save;
      requests.push(body);
      await held;
      return respond(route, body);
    }
    return respond(route, { detail: `Unexpected request: ${path}` }, 404);
  });
  await page.goto("/design-library-mutations-test.html");
  const library = page.getByRole("region", { name: "디자인 보관함", exact: true });
  const changes = page.getByLabel("확인된 디자인 변경", { exact: true });
  await expect(library.getByText(existing.name, { exact: true })).toBeVisible();
  await page.getByLabel("디자인 이름", { exact: true }).fill("확인 후 추가되는 합성 디자인");
  await page.getByRole("button", { name: "디자인 저장", exact: true }).click();
  try {
    await expect.poll(() => requests.length).toBe(1);
    await expect(changes).toHaveText("[]");
    await expect(library.getByText("확인 후 추가되는 합성 디자인", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "저장 결과 재확인", exact: true })).toBeDisabled();
    await expect(library.getByText(existing.name, { exact: true })).toBeVisible();
  } finally { release(); }
  await expect(changes).toHaveText(JSON.stringify([requests[0].id]));
  await expect(library.getByText("확인 후 추가되는 합성 디자인", { exact: true })).toBeVisible();
  await expect(page.getByLabel("디자인 이름", { exact: true })).toHaveValue("");
  expect(requests[0]).toEqual({ id: expect.any(String), name: "확인 후 추가되는 합성 디자인", kind: "table", settings });
  expect(writes).toEqual(["POST /design-fragments"]);
});

test("MOCK design library: failed save retains the frozen request and emits one change after explicit retry", async ({ page }) => {
  const requests: Save[] = [], writes: string[] = [];
  await fixture(page);
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    if (request.method() !== "GET") writes.push(`${request.method()} ${path}`);
    if (path === "/design-fragments" && request.method() === "GET") return respond(route, { items: [existing] });
    if (path === "/design-fragments" && request.method() === "POST") {
      requests.push(request.postDataJSON() as Save);
      return requests.length === 1 ? respond(route, { detail: "합성 응답 유실" }, 503) : respond(route, requests[1]);
    }
    return respond(route, { detail: `Unexpected request: ${path}` }, 404);
  });
  await page.goto("/design-library-mutations-test.html");
  const library = page.getByRole("region", { name: "디자인 보관함", exact: true });
  const changes = page.getByLabel("확인된 디자인 변경", { exact: true });
  await expect(library.getByText(existing.name, { exact: true })).toBeVisible();
  const name = page.getByLabel("디자인 이름", { exact: true });
  await name.fill("입력 동결 합성 디자인");
  await page.getByRole("button", { name: "디자인 저장", exact: true }).click();
  await expect(library.getByRole("alert")).toContainText("서버에서 요청을 처리하지 못했습니다.");
  await expect(changes).toHaveText("[]");
  await expect(name).toBeDisabled();
  await expect(page.getByRole("combobox", { name: "저장할 디자인", exact: true })).toBeDisabled();
  await expect(library.getByText(existing.name, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "합성 설정 변경", exact: true }).click();
  await expect(name).toHaveValue("입력 동결 합성 디자인");
  expect(requests).toHaveLength(1);
  await page.getByRole("button", { name: "저장 결과 재확인", exact: true }).click();
  await expect(changes).toHaveText(JSON.stringify([requests[0].id]));
  await expect(library.getByRole("alert")).toHaveCount(0);
  await expect(library.locator("article").filter({ has: page.getByText("입력 동결 합성 디자인", { exact: true }) })).toHaveCount(1);
  await expect(library.getByText(existing.name, { exact: true })).toBeVisible();
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
  expect(requests[1].settings).toEqual(settings);
  expect(writes).toEqual(["POST /design-fragments", "POST /design-fragments"]);
});
