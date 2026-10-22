import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

test("REAL HWPX template: register, recommend, apply, edit delta, preserve pinned source", async ({
  page,
  request,
}) => {
  const source = await readFile(
    new URL("../../samples/paragraph-and-table.hwpx", import.meta.url),
  );
  const filename = `회의 양식 ${Date.now()}.hwpx`;
  await page.goto("/");
  await page
    .getByLabel("HWPX 업로드", { exact: true })
    .setInputFiles({
      name: filename,
      mimeType: "application/hwp+zip",
      buffer: source,
    });
  const row = page
    .getByRole("row")
    .filter({ has: page.getByRole("button", { name: filename, exact: true }) });
  const sourceUrl = (await row
    .getByRole("link", { name: "다운로드", exact: true })
    .getAttribute("href"))!;
  await row.getByRole("button", { name: `${filename} 더보기`, exact: true }).click();
  await page.getByRole("group", { name: `${filename} 추가 작업`, exact: true }).getByRole("button", { name: "양식 등록" }).click();
  await expect(
    page.getByRole("heading", { name: "양식 보관함" }),
  ).toBeVisible();
  await page.getByLabel("양식 이름", { exact: true }).fill(filename);
  await page.getByLabel("양식 설명", { exact: true }).fill("회의 결정 기록");
  await page.getByLabel("양식 태그", { exact: true }).fill("회의, 기록");
  await expect(page.getByRole("button", { name: "이 저장본을 양식으로 등록" })).toBeEnabled();
  await page.getByRole("button", { name: "이 저장본을 양식으로 등록" }).click();
  await expect(page.getByRole("status").filter({ hasText: "고정 버전을 양식으로 등록" })).toBeVisible();
  await page.getByLabel("양식 검색").fill("회의");
  const template = page
    .locator(".market-card")
    .filter({
      has: page.getByRole("heading", { name: filename, exact: true }),
    });
  await expect(template).toContainText("회의 결정 기록");
  await template.getByRole("button", { name: "양식 보기" }).click();
  await expect(page.getByRole("img", { name: "양식 미리보기 1쪽" })).toBeVisible({ timeout: 90000 });
  await page.getByRole("button", { name: "이 양식으로 새 문서" }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  const copyUrl = (await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href"))!;
  expect(copyUrl).not.toBe(sourceUrl);
  expect(await (await request.get(copyUrl)).body()).toEqual(source);
  const frame = page.frames().find((f) => f.url().includes("/rhwp/"))!;
  await frame
    .locator("#scroll-container canvas")
    .first()
    .click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  await page.keyboard.insertText(" 양식 적용 후 수정");
  const saveRequest = page.waitForRequest(
    (r) => r.url().includes("/versions?") && r.method() === "POST",
  );
  await page.getByRole("button", { name: "저장", exact: true }).click();
  const sent = await saveRequest;
  expect(sent.headers()["content-type"]).toBe("application/vnd.hwp.delta+json");
  expect(sent.postDataJSON()).toHaveProperty("base_sha256");
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const changedUrl = (await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href"))!;
  const files = unzipSync(await (await request.get(changedUrl)).body());
  expect(strFromU8(files["Contents/section0.xml"])).toContain(
    "양식 적용 후 수정",
  );
  expect(strFromU8(files["Contents/section0.xml"])).toContain(":tbl");
  expect(await (await request.get(sourceUrl)).body()).toEqual(source);
  expect(await (await request.get(copyUrl)).body()).toEqual(source);
  const changes = await (
    await request.get(changedUrl.replace(/\/file$/, "/changes"))
  ).json();
  expect(changes.entries.map((e: { path: string }) => e.path)).toContain(
    "Contents/section0.xml",
  );
});

test("REAL engine + external proposal fixture: persisted context, explicit approval, stale selection blocked", async ({
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
  await page.keyboard.insertText("공유 문단 원문");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  const fileUrl = (await page
    .getByRole("link", { name: "저장본 다운로드" })
    .getAttribute("href"))!;
  const docPath = fileUrl.split("/versions/")[0];
  await page.getByRole("button", { name: "AI 문장 다듬기 열기" }).click();
  page.on("dialog", (d) => d.accept("문서 공동 검토"));
  await page.getByRole("button", { name: "공유 대화 만들기" }).click();
  await page.getByText("추가 작성 정보 · 선택 사항", { exact: true }).click();
  await page.getByLabel("작성 목적").fill("명확한 회의록");
  await page.getByLabel("작성 기준", { exact: true }).fill("결정된 날짜만 사용");
  await page.getByRole("button", { name: "현재 문단을 작성 정보에 저장" }).click();
  await page.getByText("작성 정보에 저장한 문단", { exact: true }).click();
  await expect(page.locator(".collaboration")).toContainText("공유 문단 원문");
  const threads = await (await request.get(docPath + "/threads")).json();
  const tid = threads[0].id;
  const context = await (await request.get(`/api/threads/${tid}`)).json();
  expect(context.context.writing_criteria).toBe("결정된 날짜만 사용");
  await page.getByLabel("대화 메시지").fill("결정 사항을 검토해주세요.");
  await page.getByRole("button", { name: "메모 저장", exact: true }).click();
  await expect(page.getByLabel("저장된 대화")).toContainText(
    "결정 사항을 검토해주세요.",
  );
  const proposal = await request.post(`/api/threads/${tid}/proposals`, {
    data: {
      context_revision: 1,
      replacement: "검토한 공유 문단",
      reason: "외부 제안 fixture · 실제 모델 아님",
    },
  });
  expect(proposal.ok()).toBeTruthy();
  await page.getByRole("button", { name: "대화 새로고침" }).click();
  const approve = page.getByRole("button", { name: "이 제안 승인하고 적용" });
  await expect(approve).toBeVisible();
  expect(
    await frame.evaluate(
      () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
    ),
  ).toBe("공유 문단 원문");
  await approve.click();
  await expect
    .poll(() =>
      frame.evaluate(
        () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
      ),
    )
    .toBe("검토한 공유 문단");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료");
  await page.getByRole("button", { name: "현재 문단을 작성 정보에 저장" }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/threads/${tid}`)).json()).context
          .revision,
    )
    .toBe(2);
  await request.post(`/api/threads/${tid}/proposals`, {
    data: {
      context_revision: 2,
      replacement: "오래된 변경",
      reason: "선택 변경 거부 시험",
    },
  });
  await page.getByRole("button", { name: "대화 새로고침" }).click();
  await focus();
  await page.keyboard.insertText(" 후속 입력");
  await page
    .getByRole("button", { name: "이 제안 승인하고 적용", exact: true })
    .first()
    .click();
  await expect(page.getByRole("alert")).toContainText("다시 검토");
  expect(
    await frame.evaluate(
      () => (window as any).hwpAssistantBridge.snapshot().evidence.text,
    ),
  ).toContain("후속 입력");
  await page.screenshot({
    path: "test-results/collaboration.png",
    fullPage: true,
  });
});
