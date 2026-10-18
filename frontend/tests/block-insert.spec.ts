import { test, expect, type Frame, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { unzipSync, strFromU8 } from "fflate";

// Explicit-save cases control the host's 20-second scheduler. Invoke the held
// tick during preparation to verify its autosave boundary; SDK iframe timers
// continue to run normally.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (window !== window.top) return;
    const nativeInterval = window.setInterval.bind(window);
    (window as any).__blockAutosaveTicks = [];
    window.setInterval = ((
      handler: TimerHandler,
      delay?: number,
      ...args: any[]
    ) => {
      if (delay !== 20000 || typeof handler !== "function")
        return nativeInterval(handler, delay, ...args);
      (window as any).__blockAutosaveTicks.push(() => handler(...args));
      return nativeInterval(() => {}, delay);
    }) as typeof window.setInterval;
  });
});

const sample = new URL(
  "../../samples/paragraph-and-table.hwpx",
  import.meta.url,
);
async function liveFrame(page: Page): Promise<Frame> {
  const element = await page
    .locator(".block-editor-slot.active iframe")
    .elementHandle();
  const frame = await element!.contentFrame();
  if (!frame) throw new Error("활성 편집기 프레임이 없습니다.");
  return frame;
}
async function exported(frame: Frame) {
  return Buffer.from(
    await frame.evaluate(
      () =>
        Array.from(
          (window as any).hwpAssistantBridge.exportSnapshot().bytes,
        ) as number[],
    ),
  );
}
async function evidence(frame: Frame) {
  return frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot());
}
async function focusStart(page: Page) {
  const frame = await liveFrame(page);
  await frame
    .getByRole("textbox", { name: "문서 편집 입력", exact: true })
    .focus();
  await page.keyboard.press("Control+Home");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Home");
  return frame;
}
async function openSample(page: Page, filename: string) {
  await page.goto("/");
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({
    name: filename,
    mimeType: "application/hwp+zip",
    buffer: await readFile(sample),
  });
  const row = page
    .getByRole("row")
    .filter({ has: page.getByRole("button", { name: filename, exact: true }) });
  const file = (await row
    .getByRole("link", { name: "다운로드", exact: true })
    .getAttribute("href"))!;
  await row.getByRole("button", { name: filename, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  return {
    file,
    document: file.split("/versions/")[0],
    version: file.split("/versions/")[1].split("/")[0],
  };
}
async function addUnsaved(page: Page) {
  const frame = await liveFrame(page);
  await frame
    .getByRole("textbox", { name: "문서 편집 입력", exact: true })
    .focus();
  await page.keyboard.press("Control+Home");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("미저장 대상 문단");
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("미저장 비대상 내용");
  await focusStart(page);
  return frame;
}
async function choose(page: Page, id: string, fields: Record<string, string>) {
  if (
    !(await page
      .getByLabel("표준 블록 삽입")
      .isVisible()
      .catch(() => false))
  )
    await page
      .getByRole("button", { name: "블록 삽입 열기", exact: true })
      .click();
  await page.getByLabel("삽입할 블록").selectOption(id);
  for (const [label, value] of Object.entries(fields))
    await page.getByLabel(label, { exact: true }).fill(value);
  await focusStart(page);
}
async function ready(page: Page) {
  const panel = page.getByLabel("표준 블록 삽입");
  await page
    .getByRole("button", { name: "현재 문단 뒤 삽입 미리보기", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await page
          .getByRole("button", { name: "확인한 블록 삽입", exact: true })
          .isEnabled()
          .catch(() => false)) ||
        (await panel
          .getByRole("alert")
          .isVisible()
          .catch(() => false)),
      { timeout: 90000 },
    )
    .toBe(true);
  if (
    await panel
      .getByRole("alert")
      .isVisible()
      .catch(() => false)
  )
    throw new Error(await panel.getByRole("alert").innerText());
  await expect(
    page.getByRole("button", { name: "확인한 블록 삽입", exact: true }),
  ).toBeEnabled({ timeout: 90000 });
  await expect(
    page.getByRole("img", { name: "블록 미리보기 1쪽", exact: true }),
  ).toBeVisible();
}
async function insert(page: Page) {
  await page
    .getByRole("button", { name: "확인한 블록 삽입", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "블록을 문단 뒤에 삽입" }),
  ).toBeVisible();
}
function content(bytes: Buffer) {
  return strFromU8(unzipSync(bytes)["Contents/section0.xml"]);
}

test("REAL engine block insert: title, summary and table preserve unsaved content, undo, save and reopen", async ({
  page,
  request,
}) => {
  const source = await openSample(page, `블록 기본 검증 ${Date.now()}.hwpx`);
  const original = await addUnsaved(page),
    before = await exported(original),
    originalEvidence = await evidence(original);
  const versions = await (
    await request.get(source.document + "/versions")
  ).json();
  await choose(page, "title-frame", { 제목: "금융 보안 운영 보고" });
  await ready(page);
  await page.evaluate(() =>
    (window as any).__blockAutosaveTicks.forEach((tick: () => void) => tick()),
  );
  expect(await exported(original)).toEqual(before);
  expect((await evidence(original)).state.documentEpoch).toBe(
    originalEvidence.state.documentEpoch,
  );
  expect(
    await (await request.get(source.document + "/versions")).json(),
  ).toEqual(versions);
  const staged = await (await page
    .locator(".block-editor-slot:not(.active) iframe")
    .elementHandle())!.contentFrame();
  await staged!.evaluate(() => {
    (window as any).__blockSentinel = "stable staging context";
  });
  await insert(page);
  const inserted = await liveFrame(page);
  expect(inserted).toBe(staged);
  expect(await inserted.evaluate(() => (window as any).__blockSentinel)).toBe(
    "stable staging context",
  );
  expect(content(await exported(inserted))).toContain("금융 보안 운영 보고");
  await page
    .getByRole("button", { name: "방금 삽입 취소", exact: true })
    .click();
  expect(await liveFrame(page)).toBe(original);
  expect(await exported(original)).toEqual(before);
  await ready(page);
  await insert(page);
  await choose(page, "summary", {
    말머리: "요약",
    내용: "우선 과제 두 건을 확인하고 담당과 기한을 관리합니다.",
  });
  await ready(page);
  await insert(page);
  await choose(page, "action-table", {
    "표 제목": "90일 대응 조치",
    조치1: "접근 권한 점검",
    담당1: "보안팀",
    기한1: "10월 20일",
    조치2: "복구 훈련",
    담당2: "운영팀",
    기한2: "11월 1일",
  });
  await ready(page);
  await insert(page);
  const output = await exported(await liveFrame(page)),
    files = unzipSync(output),
    section = strFromU8(files["Contents/section0.xml"]),
    header = strFromU8(files["Contents/header.xml"]);
  for (const text of [
    "미저장 대상",
    "미저장 비대상 내용",
    "금융 보안 운영 보고",
    "접근 권한 점검",
    "보안팀",
    "10월 20일",
    "복구 훈련",
    "운영팀",
    "11월 1일",
    "두 번째 셀",
  ])
    expect(section).toContain(text);
  const styles = await page.evaluate(
    ({ section, header }) => {
      const xml = (value: string) =>
        new DOMParser().parseFromString(value, "application/xml");
      const s = xml(section),
        h = xml(header),
        nodes = (root: Document | Element, name: string) =>
          Array.from(root.getElementsByTagNameNS("*", name));
      const title = nodes(s, "p").find(
        (p) =>
          nodes(p, "t")
            .map((t) => t.textContent)
            .join("") === "금융 보안 운영 보고",
      )!;
      const para = nodes(h, "paraPr").find(
        (p) => p.getAttribute("id") === title.getAttribute("paraPrIDRef"),
      )!;
      const borderId = nodes(para, "border")[0]?.getAttribute(
          "borderFillIDRef",
        ),
        border = nodes(h, "borderFill").find(
          (p) => p.getAttribute("id") === borderId,
        )!;
      const summary = nodes(s, "p").find((p) =>
        nodes(p, "t")
          .map((t) => t.textContent)
          .join("")
          .startsWith("□ (요약)"),
      )!;
      const labelRun = nodes(summary, "run").find((run) =>
        nodes(run, "t")
          .map((t) => t.textContent)
          .join("")
          .includes("(요약)"),
      )!;
      const character = nodes(h, "charPr").find(
        (p) => p.getAttribute("id") === labelRun.getAttribute("charPrIDRef"),
      )!;
      return {
        tables: nodes(s, "tbl").length,
        boldLabel: nodes(character, "bold").length > 0,
        top: nodes(border, "topBorder")[0]?.getAttribute("type"),
        bottom: nodes(border, "bottomBorder")[0]?.getAttribute("type"),
        left: nodes(border, "leftBorder")[0]?.getAttribute("type"),
        right: nodes(border, "rightBorder")[0]?.getAttribute("type"),
        topColor: nodes(border, "topBorder")[0]?.getAttribute("color"),
      };
    },
    { section, header },
  );
  expect(styles.tables).toBe(2);
  expect(styles.boldLabel).toBe(true);
  expect(styles.top).not.toBe("NONE");
  expect(styles.bottom).not.toBe("NONE");
  expect(styles.left).toBe("NONE");
  expect(styles.right).toBe("NONE");
  expect(styles.topColor).not.toBe("#000000");
  await page
    .getByRole("button", { name: "블록 삽입 닫기", exact: true })
    .click();
  expect(content(await exported(await liveFrame(page)))).toContain(
    "접근 권한 점검",
  );
  expect(
    await (await request.get(source.document + "/versions")).json(),
  ).toEqual(versions);
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  await page.screenshot({
    path: "test-results/block-insert-final.png",
    fullPage: true,
  });
  const saved = (await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href"))!;
  expect(saved).not.toBe(source.file);
  expect(await (await request.get(source.file)).body()).toEqual(
    await readFile(sample),
  );
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  await page.getByRole("button", { name: /^블록 기본 검증 .*\.hwpx$/ }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  const reopened = content(await exported(await liveFrame(page)));
  expect(reopened).toContain("미저장 비대상 내용");
  expect(reopened).toContain("복구 훈련");
});

test("REAL engine block insert: preparation retry and lost save response preserve frozen output and later edits", async ({
  page,
  request,
}) => {
  const source = await openSample(page, `블록 재시도 ${Date.now()}.hwpx`);
  await addUnsaved(page);
  await choose(page, "summary", {
    말머리: "조치",
    내용: "동일 요청의 출력과 후속 편집을 각각 보존합니다.",
  });
  const previewBodies: any[] = [],
    verifyBodies: any[] = [];
  let dropPreview = true,
    dropVerify = true;
  await page.route("**/api/documents/*/block-preview", async (route) => {
    previewBodies.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    if (dropPreview) {
      dropPreview = false;
      await route.abort("connectionreset");
    } else await route.fulfill({ response });
  });
  await page.route("**/api/documents/*/block-verify", async (route) => {
    verifyBodies.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    if (dropVerify) {
      dropVerify = false;
      await route.abort("connectionreset");
    } else await route.fulfill({ response });
  });
  await page
    .getByRole("button", { name: "현재 문단 뒤 삽입 미리보기", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "같은 준비 요청 재확인" }),
  ).toBeVisible({ timeout: 90000 });
  await page.getByRole("button", { name: "같은 준비 요청 재확인" }).click();
  await expect.poll(() => verifyBodies.length, { timeout: 90000 }).toBe(1);
  await expect(
    page.getByRole("button", { name: "같은 준비 요청 재확인" }),
  ).toBeVisible({ timeout: 90000 });
  await expect(
    page.getByLabel("표준 블록 삽입").getByRole("alert"),
  ).toContainText("같은 준비 요청");
  await expect(page.locator(".block-editor-slot iframe")).toHaveCount(2);
  await page.getByRole("button", { name: "같은 준비 요청 재확인" }).click();
  await expect(
    page.getByRole("button", { name: "확인한 블록 삽입", exact: true }),
  ).toBeEnabled({ timeout: 90000 });
  expect(previewBodies).toHaveLength(2);
  expect(previewBodies[0]).toEqual(previewBodies[1]);
  expect(verifyBodies).toHaveLength(2);
  expect(verifyBodies[0]).toEqual(verifyBodies[1]);
  await insert(page);
  await page
    .getByRole("button", { name: "블록 삽입 닫기", exact: true })
    .click();
  const versionsBefore = await (
    await request.get(source.document + "/versions")
  ).json();
  const savedBodies: { key: string; body: any }[] = [];
  let dropSave = true;
  await page.route("**/api/documents/*/versions?*", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    savedBodies.push({
      key: route.request().headers()["idempotency-key"],
      body: route.request().postDataJSON(),
    });
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    if (dropSave) {
      dropSave = false;
      await route.abort("connectionreset");
    } else await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 실패");
  await focusStart(page);
  await page.keyboard.insertText("후속 사용자 편집 ");
  await expect
    .poll(async () => content(await exported(await liveFrame(page))))
    .toContain("후속 사용자 편집");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
  expect(savedBodies).toHaveLength(2);
  expect(savedBodies[1]).toEqual(savedBodies[0]);
  expect(
    await (await request.get(source.document + "/versions")).json(),
  ).toHaveLength(versionsBefore.length + 1);
  const frozenFile = (await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href"))!;
  expect(content(await (await request.get(frozenFile)).body())).not.toContain(
    "후속 사용자 편집",
  );
  expect(content(await exported(await liveFrame(page)))).toContain(
    "후속 사용자 편집",
  );
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const latest = (await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href"))!;
  expect(content(await (await request.get(latest)).body())).toContain(
    "후속 사용자 편집",
  );
  expect(content(await (await request.get(latest)).body())).toContain(
    "동일 요청의 출력",
  );
});

test("REAL engine block insert: stale edits reject insertion and late preparation after close releases every stage", async ({
  page,
  request,
}) => {
  const source = await openSample(page, `블록 상태 경계 ${Date.now()}.hwpx`);
  const original = await addUnsaved(page);
  await choose(page, "title-frame", { 제목: "늦은 결과 차단" });
  await ready(page);
  await focusStart(page);
  await page.keyboard.insertText("준비 뒤 추가 입력 ");
  const changed = await exported(original),
    epoch = (await evidence(original)).state.documentEpoch;
  await page
    .getByRole("button", { name: "확인한 블록 삽입", exact: true })
    .click();
  await expect(
    page.getByLabel("표준 블록 삽입").getByRole("alert"),
  ).toContainText("문서나 커서가 바뀌었습니다");
  expect(await liveFrame(page)).toBe(original);
  expect(await exported(original)).toEqual(changed);
  await expect(page.locator(".block-editor-slot iframe")).toHaveCount(1);
  let release!: () => void;
  const barrier = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started!: () => void;
  const requestStarted = new Promise<void>((resolve) => {
    started = resolve;
  });
  let settled!: () => void;
  const requestSettled = new Promise<void>((resolve) => {
    settled = resolve;
  });
  await page.route("**/api/documents/*/block-preview", async (route) => {
    try {
      const response = await route.fetch();
      started();
      await barrier;
      await route.fulfill({ response }).catch(() => {});
    } finally {
      settled();
    }
  });
  try {
    await page
      .getByRole("button", { name: "현재 문단 뒤 삽입 미리보기", exact: true })
      .click();
    await requestStarted;
    await page
      .getByLabel("표준 블록 삽입")
      .getByRole("button", { name: "닫기", exact: true })
      .click();
    release();
    await requestSettled;
    await expect(page.locator(".block-editor-slot iframe")).toHaveCount(1);
    await expect(
      page.getByRole("button", { name: "저장", exact: true }),
    ).toBeEnabled();
    expect(await exported(original)).toEqual(changed);
    expect((await evidence(original)).state.documentEpoch).toBe(epoch);
    expect(
      await (await request.get(source.document + "/versions")).json(),
    ).toHaveLength(1);
  } finally {
    release();
    await page.unroute("**/api/documents/*/block-preview");
  }
});

test("REAL authenticated block insert: revoked editor access preserves the live buffer and creates no version", async ({
  page,
  request,
  browser,
}) => {
  test.skip(
    !process.env.HWP_E2E_AUTH_URL,
    "Separate authenticated API/Vite required",
  );
  const authUrl = process.env.HWP_E2E_AUTH_URL!,
    alice = "alice-" + "a".repeat(32),
    bob = "bob-" + "b".repeat(32),
    headers = { Authorization: "Bearer " + alice };
  const filename = `블록 권한 ${Date.now()}.hwpx`;
  const uploaded = await request.post(
    authUrl +
      "/api/documents?" +
      new URLSearchParams({ name: filename, document_id: randomUUID() }),
    {
      headers: { ...headers, "Idempotency-Key": randomUUID() },
      data: await readFile(sample),
    },
  );
  expect(uploaded.ok()).toBeTruthy();
  const doc = await uploaded.json();
  await request.put(`${authUrl}/api/documents/${doc.id}/access`, {
    headers,
    data: { user_id: "bob", role: "editor" },
  });
  await page.goto(authUrl);
  await page.getByLabel("인증 토큰").fill(bob);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("button", { name: filename, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  const original = await addUnsaved(page);
  await choose(page, "summary", {
    말머리: "요약",
    내용: "권한 회수 뒤 삽입을 거부합니다.",
  });
  await ready(page);
  const before = await exported(original);
  const revoked = await request.delete(
    `${authUrl}/api/documents/${doc.id}/access/bob`,
    { headers },
  );
  expect(revoked.ok()).toBeTruthy();
  await page
    .getByRole("button", { name: "확인한 블록 삽입", exact: true })
    .click();
  await expect(
    page.getByLabel("표준 블록 삽입").getByRole("alert"),
  ).toContainText("기존 편집 내용은 보존");
  expect(await liveFrame(page)).toBe(original);
  expect(await exported(original)).toEqual(before);
  await expect(page.locator(".block-editor-slot iframe")).toHaveCount(1);
  expect(
    await (
      await request.get(`${authUrl}/api/documents/${doc.id}/versions`, {
        headers,
      })
    ).json(),
  ).toHaveLength(1);
  expect(
    await (
      await request.get(
        `${authUrl}/api/documents/${doc.id}/versions/${doc.current_version}/file`,
        { headers },
      )
    ).body(),
  ).toEqual(await readFile(sample));
  await request.put(`${authUrl}/api/documents/${doc.id}/access`, {
    headers,
    data: { user_id: "bob", role: "reader" },
  });
  const readerContext = await browser.newContext({ baseURL: authUrl });
  try {
    const reader = await readerContext.newPage();
    await reader.goto("/");
    await reader.getByLabel("인증 토큰").fill(bob);
    await reader.getByRole("button", { name: "로그인", exact: true }).click();
    await reader.getByRole("button", { name: filename, exact: true }).click();
    await expect(
      reader.getByText("열람 권한 ·", { exact: false }),
    ).toBeVisible();
    await expect(
      reader.getByRole("button", { name: "블록 삽입 열기", exact: true }),
    ).toHaveCount(0);
  } finally {
    await readerContext.close();
  }
});
