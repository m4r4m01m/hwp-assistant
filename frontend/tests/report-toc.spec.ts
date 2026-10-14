import {
  test,
  expect,
  type Page,
  type Frame,
  type APIRequestContext,
  type TestInfo,
} from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";
import { writeFile, readFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

const sha = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");
const xml = (bytes: Buffer) =>
  strFromU8(unzipSync(bytes)["Contents/section0.xml"]);
test.beforeEach(() =>
  test.skip(
    process.env.HWP_E2E_REPORT_MOCK !== "1" ||
      !!process.env.HWP_REPORT_FIXTURES,
    "Synthetic reports with the actual rhwp engine; no model call.",
  ),
);
async function frame(page: Page): Promise<Frame> {
  const handle = await page
    .locator(".report-editor-slot.active iframe")
    .elementHandle();
  const result = await handle?.contentFrame();
  if (!result) throw new Error("활성 보고서 엔진이 없습니다.");
  return result;
}
async function output(engine: Frame) {
  return Buffer.from(
    await engine.evaluate(
      () =>
        Array.from(
          (window as any).hwpAssistantBridge.exportSnapshot().bytes,
        ) as number[],
    ),
  );
}
async function typeEnd(page: Page, text: string) {
  const engine = await frame(page);
  await engine
    .getByRole("textbox", { name: "문서 편집 입력", exact: true })
    .focus();
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(text);
  await expect.poll(async () => xml(await output(engine))).toContain(text);
  return engine;
}
async function begin(page: Page, prompt: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill(prompt);
  await page.getByLabel("공개 웹 자료를 검색하고 출처 포함").uncheck();
  const posted = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/report-tasks") &&
      response.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Codex로 보고서 초안 만들기", exact: true })
    .click();
  return (await (await posted).json()).id as string;
}
async function ready(page: Page) {
  await expect
    .poll(
      async () =>
        (await page
          .getByRole("button", { name: "초안 확정·서버 저장", exact: true })
          .isEnabled()
          .catch(() => false)) ||
        (await page
          .getByRole("alert")
          .isVisible()
          .catch(() => false)),
      { timeout: 90000 },
    )
    .toBeTruthy();
  expect(await page.getByRole("alert").allTextContents()).toEqual([]);
  await expect(
    page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByLabel("초안 목차 상태", { exact: true }),
  ).toContainText("검증 완료");
}
async function preview(request: APIRequestContext, id: string) {
  const response = await request.get(`/api/report-tasks/${id}/preview`);
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json();
}
async function proof(info: TestInfo, name: string, value: unknown) {
  await writeFile(info.outputPath(name), JSON.stringify(value, null, 2));
}
async function checkNative(page: Page, value: any) {
  const engine = await frame(page),
    bytes = await output(engine);
  expect(sha(bytes)).toBe(value.layout_sha256);
  const evidence = await engine.evaluate(
    (headings) =>
      (window as any).hwpAssistantBridge.layoutEvidence(
        headings.map(
          ({ id, section, paragraph, length, text_sha256 }: any) => ({
            id,
            section,
            paragraph,
            length,
            text_sha256,
          }),
        ),
      ),
    value.toc.headings,
  );
  expect(evidence.export_sha256).toBe(value.layout_sha256);
  expect(
    Object.fromEntries(
      evidence.headings.map((heading: any) => [heading.id, heading.page]),
    ),
  ).toEqual(value.toc.pages);
  expect(evidence.page_count).toBe(value.toc.page_count);
  expect(value.toc.headings.length).toBeGreaterThan(2);
  return { engine, bytes, evidence };
}

test("REAL rhwp + MOCK report TOC: automatic native pages, rich content, stale refresh and reopen", async ({
  page,
  request,
}, info) => {
  const before = (await (await request.get("/api/documents")).json()).length;
  const verifications: any[] = [];
  await page.route(
    "**/api/report-tasks/*/preview/toc-verify",
    async (route) => {
      const response = await route.fetch();
      verifications.push({
        body: route.request().postDataJSON(),
        status: response.status(),
        result: await response.json(),
      });
      await proof(info, "verify-phases.json", verifications);
      return route.fulfill({ response });
    },
  );
  try {
    const id = await begin(
      page,
      "[RICH] 자동 목차와 표·흐름도 보존을 확인하는 합성 보고서",
    );
    await ready(page);
    const frozen = await preview(request, id);
    expect(frozen.toc_policy).toBe("report-native-toc-v1");
    expect(frozen.toc_verified).toBe(true);
    const first = await checkNative(page, frozen);
    const originalBody = xml(first.bytes);
    expect((originalBody.match(/<hp:tbl\b/g) || []).length).toBe(3);
    expect(originalBody).toContain("접근 권한 점검");
    expect(originalBody).toContain("복구 및 검증");
    expect(originalBody).toContain("&lt; 목 차 &gt;");
    expect(originalBody).toContain("<hp:tab");
    expect(verifications.length).toBeGreaterThanOrEqual(1);
    expect(verifications.length).toBeLessThanOrEqual(3);
    expect((await (await request.get("/api/documents")).json()).length).toBe(
      before,
    );
    await page.screenshot({
      path: info.outputPath("automatic-native-toc.png"),
      fullPage: true,
    });
    page.on("dialog", (dialog) => dialog.accept());
    await page.reload();
    await page
      .getByRole("button", { name: "보고서 작성", exact: true })
      .click();
    await ready(page);
    expect((await preview(request, id)).layout_sha256).toBe(
      frozen.layout_sha256,
    );
    expect(verifications.length).toBeLessThanOrEqual(3); // Reload proves existing bytes/pages without rebuilding.
    await checkNative(page, frozen);
    await typeEnd(page, " 목차 갱신 전 미저장 본문 확인");
    await expect(
      page.getByLabel("초안 목차 상태", { exact: true }),
    ).toContainText("갱신 필요");
    await page
      .getByRole("button", { name: "목차 쪽수 갱신", exact: true })
      .click();
    await ready(page);
    const refreshed = await preview(request, id),
      current = await checkNative(page, refreshed);
    expect(refreshed.toc.id).toBe(frozen.toc.id);
    expect(refreshed.toc.headings.map((heading: any) => heading.id)).toEqual(
      frozen.toc.headings.map((heading: any) => heading.id),
    );
    expect(xml(current.bytes)).toContain("목차 갱신 전 미저장 본문 확인");
    expect((xml(current.bytes).match(/<hp:tbl\b/g) || []).length).toBe(3);
    expect(xml(current.bytes)).toContain("접근 권한 점검");
    expect(xml(current.bytes)).toContain("복구 및 검증");
    expect((await (await request.get("/api/documents")).json()).length).toBe(
      before,
    );
    await page
      .getByRole("button", { name: "초안 확정·서버 저장", exact: true })
      .click();
    await expect(page.getByRole("status")).toHaveText("HWPX 서버 저장 완료");
    const href = (await page
      .getByRole("link", { name: "완성 보고서 다운로드", exact: true })
      .getAttribute("href"))!;
    const saved = await (await request.get(href)).body();
    expect(sha(saved)).toBe(sha(current.bytes));
    expect(
      (
        await (
          await request.get(href.split("/versions/")[0] + "/versions")
        ).json()
      ).length,
    ).toBe(1);
    await writeFile(info.outputPath("native-toc-rich-report.hwpx"), saved);
    await page
      .getByRole("button", { name: "보고서 편집하기", exact: true })
      .click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    await proof(info, "native-toc-proof.json", {
      real_engine: true,
      actual_model_calls: 0,
      human_approval: false,
      automated_approval: true,
      initial_evidence: first.evidence,
      refreshed_evidence: current.evidence,
      saved_sha256: sha(saved),
      versions: 1,
    });
  } finally {
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("REAL rhwp + MOCK report TOC: lost prepare and verify responses retain frozen phase inputs", async ({
  page,
  request,
}, info) => {
  const prepares: any[] = [],
    verifies: any[] = [];
  for (const [endpoint, inputs] of [
    ["toc-prepare", prepares],
    ["toc-verify", verifies],
  ] as const) {
    await page.route(
      `**/api/report-tasks/*/preview/${endpoint}`,
      async (route) => {
        inputs.push(route.request().postDataJSON());
        const response = await route.fetch();
        await proof(info, `${endpoint}-responses.json`, {
          inputs,
          latest: { status: response.status(), result: await response.json() },
        });
        expect(response.ok()).toBeTruthy();
        if (inputs.length === 1) return route.abort("failed");
        return route.fulfill({ response });
      },
    );
  }
  try {
    const before = (await (await request.get("/api/documents")).json()).length;
    const id = await begin(page, "금융 보안 합성 목차 응답 유실 시험");
    const retry = page.getByRole("button", {
      name: "한글 초안 준비 재시도",
      exact: true,
    });
    await expect(retry).toBeEnabled({ timeout: 90000 });
    expect(prepares).toHaveLength(1);
    const sourceFrame = await frame(page),
      source = await output(sourceFrame);
    await retry.click();
    await expect.poll(() => verifies.length, { timeout: 90000 }).toBe(1);
    await expect(retry).toBeEnabled({ timeout: 90000 });
    expect(await output(sourceFrame)).toEqual(source);
    await expect(page.locator(".formatted-report-editor iframe")).toHaveCount(
      2,
    );
    await retry.click();
    await ready(page);
    expect(prepares).toHaveLength(2);
    expect(prepares[1]).toEqual(prepares[0]);
    expect(verifies.length).toBeGreaterThanOrEqual(2);
    expect(verifies[1]).toEqual(verifies[0]);
    expect(verifies.length).toBeLessThanOrEqual(4);
    await expect(page.locator(".formatted-report-editor iframe")).toHaveCount(
      1,
    );
    await checkNative(page, await preview(request, id));
    expect((await (await request.get("/api/documents")).json()).length).toBe(
      before,
    );
  } finally {
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("REAL rhwp + MOCK report TOC: late committed verification preserves edits and reconciles the next base", async ({
  page,
  request,
}, info) => {
  let release!: () => void,
    hold = false,
    committed: any = null;
  const barrier = new Promise<void>((resolve) => {
      release = resolve;
    }),
    prepares: any[] = [];
  await page.route(
    "**/api/report-tasks/*/preview/toc-prepare",
    async (route) => {
      prepares.push(route.request().postDataJSON());
      return route.continue();
    },
  );
  await page.route(
    "**/api/report-tasks/*/preview/toc-verify",
    async (route) => {
      const response = await route.fetch(),
        result = await response.json();
      if (hold && result.converged) {
        committed = result.preview;
        await barrier;
      }
      return route.fulfill({ response });
    },
  );
  try {
    const id = await begin(page, "금융 보안 합성 목차의 늦은 완료와 후속 편집");
    await ready(page);
    await typeEnd(page, " 갱신 전 입력");
    hold = true;
    await page
      .getByRole("button", { name: "목차 쪽수 갱신", exact: true })
      .click();
    await expect.poll(() => !!committed, { timeout: 90000 }).toBeTruthy();
    const oldEngine = await typeEnd(page, " 검증 응답 대기 중 후속 편집");
    const edited = await output(oldEngine);
    release();
    await expect(page.getByRole("alert")).toContainText(
      "후속 편집은 그대로 보존",
      { timeout: 90000 },
    );
    expect(await output(await frame(page))).toEqual(edited);
    await expect(
      page.getByLabel("초안 목차 상태", { exact: true }),
    ).toContainText("갱신 필요");
    await expect(page.locator(".formatted-report-editor iframe")).toHaveCount(
      1,
    );
    hold = false;
    await page
      .getByRole("button", { name: "목차 쪽수 갱신", exact: true })
      .click();
    await ready(page);
    expect(prepares.at(-1).layout_sha256).toBe(committed.layout_sha256);
    expect(prepares.at(-1).source_sha256).toBe(sha(edited));
    const current = await checkNative(page, await preview(request, id));
    expect(xml(current.bytes)).toContain("검증 응답 대기 중 후속 편집");
    await proof(info, "late-commit-proof.json", {
      real_engine: true,
      actual_model_calls: 0,
      human_approval: false,
      committed_layout: committed.layout_sha256,
      preserved_live_sha256: sha(edited),
      next_prepare: prepares.at(-1),
      final_evidence: current.evidence,
    });
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("REAL rhwp + MOCK report TOC: saved response loss preserves frozen bytes and later live edits", async ({
  page,
  request,
}, info) => {
  const saves: any[] = [];
  await page.route("**/api/report-tasks/*/preview/save", async (route) => {
    saves.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.ok(), await response.text()).toBeTruthy();
    if (saves.length === 1) return route.abort("failed");
    return route.fulfill({ response });
  });
  try {
    await begin(page, "금융 보안 합성 목차 저장 재확인과 후속 편집 시험");
    await ready(page);
    await typeEnd(page, " 저장 승인 전 편집");
    const approved = await output(await frame(page));
    await page
      .getByRole("button", { name: "초안 확정·서버 저장", exact: true })
      .click();
    const retry = page.getByRole("button", {
      name: "같은 보고서 저장 재확인",
      exact: true,
    });
    await expect(retry).toBeEnabled({ timeout: 90000 });
    await expect(
      page.getByRole("button", { name: "목차 쪽수 갱신", exact: true }),
    ).toBeDisabled();
    await typeEnd(page, " 응답 유실 뒤 후속 미저장 편집");
    const later = await output(await frame(page));
    await retry.click();
    await expect(page.getByRole("status")).toContainText("후속 편집은 미저장");
    expect(saves).toHaveLength(2);
    expect(saves[1]).toEqual(saves[0]);
    expect(Buffer.from(saves[0].output_base64, "base64")).toEqual(approved);
    const href = (await page
      .getByRole("link", { name: "완성 보고서 다운로드", exact: true })
      .getAttribute("href"))!;
    const saved = await (await request.get(href)).body();
    expect(saved).toEqual(approved);
    expect(xml(saved)).not.toContain("응답 유실 뒤 후속 미저장 편집");
    expect(await output(await frame(page))).toEqual(later);
    const downloaded = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "후속 편집 복구용 다운로드", exact: true })
      .click();
    expect(await readFile((await (await downloaded).path())!)).toEqual(later);
    await expect(
      page.getByLabel("보고서 템플릿", { exact: true }),
    ).toBeDisabled();
    page.once("dialog", (dialog) => dialog.dismiss());
    await page
      .getByRole("button", { name: "보고서 편집하기", exact: true })
      .click();
    await expect(
      page.getByLabel("한글 초안 편집기", { exact: true }),
    ).toBeVisible();
    expect(await output(await frame(page))).toEqual(later);
    await proof(info, "frozen-save-proof.json", {
      real_engine: true,
      actual_model_calls: 0,
      human_approval: false,
      automated_approval: true,
      frozen_saved_sha256: sha(saved),
      later_unsaved_sha256: sha(later),
      same_save_input: saves[0].id === saves[1].id,
      saved_versions: (
        await (
          await request.get(href.split("/versions/")[0] + "/versions")
        ).json()
      ).length,
    });
  } finally {
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("REAL rhwp + MOCK report TOC: cancellation response loss and old completion cannot change another preview", async ({
  page,
  request,
}, info) => {
  let release!: () => void,
    lateDone!: () => void,
    hold = false,
    received = false;
  const barrier = new Promise<void>((resolve) => {
      release = resolve;
    }),
    lateFinished = new Promise<void>((resolve) => {
      lateDone = resolve;
    }),
    cancels: any[] = [];
  await page.route(
    "**/api/report-tasks/*/preview/toc-prepare",
    async (route) => {
      const response = await route.fetch();
      if (hold) {
        received = true;
        await barrier;
        try {
          await route.fulfill({ response }).catch(() => {});
        } finally {
          lateDone();
        }
        return;
      }
      await route.fulfill({ response }).catch(() => {});
    },
  );
  await page.route(
    "**/api/report-tasks/*/preview/toc-cancel",
    async (route) => {
      cancels.push(route.request().postDataJSON());
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBeTruthy();
      if (cancels.length === 1) return route.abort("failed");
      return route.fulfill({ response });
    },
  );
  try {
    await begin(page, "첫 번째 합성 보고서의 목차 중단 시험");
    await ready(page);
    await typeEnd(page, " 중단 중에도 유지할 미저장 본문");
    const old = await output(await frame(page));
    hold = true;
    await page
      .getByRole("button", { name: "목차 쪽수 갱신", exact: true })
      .click();
    await expect.poll(() => received, { timeout: 90000 }).toBeTruthy();
    await page
      .getByRole("button", { name: "목차 갱신 취소", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "같은 목차 취소 재확인", exact: true }),
    ).toBeEnabled({ timeout: 90000 });
    await expect(
      page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }),
    ).toBeDisabled();
    expect(await output(await frame(page))).toEqual(old);
    await page
      .getByRole("button", { name: "같은 목차 취소 재확인", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }),
    ).toBeEnabled();
    expect(cancels).toHaveLength(2);
    expect(cancels[1]).toEqual(cancels[0]);
    page.once("dialog", (dialog) => dialog.accept());
    await page
      .getByRole("button", { name: "초안 편집 종료", exact: true })
      .click();
    hold = false;
    await page
      .getByLabel("보고서 작성 요청", { exact: true })
      .fill("[RICH] 두 번째 보고서의 독립 목차 상태 확인");
    await page
      .getByRole("button", { name: "Codex로 보고서 초안 만들기", exact: true })
      .click();
    await ready(page);
    const second = await output(await frame(page));
    release();
    await lateFinished;
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(await output(await frame(page))).toEqual(second);
    await expect(page.locator(".formatted-report-editor iframe")).toHaveCount(
      1,
    );
    await proof(info, "cancel-owner-proof.json", {
      real_engine: true,
      actual_model_calls: 0,
      human_approval: false,
      same_cancel_input: cancels[0].request_id === cancels[1].request_id,
      old_live_sha256: sha(old),
      new_live_sha256: sha(second),
    });
  } finally {
    release();
    await page.unrouteAll({ behavior: "wait" });
  }
});

test("REAL rhwp + MOCK report TOC: a frozen legacy preview keeps its original layout policy", async ({
  page,
  request,
}) => {
  const id = randomUUID();
  const accepted = await request.post("/api/report-tasks", {
    data: {
      id,
      prompt: "이전 저장 정책의 합성 보고서 보존 시험",
      research: false,
    },
  });
  expect(accepted.ok(), await accepted.text()).toBeTruthy();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/report-tasks/${id}`)).json()).status,
    )
    .toBe("ready");
  await page.goto("/");
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await page.locator(".report-history button").first().click();
  await expect(
    page.getByRole("button", { name: "초안 확정·서버 저장", exact: true }),
  ).toBeEnabled({ timeout: 90000 });
  await expect(page.getByRole("alert")).toHaveCount(0);
  const frozen = await preview(request, id);
  expect(frozen.toc_policy).toBeUndefined();
  expect(frozen.toc_verified).toBeUndefined();
  await expect(page.getByLabel("초안 목차 상태", { exact: true })).toHaveCount(
    0,
  );
  expect(xml(await output(await frame(page)))).not.toContain("&lt; 목 차 &gt;");
});
