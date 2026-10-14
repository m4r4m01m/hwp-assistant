import { test, expect, type Page, type Frame } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { randomUUID, createHash } from "node:crypto";
import { unzipSync, zipSync, strFromU8, strToU8 } from "fflate";

const heading1 = "Ⅰ. 금융 보안 🔐 현황",
  heading2 =
    "1. 접근 권한 점검과 운영 통제의 책임·기한·증거를 함께 관리하고 부서 간 승인 흐름과 변경 이력을 일관되게 유지하는 상세 실행 기준 및 위험 대응 점검 계획",
  heading3 = "Ⅱ. 90일 대응 계획";
const sha = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");
test.beforeEach(async ({ page }) => {
  // Explicit-save cases hold only the application's autosave scheduler. Engine
  // timers continue normally, and the captured tick is exercised during preparation.
  await page.addInitScript(() => {
    if (window !== window.top) return;
    const native = window.setInterval.bind(window);
    (window as any).__tocTicks = [];
    window.setInterval = ((
      handler: TimerHandler,
      delay?: number,
      ...args: any[]
    ) => {
      if (delay !== 20000 || typeof handler !== "function")
        return native(handler, delay, ...args);
      (window as any).__tocTicks.push(() => handler(...args));
      return native(() => {}, delay);
    }) as typeof window.setInterval;
  });
});
async function fixture(tenPages = false) {
  const files = unzipSync(
    await readFile(
      new URL("../../samples/paragraph-and-table.hwpx", import.meta.url),
    ),
  );
  const section = strFromU8(files["Contents/section0.xml"]);
  const original = section.match(/<hp:p id="1"[^>]*>[\s\S]*?<\/hp:p>/)![0];
  const paragraph = (text: string, id: number, pageBreak = false) =>
    original
      .replace(/id="1"/, `id="${id}"`)
      .replace(/pageBreak="0"/, `pageBreak="${pageBreak ? 1 : 0}"`)
      .replace(/<hp:t>[\s\S]*?<\/hp:t>/, `<hp:t>${text}</hp:t>`)
      .replace(/<hp:linesegarray>[\s\S]*?<\/hp:linesegarray>/, "");
  const paragraphs = [
    paragraph("보고서 표지", 100),
    paragraph(heading1, 101),
    paragraph("보안 운영 현황을 점검하고 증거를 보관합니다.", 102),
    paragraph(heading2, 103),
    paragraph("담당자가 권한과 만료일을 확인합니다.", 104),
    ...(tenPages
      ? Array.from({ length: 8 }, (_, index) =>
          paragraph(`합성 점검 자료 ${index + 1}쪽의 본문`, 200 + index, true),
        )
      : []),
    paragraph(heading3, 105, true),
    paragraph("정기 복구 훈련과 위험 점검을 수행합니다.", 106),
  ];
  files["Contents/section0.xml"] = strToU8(
    section.replace(original, paragraphs.join("")),
  );
  return Buffer.from(zipSync(files));
}
async function live(page: Page): Promise<Frame> {
  const handle = await page
    .locator(".block-editor-slot.active iframe")
    .elementHandle();
  const frame = await handle!.contentFrame();
  if (!frame) throw new Error("활성 엔진이 없습니다");
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
function content(bytes: Buffer) {
  return strFromU8(unzipSync(bytes)["Contents/section0.xml"]);
}
async function open(page: Page, name: string, source?: Buffer) {
  source = source ?? (await fixture());
  await page.goto("/");
  await page
    .getByLabel("HWPX 업로드", { exact: true })
    .setInputFiles({ name, mimeType: "application/hwp+zip", buffer: source });
  const row = page
    .getByRole("row")
    .filter({ has: page.getByRole("button", { name, exact: true }) });
  const file = (await row
    .getByRole("link", { name: "다운로드", exact: true })
    .getAttribute("href"))!;
  await row.getByRole("button", { name, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  return { file, document: file.split("/versions/")[0], name, bytes: source };
}
async function typeEnd(page: Page, text: string) {
  const frame = await live(page);
  await frame
    .getByRole("textbox", { name: "문서 편집 입력", exact: true })
    .focus();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(text);
  await expect.poll(async () => content(await exported(frame))).toContain(text);
  return frame;
}
async function analyze(page: Page, isNew = true) {
  if (
    !(await page
      .getByLabel("목차 관리", { exact: true })
      .isVisible()
      .catch(() => false))
  )
    await page.getByRole("button", { name: "목차 관리", exact: true }).click();
  await page
    .getByRole("button", { name: "현재 문서의 목차·제목 확인", exact: true })
    .click();
  await expect(
    page.getByLabel(`목차 제목 ${heading1}`, { exact: true }),
  ).toBeVisible({ timeout: 90000 });
  if (isNew) {
    for (const title of [heading1, heading2, heading3])
      await page.getByLabel(`목차 제목 ${title}`, { exact: true }).check();
    await page
      .getByLabel(`제목 계층 ${heading2}`, { exact: true })
      .selectOption("2");
    const options = page.getByLabel("목차를 넣을 문단 뒤").locator("option");
    const value = await options
      .filter({ hasText: "보고서 표지" })
      .getAttribute("value");
    await page.getByLabel("목차를 넣을 문단 뒤").selectOption(value!);
  }
}
async function ready(page: Page, existing = false) {
  await page
    .getByRole("button", {
      name: existing
        ? "목차 쪽수 갱신 미리보기"
        : "선택한 제목으로 목차 미리보기",
      exact: true,
    })
    .click();
  await expect
    .poll(
      async () =>
        (await page
          .getByRole("button", { name: "검증한 목차 반영", exact: true })
          .isEnabled()
          .catch(() => false)) ||
        (await page
          .getByLabel("목차 관리", { exact: true })
          .getByRole("alert")
          .isVisible()
          .catch(() => false)),
      { timeout: 90000 },
    )
    .toBe(true);
  const alert = page
    .getByLabel("목차 관리", { exact: true })
    .getByRole("alert");
  if (await alert.isVisible().catch(() => false))
    throw new Error(await alert.innerText());
  await expect(
    page.getByRole("img", { name: "목차 미리보기 1쪽", exact: true }),
  ).toBeVisible();
}
async function insert(page: Page) {
  await page
    .getByRole("button", { name: "검증한 목차 반영", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "검증한 목차를 반영했습니다" }),
  ).toBeVisible({ timeout: 90000 });
}
async function closePanel(page: Page) {
  await page
    .getByLabel("목차 관리", { exact: true })
    .getByRole("button", { name: "닫기", exact: true })
    .click();
}

test("REAL engine TOC: verified physical pages, hierarchy, unsaved content, explicit refresh, save and reopen", async ({
  page,
  request,
}, testInfo) => {
  const source = await open(
    page,
    `목차 실제 렌더 ${Date.now()}.hwpx`,
    await fixture(true),
  );
  const original = await typeEnd(page, "미저장 비대상 문장"),
    before = await exported(original);
  const state = await original.evaluate(() =>
    (window as any).hwpAssistantBridge.documentState(),
  );
  const verifyBodies: any[] = [],
    verifies: any[] = [];
  await page.route("**/api/documents/*/toc-verify", async (route) => {
    verifyBodies.push(route.request().postDataJSON());
    const response = await route.fetch();
    verifies.push(await response.json());
    await route.fulfill({ response });
  });
  await analyze(page);
  await ready(page);
  expect(await exported(original)).toEqual(before);
  expect(
    await original.evaluate(() =>
      (window as any).hwpAssistantBridge.documentState(),
    ),
  ).toEqual(state);
  await page.evaluate(() =>
    (window as any).__tocTicks.forEach((tick: () => void) => tick()),
  );
  expect(
    await (await request.get(source.document + "/versions")).json(),
  ).toHaveLength(1);
  expect(verifyBodies.length).toBeGreaterThanOrEqual(2);
  expect(verifyBodies.length).toBeLessThanOrEqual(3);
  const final = verifyBodies.at(-1),
    measured = final.layout_evidence;
  expect(verifies.at(-1).converged).toBe(true);
  expect(measured.page_count).toBeGreaterThanOrEqual(10);
  expect(measured.headings.at(-1).page).toBeGreaterThan(
    measured.headings[0].page,
  );
  expect(sha(Buffer.from(final.engine_output_base64, "base64"))).toBe(
    measured.export_sha256,
  );
  const stage = await (await page
    .locator(".block-editor-slot:not(.active) iframe")
    .elementHandle())!.contentFrame();
  const bridgeChecks = await stage!.evaluate((headings: any[]) => {
    const bridge = (window as any).hwpAssistantBridge,
      before = bridge.exportSnapshot(),
      state = bridge.documentState();
    const descriptor = headings.map(
      ({ id, section, paragraph, length, text_sha256 }) => ({
        id,
        section,
        paragraph,
        length,
        text_sha256,
      }),
    );
    const actual = bridge.layoutEvidence(descriptor),
      rejected: boolean[] = [];
    for (const invalid of [
      [{ ...descriptor[0], paragraph: 999999 }],
      [{ ...descriptor[0], length: descriptor[0].length + 1 }],
      [{ ...descriptor[0], text_sha256: "0".repeat(64) }],
      [descriptor[0], descriptor[0]],
    ]) {
      try {
        bridge.layoutEvidence(invalid);
        rejected.push(false);
      } catch {
        rejected.push(true);
      }
    }
    const after = bridge.exportSnapshot();
    (window as any).__tocContext = "stable";
    return {
      actual,
      rejected,
      sameBytes:
        before.bytes.length === after.bytes.length &&
        before.bytes.every((n: number, i: number) => n === after.bytes[i]),
      sameState:
        JSON.stringify(state) === JSON.stringify(bridge.documentState()),
      revision: after.revision === before.revision,
    };
  }, measured.headings);
  expect(bridgeChecks.rejected).toEqual([true, true, true, true]);
  expect(
    bridgeChecks.sameBytes && bridgeChecks.sameState && bridgeChecks.revision,
  ).toBe(true);
  expect(bridgeChecks.actual).toEqual(measured);
  await insert(page);
  expect(await live(page)).toBe(stage);
  expect(
    await (await live(page)).evaluate(() => (window as any).__tocContext),
  ).toBe("stable");
  const output = content(await exported(await live(page)));
  expect(output).toContain("미저장 비대상 문장");
  expect(output.match(/<hp:tbl\b/g)).toHaveLength(2);
  expect(output).toContain("두 번째 셀");
  expect(output.match(/<hp:tab\b/g)).toHaveLength(3);
  await expect(page.getByLabel("목차 상태")).toContainText("검증 완료");
  await closePanel(page);
  await typeEnd(page, " 추가 수정");
  await expect(page.getByLabel("목차 상태")).toContainText("갱신 필요");
  const firstIds = final.headings.map((heading: any) => heading.heading_id);
  await analyze(page, false);
  await ready(page, true);
  expect(
    verifyBodies.at(-1).headings.map((heading: any) => heading.heading_id),
  ).toEqual(firstIds);
  await insert(page);
  await closePanel(page);
  await page.screenshot({ path: testInfo.outputPath("toc-final.png") });
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const saved = (await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href"))!;
  expect(content(await (await request.get(saved)).body())).toContain(
    "추가 수정",
  );
  expect(await (await request.get(source.file)).body()).toEqual(source.bytes);
  await page.getByRole("button", { name: "← 문서 목록", exact: true }).click();
  await page.getByRole("button", { name: source.name, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  await expect(page.getByLabel("목차 상태")).toContainText("확인 필요");
  await analyze(page, false);
  await expect(page.getByLabel("목차 관리", { exact: true })).toContainText(
    "현재 목차",
  );
  await ready(page, true);
  expect(
    verifyBodies.at(-1).headings.map((heading: any) => heading.heading_id),
  ).toEqual(firstIds);
});

test("REAL engine TOC: response loss retries frozen inputs and save output while later edits remain separate", async ({
  page,
  request,
}) => {
  const source = await open(page, `목차 재확인 ${Date.now()}.hwpx`);
  await typeEnd(page, "초기 미저장 내용");
  const previewBodies: any[] = [],
    verifyBodies: any[] = [];
  let losePreview = true,
    loseVerify = true;
  await page.route("**/api/documents/*/toc-preview", async (route) => {
    previewBodies.push(route.request().postDataJSON());
    const response = await route.fetch();
    if (losePreview) {
      losePreview = false;
      await route.abort("failed");
    } else await route.fulfill({ response });
  });
  await page.route("**/api/documents/*/toc-verify", async (route) => {
    verifyBodies.push(route.request().postDataJSON());
    const response = await route.fetch();
    if (loseVerify) {
      loseVerify = false;
      await route.abort("failed");
    } else await route.fulfill({ response });
  });
  await analyze(page);
  await page
    .getByRole("button", { name: "선택한 제목으로 목차 미리보기", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "같은 목차 요청 재확인", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "같은 목차 요청 재확인", exact: true })
    .click();
  await expect.poll(() => verifyBodies.length, { timeout: 90000 }).toBe(1);
  await expect(
    page.getByRole("button", { name: "같은 목차 요청 재확인", exact: true }),
  ).toBeVisible({ timeout: 90000 });
  await page
    .getByRole("button", { name: "같은 목차 요청 재확인", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "검증한 목차 반영", exact: true }),
  ).toBeEnabled({ timeout: 90000 });
  expect(previewBodies[1]).toEqual(previewBodies[0]);
  expect(verifyBodies[1]).toEqual(verifyBodies[0]);
  expect(
    await (await request.get(source.document + "/versions")).json(),
  ).toHaveLength(1);
  await insert(page);
  await closePanel(page);
  const saves: { key: string; body: string }[] = [];
  let loseSave = true;
  await page.route("**/api/documents/*/versions?*", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    saves.push({
      key: route.request().headers()["idempotency-key"],
      body: route.request().postData()!,
    });
    const response = await route.fetch();
    if (loseSave) {
      loseSave = false;
      await route.abort("failed");
    } else await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 실패");
  await typeEnd(page, " 응답 유실 뒤 사용자 편집");
  await page.getByRole("button", { name: "목차 관리", exact: true }).click();
  await page
    .getByRole("button", { name: "현재 문서의 목차·제목 확인", exact: true })
    .click();
  await expect(
    page.getByLabel("목차 관리", { exact: true }).getByRole("alert"),
  ).toContainText("저장·승인·후보 비교");
  await closePanel(page);
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장되지 않은 변경 있음");
  expect(saves[1]).toEqual(saves[0]);
  const frozenFile = (await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href"))!;
  expect(content(await (await request.get(frozenFile)).body())).not.toContain(
    "응답 유실 뒤 사용자 편집",
  );
  expect(content(await exported(await live(page)))).toContain(
    "응답 유실 뒤 사용자 편집",
  );
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  expect(
    await (await request.get(source.document + "/versions")).json(),
  ).toHaveLength(3);
  expect(await (await request.get(source.file)).body()).toEqual(source.bytes);
});

test("REAL engine TOC: stale edits and late results after close preserve live buffers and bounded stages", async ({
  page,
  request,
}) => {
  const source = await open(page, `목차 경계 ${Date.now()}.hwpx`);
  const original = await typeEnd(page, "보존할 미저장 내용");
  await analyze(page);
  await ready(page);
  await typeEnd(page, " 준비 뒤 추가 편집");
  const changed = await exported(original);
  await page
    .getByRole("button", { name: "검증한 목차 반영", exact: true })
    .click();
  await expect(
    page.getByLabel("목차 관리", { exact: true }).getByRole("alert"),
  ).toContainText("문서나 커서가 바뀌었습니다");
  expect(await live(page)).toBe(original);
  expect(await exported(original)).toEqual(changed);
  await expect(page.locator(".block-editor-slot iframe")).toHaveCount(1);
  let release!: () => void, started!: () => void, settled!: () => void;
  const barrier = new Promise<void>((resolve) => {
      release = resolve;
    }),
    received = new Promise<void>((resolve) => {
      started = resolve;
    }),
    finished = new Promise<void>((resolve) => {
      settled = resolve;
    });
  await page.route("**/api/documents/*/toc-preview", async (route) => {
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
    await analyze(page);
    await page
      .getByRole("button", {
        name: "선택한 제목으로 목차 미리보기",
        exact: true,
      })
      .click();
    await received;
    await closePanel(page);
    release();
    await finished;
    await expect(page.locator(".block-editor-slot iframe")).toHaveCount(1);
    expect(await exported(original)).toEqual(changed);
    await expect(
      page.getByRole("button", { name: "저장", exact: true }),
    ).toBeEnabled();
    expect(
      await (await request.get(source.document + "/versions")).json(),
    ).toHaveLength(1);
  } finally {
    release();
    await page.unroute("**/api/documents/*/toc-preview");
  }
});

test("REAL authenticated TOC: current ACL revocation rejects adoption and reader has no management entry", async ({
  page,
  request,
}) => {
  test.skip(
    !process.env.HWP_E2E_AUTH_URL,
    "Separate authenticated API/Vite required",
  );
  const url = process.env.HWP_E2E_AUTH_URL!,
    alice = "alice-" + "a".repeat(32),
    bob = "bob-" + "b".repeat(32),
    headers = { Authorization: "Bearer " + alice };
  const bytes = await fixture(),
    name = `목차 접근 권한 ${Date.now()}.hwpx`;
  const uploaded = await request.post(
    url +
      "/api/documents?" +
      new URLSearchParams({ name, document_id: randomUUID() }),
    { headers: { ...headers, "Idempotency-Key": randomUUID() }, data: bytes },
  );
  expect(uploaded.ok()).toBe(true);
  const document = await uploaded.json();
  const path = `${url}/api/documents/${document.id}`;
  await request.put(path + "/access", {
    headers,
    data: { user_id: "bob", role: "editor" },
  });
  await page.goto(url);
  await page.getByLabel("인증 토큰").fill(bob);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("button", { name, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  const original = await typeEnd(page, "권한 회수 뒤에도 보존"),
    before = await exported(original);
  await analyze(page);
  await ready(page);
  await request.delete(path + "/access/bob", { headers });
  await page
    .getByRole("button", { name: "검증한 목차 반영", exact: true })
    .click();
  await expect(
    page.getByLabel("목차 관리", { exact: true }).getByRole("alert"),
  ).toBeVisible();
  expect(await live(page)).toBe(original);
  expect(await exported(original)).toEqual(before);
  await expect(page.locator(".block-editor-slot iframe")).toHaveCount(1);
  expect(
    await (await request.get(path + "/versions", { headers })).json(),
  ).toHaveLength(1);
  expect(
    await (
      await request.get(path + `/versions/${document.current_version}/file`, {
        headers,
      })
    ).body(),
  ).toEqual(bytes);
  await request.put(path + "/access", {
    headers,
    data: { user_id: "bob", role: "reader" },
  });
  await page.goto(url);
  await page.getByRole("button", { name, exact: true }).click();
  await expect(page.getByText("열람 권한 ·", { exact: false })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "목차 관리", exact: true }),
  ).toHaveCount(0);
});
