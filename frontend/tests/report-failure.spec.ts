import { test, expect } from "@playwright/test";

// HTTP and editor are mocked; these tests never invoke a model or store HWPX.
for (const partial of [true, false]) test(`MOCK report failure: research diagnosis, ${partial ? "partial" : "empty"} output, explicit fresh request and no preview/save`, async ({ page }) => {
  const posts: { path: string; body: any }[] = [];
  const id = "00000000-0000-4000-8000-000000000001";
  const failed = { id, input: { prompt: "합성 공개 동향 보고서", research: true }, status: "failed", draft: null, draft_sha256: null,
    approval: null, error: "공개자료 조회를 완료하지 못했습니다. 보고서를 확정하지 않았습니다.", created_at: 1,
    execution: { failure: { code: "research_unavailable", stage: "researching", research_calls: 0 },
      progress: { stage: "researching", revision: 2, activity: "", research_calls: 0, elapsed_ms: 99414,
        blocks: partial ? [{ text: "합성 작성 중 본문", kind: "body" }] : [], timings: { startup_ms: 2152 } } } };
  const template = { id: "standard-business", revision: 1, standard_id: "synthetic", reference_sha256: "a".repeat(64),
    standard_profile_sha256: "b".repeat(64), profile_sha256: "c".repeat(64), values: { name: "합성 구성", description: "", writing_criteria: [] } };
  await page.route("**/report-failure-test.html", route => route.fulfill({ contentType: "text/html", body: '<!doctype html><html lang="ko"><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/report-failure.tsx"></script></body></html>' }));
  await page.route("**/src/editor.ts*", route => route.fulfill({ contentType: "text/javascript", body: 'export class EditorAdapter { static mount() { window.__failureEngineMounts = (window.__failureEngineMounts || 0) + 1; throw Error("Unexpected editor mount"); } }' }));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    const respond = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (request.method() === "POST") {
      posts.push({ path, body: request.postDataJSON() });
      if (path === "/report-tasks") return respond({ ...failed, id: posts.at(-1)!.body.id, input: posts.at(-1)!.body });
      return respond({ detail: "Unexpected mutation" }, 409);
    }
    if (path === "/report-tasks") return respond([failed]);
    if (path === `/report-tasks/${id}`) return respond(failed);
    if (path === "/report-templates") return respond({ items: [template], default_id: template.id });
    if (path === "/visual-standards") return respond({ items: [] });
    if (path === "/capabilities") return respond({ features: { reports: { configured: true, reachable: true, allowed: true, message: "Codex 연결됨" } } });
    return respond({ detail: "Unexpected request" }, 404);
  });
  await page.addInitScript(task => sessionStorage.setItem("hwp-report-task", task), id);
  await page.goto("/report-failure-test.html");
  await expect(page.getByRole("alert")).toContainText("공개자료 조회를 완료하지 못했습니다.");
  await expect(page.getByRole("alert")).toContainText("Codex 웹 검색 연결");
  await expect(page.getByText("생성이 완료되지 않은 내용입니다. 한글 문서로 확정하거나 저장하지 않았습니다.", { exact: true })).toBeVisible();
  if (partial) await expect(page.getByTestId("stream-report-paragraph")).toHaveText("합성 작성 중 본문");
  else await expect(page.getByText("완료된 초안이 없습니다. 실패 안내와 작성 요청을 확인하세요.")).toBeVisible();
  await page.getByText("실패 단계 확인", { exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("공개자료 조회 0회 완료");
  await expect(page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("공개자료 조회를 완료하지 못했습니다.");
  expect(posts).toEqual([]); // Reload cannot replay generation or approval.
  await page.getByRole("button", { name: "작성 요청 확인", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: /공개 웹 자료/ })).toBeChecked();
  expect(posts).toEqual([]); // Opening recovery keeps research and requires an explicit request.
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0].path).toBe("/report-tasks");
  expect(posts[0].body.id).not.toBe(id);
  expect(posts[0].body.research).toBe(true);
  expect(await page.evaluate(() => (window as any).__failureEngineMounts || 0)).toBe(0);
});
