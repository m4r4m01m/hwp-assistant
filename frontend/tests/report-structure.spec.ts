import { test, expect, type Page } from "@playwright/test";

// Mock HTTP and editor; no model call or HWPX save.
async function setup(page: Page, loseFirst = false) {
  const posts: any[] = [];
  const template = { id: "standard-business", revision: 1, values: { name: "표준 업무보고", description: "일반 업무 보고", writing_criteria: [] } };
  await page.route("**/report-structure-test.html", route => route.fulfill({ contentType: "text/html", body: '<html lang="ko"><meta charset="utf-8"><div id="root"></div><script type="module" src="/tests/fixtures/report-failure.tsx"></script></html>' }));
  await page.route("**/src/editor.ts*", route => route.fulfill({ contentType: "text/javascript", body: 'export class EditorAdapter { static mount() { throw Error("Unexpected editor mount"); } }' }));
  await page.route("**/api/**", route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    const respond = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    if (request.method() === "POST" && path === "/report-tasks") {
      const input = request.postDataJSON(); posts.push(input);
      if (loseFirst && posts.length === 1) return route.abort("failed");
      return respond({ id: input.id, input, status: "ready", approval: null, draft_sha256: "a".repeat(64),
        draft: { title: "합성 상황 보고", summary: "검토용 요약", sources: [], limitations: [],
          sections: (input.section_outline || ["자동 개요", "자동 현황", "자동 조치"]).map((heading: string) => ({ heading, paragraphs: ["□ 합성 내용"], tables: [], diagrams: [] })) } });
    }
    if (request.method() !== "GET") return respond({ detail: "Unexpected write" }, 409);
    if (path === "/report-tasks") return respond([]);
    if (path === "/report-templates") return respond({ items: [template] });
    if (path === "/visual-standards") return respond({ items: [] });
    if (path === "/capabilities") return respond({ features: { reports: { configured: true, reachable: true, allowed: true } } });
    return respond({}, 404);
  });
  await page.goto("/report-structure-test.html");
  await expect(page.getByLabel("작성 목적", { exact: true })).toBeEnabled();
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill("합성 서비스의 점검 현황과 후속 대응을 정리해줘");
  return posts;
}

test("MOCK report outline: selected headings survive a lost reception and match reviewed sections", async ({ page }) => {
  const posts = await setup(page, true);
  await page.getByLabel("대제목 구성 방식").selectOption("response");
  const headings = ["개요", "현황", "대응", "향후 계획"];
  for (const [index, heading] of headings.entries()) await expect(page.getByRole("textbox", { name: `${index + 1}번째 대제목`, exact: true })).toHaveValue(heading);
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  await expect(page.getByLabel("대제목 구성 방식")).toBeDisabled();
  await expect(page.getByRole("textbox", { name: "1번째 대제목", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "같은 요청 접수 재확인", exact: true }).click();
  await expect(page.getByLabel("초안 내용 검토")).toBeVisible();
  expect(posts).toHaveLength(2);
  expect(posts[1]).toEqual(posts[0]);
  expect(posts[0].section_outline).toEqual(headings);
  expect(posts[0].research_policy).toBe("allow_unverified");
  await expect(page.locator(".report-review-section h2")).toHaveText(headings.map((heading, index) => `${index + 1}${heading}`));
  await expect(page.getByLabel("현재 보고서 고정 서식")).toContainText("개요 → 현황 → 대응 → 향후 계획");
  await expect(page.getByLabel("현재 보고서 고정 서식")).toContainText("경고와 함께 초안 검토");
});

test("MOCK report outline: edit, reorder, bounds, duplicate validation and automatic omission", async ({ page }) => {
  const posts = await setup(page);
  await page.getByLabel("대제목 구성 방식").selectOption("response");
  await page.getByRole("textbox", { name: "2번째 대제목", exact: true }).fill("점검 현황");
  await expect(page.getByLabel("대제목 구성 방식")).toHaveValue("custom");
  await page.getByRole("button", { name: "2번째 대제목 아래로", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "3번째 대제목", exact: true })).toHaveValue("점검 현황");
  await page.getByRole("textbox", { name: "1번째 대제목", exact: true }).fill("대응");
  await expect(page.getByRole("alert")).toContainText("서로 구별");
  await expect(page.getByRole("button", { name: "초안 작성하기", exact: true })).toBeDisabled();
  await page.getByRole("textbox", { name: "1번째 대제목", exact: true }).fill("요약");
  await page.getByRole("button", { name: "4번째 대제목 삭제", exact: true }).click();
  await expect(page.getByRole("button", { name: "3번째 대제목 삭제", exact: true })).toBeDisabled();
  for (let index = 4; index <= 6; index++) {
    await page.getByRole("button", { name: "+ 대제목 추가", exact: true }).click();
    await expect(page.getByRole("button", { name: "초안 작성하기", exact: true })).toBeDisabled();
    await page.getByRole("textbox", { name: `${index}번째 대제목`, exact: true }).fill(`추가 항목 ${index}`);
  }
  await expect(page.getByRole("button", { name: "+ 대제목 추가", exact: true })).toBeDisabled();
  await page.getByLabel("대제목 구성 방식").selectOption("auto");
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  await expect(page.getByLabel("초안 내용 검토")).toBeVisible();
  expect(posts).toHaveLength(1);
  expect(posts[0]).not.toHaveProperty("section_outline");
});
