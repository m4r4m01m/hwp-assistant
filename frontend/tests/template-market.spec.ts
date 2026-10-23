import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";

const sample = new URL(
  "../../samples/paragraph-and-table.hwpx",
  import.meta.url,
);
const docPath = (url: string) => url.split("/versions/")[0];

async function uploadSource(page: Page, filename: string) {
  await page.goto("/");
  await page
    .getByLabel("HWPX 업로드", { exact: true })
    .setInputFiles({
      name: filename,
      mimeType: "application/hwp+zip",
      buffer: await readFile(sample),
    });
  const row = page
    .getByRole("row")
    .filter({ has: page.getByRole("button", { name: filename, exact: true }) });
  await expect(row).toBeVisible();
  const url = (await row
    .getByRole("link", { name: "다운로드", exact: true })
    .getAttribute("href"))!;
  return {
    row,
    url,
    id: url.split("/documents/")[1].split("/")[0],
    version: url.split("/versions/")[1].split("/")[0],
  };
}

test("REAL engine template market: frozen registration, pinned preview, metadata, favorites and lost apply response", async ({
  page,
  request,
}) => {
  const name = `마켓 검증 ${Date.now()}`;
  const source = await uploadSource(page, `${name}.hwpx`);
  const beforeDocs = await (await request.get("/api/documents")).json();
  const beforeVersions = await (
    await request.get(docPath(source.url) + "/versions")
  ).json();
  await source.row.getByRole("button", { name: `${name}.hwpx 더보기`, exact: true }).click();
  await page.getByRole("group", { name: `${name}.hwpx 추가 작업`, exact: true })
    .getByRole("button", { name: "양식 등록", exact: true })
    .click();
  await page.getByLabel("양식 이름", { exact: true }).fill(name);
  await page
    .getByLabel("양식 설명", { exact: true })
    .fill("회의 결정과 후속 조치를 정리하는 양식");
  await page.getByLabel("양식 분류", { exact: true }).selectOption("회의록");
  await page.getByLabel("양식 태그", { exact: true }).fill("회의, 후속 조치");
  const registrations: any[] = [];
  let dropRegistration = true,
    registeredId = "";
  await page.route("**/api/templates", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    registrations.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    registeredId = (await response.json()).id;
    if (dropRegistration) {
      dropRegistration = false;
      await route.abort("connectionreset");
    } else await route.fulfill({ response });
  });
  await expect(
    page.getByRole("button", { name: "이 저장본을 양식으로 등록" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "이 저장본을 양식으로 등록" }).click();
  await expect(page.getByRole("alert")).toContainText("같은 입력으로 재확인");
  await expect(
    page.getByRole("button", { name: "문서함", exact: true }),
  ).toBeDisabled();
  await expect(page.getByLabel("양식 이름", { exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "같은 요청 재확인" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "양식으로 등록" }),
  ).toBeVisible();
  expect(registrations).toHaveLength(2);
  expect(registrations[1]).toEqual(registrations[0]);
  expect(registrations[0].version_id).toBe(source.version);
  await expect(
    page.getByRole("img", { name: "양식 미리보기 1쪽" }),
  ).toBeVisible({ timeout: 90000 });
  expect(await (await request.get("/api/documents")).json()).toHaveLength(
    beforeDocs.length,
  );
  expect(
    await (await request.get(docPath(source.url) + "/versions")).json(),
  ).toEqual(beforeVersions);
  expect(
    await (
      await request.get(`/api/template-market/${registeredId}/file`)
    ).body(),
  ).toEqual(await readFile(sample));
  const revisedFiles = unzipSync(await readFile(sample));
  const section = strFromU8(revisedFiles["Contents/section0.xml"]);
  expect(section).toContain("서식 시험");
  revisedFiles["Contents/section0.xml"] = strToU8(
    section.replace("서식 시험", "원본 후속 변경"),
  );
  const session = await request.post(docPath(source.url) + "/session");
  expect(session.ok()).toBeTruthy();
  const lease = await session.json();
  try {
    const update = await request.post(
      `${docPath(source.url)}/versions?base_version=${source.version}&cause=reupload`,
      {
        headers: {
          "X-Edit-Session": lease.token,
          "Idempotency-Key": randomUUID(),
        },
        data: Buffer.from(zipSync(revisedFiles)),
      },
    );
    expect(update.ok()).toBeTruthy();
  } finally {
    await request.delete(docPath(source.url) + "/session", {
      headers: { "X-Edit-Session": lease.token },
    });
  }
  const pinned = await (
    await request.get(`/api/template-market/${registeredId}`)
  ).json();
  expect(pinned.version_id).toBe(source.version);
  expect(
    await (
      await request.get(`/api/template-market/${registeredId}/file`)
    ).body(),
  ).toEqual(await readFile(sample));
  expect(await (await request.get(source.url)).body()).toEqual(
    await readFile(sample),
  );
  await page.getByLabel("양식 검색").fill(name);
  const card = page
    .locator(".market-card")
    .filter({ has: page.getByRole("heading", { name, exact: true }) });
  await expect(card).toBeVisible();
  await card
    .getByRole("button", { name: `${name} 즐겨찾기`, exact: true })
    .click();
  await expect(
    card.getByRole("button", { name: `${name} 즐겨찾기`, exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("즐겨찾기만", { exact: true }).check();
  await page.getByLabel("양식 분류 필터").selectOption("회의록");
  await expect(card).toBeVisible();
  await page
    .getByRole("button", { name: "양식 정보 편집", exact: true })
    .click();
  await page
    .getByLabel("양식 설명", { exact: true })
    .fill("최신 설명 · 담당과 기한을 확인");
  let dropEdit = true;
  const edits: any[] = [];
  await page.route(`**/api/template-market/${registeredId}`, async (route) => {
    if (route.request().method() !== "PUT") {
      await route.continue();
      return;
    }
    edits.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    if (dropEdit) {
      dropEdit = false;
      await route.abort("connectionreset");
    } else await route.fulfill({ response });
  });
  await page
    .getByRole("button", { name: "양식 정보 저장", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("같은 입력으로 재확인");
  await page.getByRole("button", { name: "같은 요청 재확인" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "저장 요청과 일치" }),
  ).toBeVisible();
  expect(edits).toHaveLength(1);
  await expect(page.locator(".market-detail-heading")).toContainText(
    "최신 설명",
  );
  await expect(card).toContainText("최신 설명");
  await page.screenshot({
    path: "test-results/template-market.png",
    fullPage: true,
  });
  const applies: any[] = [];
  let dropApply = true;
  await page.route(`**/api/templates/${registeredId}/apply`, async (route) => {
    applies.push(route.request().postDataJSON());
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    if (dropApply) {
      dropApply = false;
      await route.abort("connectionreset");
    } else await route.fulfill({ response });
  });
  await page.getByLabel("양식 새 문서 이름").fill(`${name} 새 문서.hwpx`);
  await page
    .getByRole("button", { name: "이 양식으로 새 문서", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("같은 입력으로 재확인");
  await expect(
    page.getByRole("button", { name: "문서함", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "같은 요청 재확인" }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", {
    timeout: 90000,
  });
  expect(applies).toHaveLength(2);
  expect(applies[1]).toEqual(applies[0]);
  const afterDocs = await (await request.get("/api/documents")).json();
  expect(afterDocs).toHaveLength(beforeDocs.length + 1);
  const copied = afterDocs.find((d: any) => d.id === applies[0].document_id);
  expect(copied.name).toBe(`${name} 새 문서.hwpx`);
  expect(
    await (
      await request.get(
        `/api/documents/${copied.id}/versions/${copied.current_version}/file`,
      )
    ).body(),
  ).toEqual(await readFile(sample));
  expect(await (await request.get(source.url)).body()).toEqual(
    await readFile(sample),
  );
});

test("template market: paging and current source permissions reject stale apply and clear preview", async ({
  page,
  request,
}) => {
  // Catalog search matches any query term. A single unique term keeps repeated
  // runs isolated without deleting templates produced by earlier tests.
  const prefix = `목록검증-${Date.now()}`;
  const source = await uploadSource(page, `${prefix}.hwpx`);
  const templateIds: string[] = [];
  for (let index = 0; index < 25; index++) {
    const response = await request.post("/api/templates", {
      data: {
        id: randomUUID(),
        document_id: source.id,
        version_id: source.version,
        name: `${prefix} ${String(index).padStart(2, "0")}`,
        description: "목록 페이지 시험",
        category: "점검표",
        tags: [prefix],
      },
    });
    expect(response.ok()).toBeTruthy();
    templateIds.push((await response.json()).id);
  }
  await page.getByRole("button", { name: "양식 보관함", exact: true }).click();
  await page.getByLabel("양식 검색").fill(prefix);
  await page.getByLabel("양식 정렬").selectOption("name");
  await expect(page.locator(".market-card")).toHaveCount(24);
  await page.getByRole("button", { name: "다음", exact: true }).click();
  await expect(page.locator(".market-card")).toHaveCount(1);
  await expect(page.locator(".market-card").first()).toContainText(
    `${prefix} 24`,
  );
  await page.getByRole("button", { name: "이전", exact: true }).click();
  await expect(page.locator(".market-card")).toHaveCount(24);
  await expect(page.locator(".market-card").first()).toContainText(
    `${prefix} 00`,
  );
  await page
    .locator(".market-card")
    .first()
    .getByRole("button", { name: "양식 보기" })
    .click();
  await expect(
    page.getByRole("img", { name: "양식 미리보기 1쪽" }),
  ).toBeVisible({ timeout: 90000 });
  const beforeDocs = await (await request.get("/api/documents")).json();
  // The browser boundary is tested with a synthetic forbidden response. Actual
  // source access revocation is independently exercised by authenticated API tests.
  await page.route(`**/api/templates/${templateIds[0]}/apply`, (route) =>
    route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ detail: "현재 문서 접근 권한이 없습니다." }),
    }),
  );
  await page
    .getByRole("button", { name: "이 양식으로 새 문서", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("현재 문서 접근 권한");
  await expect(
    page.getByRole("img", { name: "양식 미리보기 1쪽" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "문서함", exact: true }),
  ).toBeEnabled();
  expect(await (await request.get("/api/documents")).json()).toHaveLength(
    beforeDocs.length,
  );
});

test("REAL engine template market: standard editor and report entry create no document or generation", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const beforeDocs = await (await request.get("/api/documents")).json();
  const standard = await (await request.get("/api/document-standard")).json();
  const reference = await (await request.get(standard.reference_url)).body();
  expect(createHash("sha256").update(reference).digest("hex")).toBe(
    standard.reference_sha256,
  );
  const posts: string[] = [];
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      /\/api\/(reports|documents|templates)/.test(request.url())
    )
      posts.push(request.url());
  });
  await page.getByRole("button", { name: "양식 보관함", exact: true }).click();
  await page.getByText("AI 보고서의 양식을 바꾸려면", { exact: true }).click();
  await page.getByRole("button", { name: "표준 설정 편집", exact: true }).click();
  await expect(page.getByRole("img", { name: "문서 표준 예시 1쪽" })).toBeVisible({ timeout: 90000 });
  await page.getByRole("button", { name: "양식 보관함", exact: true }).click();
  await page.getByText("AI 보고서의 양식을 바꾸려면", { exact: true }).click();
  await page.getByRole("button", { name: "보고서 작성으로", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "무엇을 정리할까요?", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("보고서 작성 요청", { exact: true }),
  ).toBeVisible();
  expect(posts).toEqual([]);
  expect(await (await request.get("/api/documents")).json()).toHaveLength(
    beforeDocs.length,
  );
  expect(await (await request.get(standard.reference_url)).body()).toEqual(
    reference,
  );
});
