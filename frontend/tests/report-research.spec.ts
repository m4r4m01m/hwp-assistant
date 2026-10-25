import { test, expect, type Page } from "@playwright/test";

// HTTP/editor fixtures only. Actual-engine tests live in report-research-save.spec.ts.
const template = { id: "standard-business", revision: 1, standard_id: "synthetic", reference_sha256: "a".repeat(64),
  standard_profile_sha256: "b".repeat(64), profile_sha256: "c".repeat(64), values: { name: "합성 구성", description: "", writing_criteria: [] } };
const notice = (status = "unverified") => ({ version: "research-notice-v1", policy: "allow_unverified", requested: status !== "not_requested",
  status, completed_calls: status === "completed" ? 1 : 0, source_verification: "unverified",
  warnings: [{ code: status === "not_requested" ? "research_not_requested" : status === "completed" ? "sources_unverified" : "research_incomplete",
    message: status === "not_requested" ? "이번 요청에서는 공개자료를 조회하지 않았습니다." : status === "completed" ? "AI가 제시한 출처는 개별 원문 검증이 완료되지 않았습니다." : "공개자료 조회를 완료하지 못했으며 최신 정보와 출처는 미검증 상태입니다." }] });
const draft = (status = "unverified") => ({ title: "합성 자료 확인 보고서", summary: "□ (요약) 합성 자료를 검토해야 함 [S1]", emphasis: [],
  sections: [{ heading: "합성 검토 범위", paragraphs: ["□ (제안) 원문 내용과 공개자료를 사용자가 확인할 필요 [S1]"], tables: [], diagrams: [] }],
  sources: [{ id: "S1", title: "합성 참고 자료", url: "https://example.invalid/synthetic-source", published: "2026-01-01" }],
  limitations: Array.from({ length: 6 }, (_, index) => `기존 확인 사항 ${index + 1}은 삭제하거나 대체하지 않음`), research_notice: notice(status) });
const ready = (status = "unverified") => ({ id: "00000000-0000-4000-8000-000000000011", status: "ready", input: { prompt: "합성 보고서 작성 요청", research: status !== "not_requested", research_policy: "allow_unverified" },
  draft: draft(status), draft_sha256: "a".repeat(64), preview_id: null, error: "", approval: null, execution: { web_search_calls: status === "completed" ? 1 : 0 } });

async function workspace(page: Page, options: { initial?: any; loseFirstPost?: boolean } = {}) {
  let current = options.initial || null;
  const posts: { path: string; body: any }[] = [];
  await page.route("**/report-research-test.html", route => route.fulfill({ contentType: "text/html", body: '<!doctype html><html lang="ko"><body><div id="root"></div><script type="module" src="/tests/fixtures/report-failure.tsx"></script></body></html>' }));
  await page.route("**/src/editor.ts*", route => route.fulfill({ contentType: "text/javascript", body: 'export class EditorAdapter { static mount() { window.__researchEngineMounts = (window.__researchEngineMounts || 0) + 1; throw Error("Synthetic editor is not mounted by content review"); } }' }));
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace(/^.*\/api/, "");
    const respond = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (request.method() === "POST") {
      const body = request.postDataJSON(); posts.push({ path, body });
      if (path === "/report-tasks") {
        current = { ...ready(body.research ? "unverified" : "not_requested"), id: body.id, input: body };
        if (options.loseFirstPost && posts.length === 1) return route.abort("failed");
        return respond(current);
      }
      if (path.endsWith("/revise")) {
        current = { ...current, id: body.id, execution: { ...current.execution, revision_of: current.id } };
        return respond(current);
      }
      return respond({ detail: "Unexpected synthetic mutation" }, 409);
    }
    if (path === "/report-tasks") return respond(current ? [current] : []);
    if (path.startsWith("/report-tasks/")) return respond(current || { detail: "No synthetic task" }, current ? 200 : 404);
    if (path === "/report-templates") return respond({ items: [template], default_id: template.id });
    if (path === "/visual-standards") return respond({ items: [] });
    if (path === "/capabilities") return respond({ features: { reports: { configured: true, reachable: true, allowed: true, message: "합성 Codex 설정" } } });
    return respond({ detail: "Unexpected synthetic request" }, 404);
  });
  if (current) await page.addInitScript(id => sessionStorage.setItem("hwp-report-task", id), current.id);
  await page.goto("/report-research-test.html");
  return { posts, current: () => current };
}

test("MOCK research: new requests freeze warning policy by default and repeat an uncertain POST byte-for-byte", async ({ page }) => {
  const state = await workspace(page, { loseFirstPost: true });
  await expect(page.getByRole("checkbox", { name: /공개 웹 자료/ })).toBeChecked();
  await expect(page.getByLabel("새 요청의 자료 조회 정책")).toContainText("조회가 완료되지 않아도");
  await page.getByLabel("보고서 작성 요청", { exact: true }).fill("합성 공개 동향 보고서 작성 요청");
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  await expect(page.getByRole("button", { name: "같은 요청 접수 재확인", exact: true })).toBeEnabled();
  expect(state.posts).toHaveLength(1);
  expect(state.posts[0].body).toMatchObject({ research: true, research_policy: "allow_unverified", prompt: "합성 공개 동향 보고서 작성 요청" });
  await expect(page.getByLabel("보고서 작성 요청", { exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "같은 요청 접수 재확인", exact: true }).click();
  await expect(page.getByLabel("초안 내용 검토", { exact: true })).toBeVisible();
  expect(state.posts).toHaveLength(2); expect(state.posts[1]).toEqual(state.posts[0]);
  await expect(page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => (window as any).__researchEngineMounts || 0)).toBe(0);
});

