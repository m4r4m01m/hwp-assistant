import { test, expect } from "@playwright/test";

for (const profileId of ["standard-business", "executive-decision", "response-plan"]) {
test(`REAL rhwp + MOCK report template ${profileId}: lost reception retries the selected actual values and creates one task`, async ({ page, request }) => {
  test.skip(process.env.HWP_E2E_REPORT_MOCK !== "1", "Synthetic generation only; no real model call.");
  const beforeDocs = (await (await request.get("/api/documents")).json()).length;
  const beforeJobs = (await (await request.get("/api/report-tasks")).json()).length;
  const catalog = await (await request.get("/api/report-templates")).json();
  const selected = catalog.items.find((item: any) => item.id === profileId);
  expect(selected).toBeTruthy();
  const posts: any[] = [];
  await page.route("**/api/report-tasks", async route => {
    if (route.request().method() !== "POST") return route.continue();
    posts.push(route.request().postDataJSON());
    if (posts.length === 1) {
      const response = await route.fetch();
      expect(response.ok(), await response.text()).toBeTruthy();
      return route.abort("failed");
    }
    return route.continue();
  });
  await page.goto("/");
  await page.getByRole("button", { name: "보고서 작성", exact: true }).click();
  await expect(page.getByLabel("문서 서식 표준", { exact: true })).toBeEnabled();
  await page.getByLabel("문서 서식 표준", { exact: true }).selectOption(""); // Keep this existing profile/TOC contract on the legacy visual standard.
  await page.getByLabel("보고서 템플릿", { exact: true }).selectOption(profileId);
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill("금융권 해킹 위험과 경영진 결정 사항을 정리하는 합성 보고서");
  await page.getByLabel("공개 웹 자료를 검색하고 출처 포함").uncheck();
  await page.getByRole("button", { name: "Codex로 보고서 초안 만들기", exact: true }).click();
  const retry = page.getByRole("button", { name: "같은 요청 접수 재확인", exact: true });
  await expect(retry).toBeEnabled();
  await expect(page.getByLabel("보고서 템플릿", { exact: true })).toBeDisabled();
  await expect(page.getByLabel("보고서 작성 요청", { exact: true })).toBeDisabled();
  await retry.click();
  await page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true }).click();
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toBeVisible({ timeout: 90000 });
  expect(posts).toHaveLength(2);
  expect(posts[1]).toEqual(posts[0]);
  expect(posts[0].template).toEqual(selected);
  const job = await (await request.get(`/api/report-tasks/${posts[0].id}`)).json();
  expect(job.input).toEqual(posts[0]);
  expect((await (await request.get("/api/report-tasks")).json()).length).toBe(beforeJobs + 1);
  expect((await (await request.get("/api/documents")).json()).length).toBe(beforeDocs);
  await page.getByRole("button", { name: "결과·출처", exact: true }).click();
  await expect(page.getByText(`작성 기준: ${selected.values.name}`, { exact: true })).toBeVisible();
  const preview = await (await request.get(`/api/report-tasks/${posts[0].id}/preview`)).json();
  expect(preview.toc_policy).toBe("report-native-toc-v1");
});
}
