import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { unzipSync, strFromU8 } from "fflate";
test("REAL rhwp: create, Korean edit, save, original upload, edit version, download and reopen", async ({
  page,
  request,
}) => {
  const filename = `왕복 시험 ${Date.now()}.hwpx`;
  const external: string[] = [];
  page.on("request", (r) => {
    if (
      /^https?:/.test(r.url()) &&
      !r.url().startsWith((process.env.HWP_E2E_URL || "http://127.0.0.1:5173") + "/")
    )
      external.push(r.url());
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByRole("button", { name: "빈 문서 만들기" }).click();
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음", {
    timeout: 90000,
  });
  const frame = page.frames().find((f) => f.url().includes("/rhwp/"))!;
  const canvas = frame.locator("#scroll-container canvas").first();
  await canvas.click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+Home");
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("합성 문서 원문");
  await expect
    .poll(() =>
      frame.evaluate(
        () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
      ),
    )
    .toContain("합성 문서 원문");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const downloadUrl = await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href");
  const original = await (await request.get(downloadUrl!)).body();
  expect(original.subarray(0, 2).toString()).toBe("PK");
  await page.getByRole("button", { name: "← 문서 목록" }).click();
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({
    name: filename,
    mimeType: "application/hwp+zip",
    buffer: original,
  });
  await page.getByRole("button", { name: filename, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  const frame2 = page.frames().find((f) => f.url().includes("/rhwp/"))!;
  const current = await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href");
  expect(
    createHash("sha256")
      .update(await (await request.get(current!)).body())
      .digest("hex"),
  ).toBe(createHash("sha256").update(original).digest("hex"));
  await frame2
    .locator("#scroll-container canvas")
    .first()
    .click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(" 웹 수정 확인");
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
  await page.getByLabel("추가 도구", { exact: true }).click();
  await page.getByRole("button", { name: "AI 검토 열기" }).click();
  await expect(page.getByRole("button", { name: "현재 문단 검토" })).toBeDisabled();
  const beforeConnection = await frame2.evaluate(
    () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
  );
  let connectionChecks = 0;
  // Exercise an explicit outage without depending on the configured local AI.
  await page.route("**/api/ai/models", async (route) => {
    connectionChecks++;
    await route.fulfill({ status: 503, json: { detail: "TEST MOCK: separate AI unavailable" } });
  });
  await page.getByRole("button", { name: "AI 연결 확인", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("별도 AI에 연결할 수 없습니다");
  expect(connectionChecks).toBe(1);
  await expect(page.getByRole("button", { name: "현재 문단 검토" })).toBeDisabled();
  expect(await frame2.evaluate(
    () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
  )).toBe(beforeConnection);
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const editedUrl = await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href");
  expect(editedUrl).not.toBe(current);
  expect(await (await request.get(current!)).body()).toEqual(original);
  await page.getByRole("button", { name: "← 문서 목록" }).click();
  await page.getByRole("button", { name: filename, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  const reopened = page.frames().find((f) => f.url().includes("/rhwp/"))!;
  await reopened
    .locator("#scroll-container canvas")
    .first()
    .click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  await expect
    .poll(() =>
      reopened.evaluate(
        () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
      ),
    )
    .toContain("웹 수정 확인");
  await page.screenshot({
    path: "test-results/real-editor.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
  expect(external).toEqual([]);
});

test("REAL engine: character/paragraph formatting and table cells survive reload", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "빈 문서 만들기" }).click();
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음", {
    timeout: 90000,
  });
  const frame = page.frames().find((f) => f.url().includes("/rhwp/"))!;
  await frame
    .locator("#scroll-container canvas")
    .first()
    .click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+Home");
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("서식 시험");
  await page.keyboard.press("Home");
  await page.keyboard.press("Shift+End");
  await page.keyboard.press("Control+b");
  await frame.getByRole("button", { name: "가운데 정렬", exact: true }).click();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await frame.locator('button[data-cmd="table:create"]').click();
  const cell = frame.locator('[data-row="1"][data-col="1"]');
  await cell.hover();
  await cell.click();
  await page.keyboard.insertText("표 셀 원문");
  await page.keyboard.press("Tab");
  await page.keyboard.insertText("두 번째 셀");
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("End");
  await page.keyboard.insertText(" 수정");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const url = (await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href"))!;
  const bytes = await (await request.get(url)).body();
  const files = unzipSync(bytes);
  const section = strFromU8(files["Contents/section0.xml"]);
  const header = strFromU8(files["Contents/header.xml"]);
  expect(section).toContain("표 셀 원문 수정");
  expect(section).toContain("두 번째 셀");
  expect(section).toContain(":tbl");
  expect(header).toContain(":bold");
  expect(header).toContain("CENTER");
  const filename = `표 서식 ${Date.now()}.hwpx`;
  await page.getByRole("button", { name: "← 문서 목록" }).click();
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({
    name: filename,
    mimeType: "application/hwp+zip",
    buffer: bytes,
  });
  await page.getByRole("button", { name: filename, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  // Force a second serialization through the engine; original-byte download is insufficient.
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const again = (await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href"))!;
  const againFiles = unzipSync(await (await request.get(again)).body());
  expect(strFromU8(againFiles["Contents/section0.xml"])).toContain(
    "표 셀 원문 수정",
  );
  await page.screenshot({
    path: "test-results/table-editor.png",
    fullPage: true,
  });
});

test("REAL engine + MOCK AI transport: approval, stale proposal and undo", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "빈 문서 만들기" }).click();
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음", {
    timeout: 90000,
  });
  const frame = page.frames().find((f) => f.url().includes("/rhwp/"))!;
  async function focus() {
    await frame
      .locator("#scroll-container canvas")
      .first()
      .click({ position: { x: 160, y: 150 }, force: true });
    await page.keyboard.press("Control+End");
  }
  await focus();
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("회의 내용을 검토합니다.");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const original = await frame.evaluate(
    () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
  );
  await page.route("**/api/ai/models", route => route.fulfill({ json: { configured_model: "synthetic", models: ["synthetic"], model_available: true } }));
  await page.route("**/api/ai/review", async (route) => {
    const data = route.request().postDataJSON();
    await route.fulfill({
      json: {
        ...data,
        replacement: "회의 내용을 명확히 검토합니다.",
        reason: "TEST MOCK: 표현 개선",
        original_sha256: createHash("sha256")
          .update(data.original)
          .digest("hex"),
      },
    });
  });
  await page.getByLabel("추가 도구", { exact: true }).click();
  await page.getByRole("button", { name: "AI 검토 열기" }).click();
  await page.getByRole("button", { name: "AI 연결 확인", exact: true }).click();
  await page.getByRole("button", { name: "현재 문단 검토" }).click();
  await expect(
    page.getByRole("button", { name: "승인하고 적용" }),
  ).toBeVisible();
  expect(
    await frame.evaluate(
      () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
    ),
  ).toBe(original);
  await page.getByRole("button", { name: "승인하고 적용" }).click();
  await expect
    .poll(() =>
      frame.evaluate(
        () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
      ),
    )
    .toBe("회의 내용을 명확히 검토합니다.");
  await focus();
  await page.keyboard.press("Control+z");
  await expect
    .poll(() =>
      frame.evaluate(
        () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
      ),
    )
    .toBe(original);
  await page.keyboard.press("Control+Shift+z");
  await expect
    .poll(() =>
      frame.evaluate(
        () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
      ),
    )
    .toBe("회의 내용을 명확히 검토합니다.");
  await page.getByRole("button", { name: "AI 연결 확인", exact: true }).click();
  await page.getByRole("button", { name: "현재 문단 검토" }).click();
  await expect(
    page.getByRole("button", { name: "승인하고 적용" }),
  ).toBeVisible();
  await focus();
  await page.keyboard.insertText(" 추가 편집");
  await page.getByRole("button", { name: "승인하고 적용" }).click();
  await expect(page.getByRole("alert")).toContainText("다시 검토");
  expect(
    await frame.evaluate(
      () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
    ),
  ).toContain("추가 편집");
});

test("REAL engine: in-flight edit stays dirty, lost response retries once", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "빈 문서 만들기" }).click();
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음", {
    timeout: 90000,
  });
  const frame = page.frames().find((f) => f.url().includes("/rhwp/"))!;
  const focus = async () => {
    await frame
      .locator("#scroll-container canvas")
      .first()
      .click({ position: { x: 160, y: 150 }, force: true });
    await page.keyboard.press("Control+End");
  };
  await focus();
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("첫 저장");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const firstUrl = (await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href"))!;
  const docPath = firstUrl.split("/versions/")[0];
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => (release = r));
  let entered: () => void = () => {};
  const started = new Promise<void>((r) => (entered = r));
  await page.route(
    "**/api/documents/*/versions?*",
    async (route) => {
      const response = await route.fetch();
      entered();
      await gate;
      await route.fulfill({ response });
    },
    { times: 1 },
  );
  await focus();
  await page.keyboard.insertText(" 서버 저장 대상");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await started;
  await focus();
  await page.keyboard.insertText(" 저장 중 추가");
  release();
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
  let first = true;
  await page.route("**/api/documents/*/versions?*", async (route) => {
    if (first) {
      first = false;
      await route.fetch();
      await route.abort("failed");
    } else await route.continue();
  });
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 실패");
  const count = (await (await request.get(docPath + "/versions")).json())
    .length;
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  expect((await (await request.get(docPath + "/versions")).json()).length).toBe(
    count,
  );
});
