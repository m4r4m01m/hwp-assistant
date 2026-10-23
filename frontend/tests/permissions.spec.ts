import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
test.use({ baseURL: process.env.HWP_E2E_AUTH_URL || "http://127.0.0.1:5174" });
test.skip(
  !process.env.HWP_E2E_AUTH_URL,
  "Separate authenticated API/Vite required; see verification instructions",
);

test("REAL authenticated browsers: owner sharing, reader visibility, revoke blocks all reads", async ({
  page,
  browser,
  request,
}) => {
  // Public synthetic test credentials, only used by the isolated verification server.
  const alice = "alice-" + "a".repeat(32),
    bob = "bob-" + "b".repeat(32);
  const headers = { Authorization: "Bearer " + alice };
  const name = `권한 검증 ${Date.now()}.hwpx`;
  const response = await request.post(
    "/api/documents?" +
      new URLSearchParams({ name, document_id: randomUUID() }),
    {
      headers: { ...headers, "Idempotency-Key": randomUUID() },
      data: await readFile(
        new URL("../../samples/paragraph-and-table.hwpx", import.meta.url),
      ),
    },
  );
  expect(response.ok()).toBeTruthy();
  const doc = await response.json();
  const shared = await (
    await request.post(`/api/documents/${doc.id}/threads`, {
      headers,
      data: { id: randomUUID(), title: "공유 검토", visibility: "document" },
    })
  ).json();
  const privateThread = await (
    await request.post(`/api/documents/${doc.id}/threads`, {
      headers,
      data: { id: randomUUID(), title: "기존 private 요청도 문서 공유", visibility: "private" },
    })
  ).json();
  await request.post(`/api/threads/${shared.id}/messages`, {
    headers,
    data: { request_id: randomUUID(), content: "문서 구성원 공유 내용" },
  });
  await page.goto("/");
  await page.getByLabel("인증 토큰").fill(alice);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  const row = page
    .getByRole("row")
    .filter({ has: page.getByRole("button", { name, exact: true }) });
  await row.getByRole("button", { name: `${name} 더보기`, exact: true }).click();
  await page.getByRole("group", { name: `${name} 추가 작업`, exact: true }).getByRole("button", { name: "공유 권한" }).click();
  await page.getByLabel("공유 대상 사용자").fill("bob");
  await page.getByRole("button", { name: "권한 저장" }).click();
  await expect(page.locator(".modal")).toContainText("bob · reader");
  const context = await browser.newContext({
    baseURL: process.env.HWP_E2E_AUTH_URL,
  });
  try {
    const reader = await context.newPage();
    await reader.goto("/");
    await reader.getByLabel("인증 토큰").fill(bob);
    await reader.getByRole("button", { name: "로그인", exact: true }).click();
    await reader.getByRole("button", { name, exact: true }).click();
    await expect(
      reader.getByText("열람 권한 ·", { exact: false }),
    ).toBeVisible();
    await expect(
      reader.getByRole("button", { name: "저장", exact: true }),
    ).toHaveCount(0);
    await reader.getByLabel("대화 선택").selectOption(shared.id);
    await expect(reader.getByLabel("저장된 대화")).toContainText(
      "문서 구성원 공유 내용",
    );
    expect(
      (await context.request.get(`/api/threads/${privateThread.id}`)).status(),
    ).toBe(200);
    expect(
      (await context.request.post(`/api/documents/${doc.id}/session`)).status(),
    ).toBe(403);
    await page.getByRole("button", { name: "권한 회수" }).click();
    await expect(page.locator(".modal")).not.toContainText("bob · reader");
    expect(
      (await context.request.get(`/api/threads/${shared.id}`)).status(),
    ).toBe(404);
    expect(
      (
        await context.request.get(
          `/api/documents/${doc.id}/versions/${doc.current_version}/file`,
        )
      ).status(),
    ).toBe(404);
    await reader.reload();
    await expect(reader.getByRole("button", { name, exact: true })).toHaveCount(
      0,
    );
  } finally {
    await context.close();
  }
});