test("MOCK research: unverified final draft keeps six limitations, source candidates and warning across reload and feedback", async ({ page }) => {
  const state = await workspace(page, { initial: ready() });
  const review = page.getByLabel("초안 내용 검토", { exact: true });
  await expect(review.getByLabel("자료 확인 상태")).toContainText("자료 조회 미완료 · 최신 정보 미검증");
  for (let index = 1; index <= 6; index++) await expect(review.getByText(`기존 확인 사항 ${index}은 삭제하거나 대체하지 않음`, { exact: true })).toBeVisible();
  await review.getByText("미검증 참고 출처 1개", { exact: true }).click();
  await expect(review.getByText("웹 확인 미완료", { exact: true })).toBeVisible();
  await expect(review.getByText("https://example.invalid/synthetic-source", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "합성 참고 자료", exact: true })).toHaveCount(0);
  expect(state.posts).toEqual([]);
  await page.getByRole("button", { name: "결과·출처", exact: true }).click();
  await expect(page.getByLabel("보고서 결과와 출처").getByLabel("자료 확인 상태")).toContainText("자료 조회 미완료");
  await page.reload();
  await expect(review.getByLabel("자료 확인 상태")).toContainText("최신 정보 미검증");
  expect(state.posts).toEqual([]);
  await page.getByLabel("보완 의견", { exact: true }).fill("합성 확인 절차를 구체화해 주세요");
  await page.getByRole("button", { name: "의견 반영해 다시 작성", exact: true }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].path).toMatch(/\/revise$/);
  expect(Object.keys(state.posts[0].body).sort()).toEqual(["draft_sha256", "feedback", "id"]);
  await expect(review.getByLabel("자료 확인 상태")).toContainText("최신 정보 미검증");
});

for (const status of ["not_requested", "completed"]) test(`MOCK research: ${status} is distinct and source candidates never imply verification`, async ({ page }) => {
  await workspace(page, { initial: ready(status) });
  const review = page.getByLabel("초안 내용 검토", { exact: true });
  await expect(review.getByLabel("자료 확인 상태")).toContainText(status === "not_requested" ? "공개자료 조회 안 함" : "공개자료 조회 완료 · 출처별 확인 필요");
  if (status === "not_requested") await expect(review.getByLabel("자료 확인 상태")).not.toContainText("자료 조회 미완료");
  await review.getByText("미검증 참고 출처 1개", { exact: true }).click();
  await expect(review.getByText("웹 확인 미완료", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "합성 참고 자료", exact: true })).toHaveCount(0);
});

test("MOCK research: legacy policy stays frozen while an explicitly new request acquires the warning policy", async ({ page }) => {
  const legacy: any = ready(); delete legacy.input.research_policy; delete legacy.draft.research_notice;
  const frozen = JSON.stringify(legacy.input);
  const state = await workspace(page, { initial: legacy });
  await expect(page.getByLabel("초안 내용 검토", { exact: true })).toBeVisible();
  await expect(page.getByLabel("초안 내용 검토", { exact: true }).getByLabel("자료 확인 상태")).toHaveCount(0);
  await page.getByText("AI가 제시한 출처 1개", { exact: true }).click();
  await expect(page.getByRole("link", { name: "합성 참고 자료", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "작성 요청", exact: true }).click();
  await expect(page.getByLabel("현재 보고서 고정 서식")).toContainText("초안 생성 중단 (기존 요청 정책)");
  expect(JSON.stringify(state.current().input)).toBe(frozen); expect(state.posts).toEqual([]);
  await page.getByRole("button", { name: "초안 작성하기", exact: true }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0].body.research_policy).toBe("allow_unverified");
  expect(state.posts[0].body.id).not.toBe(legacy.id);
});

test("MOCK research: inherited uncertainty stays visible after a later completed search", async ({ page }) => {
  const task = ready("completed");
  task.draft.research_notice.warnings.push({ code: "inherited_research_unverified", message: "보완 전 초안의 공개자료 조회 미완료 경고를 유지합니다." });
  await workspace(page, { initial: task });
  await expect(page.getByLabel("초안 내용 검토", { exact: true }).getByLabel("자료 확인 상태")).toContainText("이전 초안의 자료 확인 미완료 · 재검토 필요");
  await expect(page.getByLabel("초안 내용 검토", { exact: true }).getByLabel("자료 확인 상태")).toContainText("보완 전 초안의 공개자료 조회 미완료 경고를 유지합니다.");
  await page.reload();
  await expect(page.getByLabel("초안 내용 검토", { exact: true }).getByLabel("자료 확인 상태")).toContainText("재검토 필요");
});

for (const status of ["running", "failed", "cancelled"]) test(`MOCK research: ${status} cannot promote a partial or stale draft to content review`, async ({ page }) => {
  const task = { ...ready(), status, error: status === "failed" ? "최종 출력 검증을 완료하지 못했습니다." : "",
    execution: { failure: { code: "timeout", stage: "writing", research_calls: 0 }, progress: { stage: "writing", revision: 1, activity: "", research_calls: 0, blocks: [{ kind: "body", text: "합성 부분 내용" }], timings: {} } } };
  const state = await workspace(page, { initial: task });
  await expect(page.getByTestId("stream-report-paragraph")).toHaveText("합성 부분 내용");
  await expect(page.getByLabel("초안 내용 검토", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "이 내용으로 문서 만들기", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "초안 확정·서버 저장", exact: true })).toHaveCount(0);
  expect(state.posts).toEqual([]);
  expect(await page.evaluate(() => (window as any).__researchEngineMounts || 0)).toBe(0);
});
