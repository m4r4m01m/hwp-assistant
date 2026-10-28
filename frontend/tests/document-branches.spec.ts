import { test, expect, type Page } from "@playwright/test";
const source = {id: "source", name: "합성 보고서.hwpx", current_version: "v2", created_at: 1, role: "owner"};
const branch = {document_id: "branch", source_document_id: "source", base_version_id: "v1", owner_id: "writer", name: "대응방안 수정", current_version: "b2", created_at: 2, can_edit: true};
const request = {id: "request", branch_document_id: "branch", source_document_id: "source", source_version_id: "b2", target_version_id: "v2", author_id: "writer", status: "pending", created_at: 3, reviewed_by: null, reviewed_at: null, merged_version_id: null, can_review: true};
const flow = {source_document: source, branches: [branch], requests: [], versions: [{id: "v1", base_version: null, created_at: 1}, {id: "v2", base_version: "v1", created_at: 2}]};
const review = {request, changes: [{path: "Contents/section0.xml"}], before_text: "대응방안을 검토할 예정이다.", after_text: "우선 대응방안 검토 예정", merged_sha256: "a".repeat(64), truncated: false};
async function load(page: Page, value: unknown = flow) {
  await page.route("**/api/documents/source/branches", route => route.fulfill({json: value}));
  await page.goto("/tests/fixtures/document-branches.html");
  await expect(page.getByRole("dialog", {name: "작업본·통합"})).toBeVisible();
}
test("MOCK branches: uncertain creation retains exact frozen request and blocks duplicate actions", async ({page}) => {
  const bodies: unknown[] = [];
  let release!: () => void;
  const wait = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/documents/source/branches", async route => {
    if (route.request().method() === "GET") return route.fulfill({json: flow});
    bodies.push(route.request().postDataJSON());
    if (bodies.length === 1) { await wait; return route.fulfill({status: 503, json: {detail: "synthetic failure"}}); }
    return route.fulfill({json: {document: {...source, id: "new"}}});
  });
  await page.goto("/tests/fixtures/document-branches.html");
  await page.getByLabel("작업본 이름").fill("위험 분석");
  await page.getByRole("button", {name: "내 작업본 만들기"}).click();
  await expect(page.getByRole("button", {name: "닫기", exact:true})).toBeDisabled();
  await page.keyboard.press("Escape"); await expect(page.getByRole("dialog")).toBeVisible();
  release();
  await expect(page.getByRole("button", {name: "같은 요청으로 결과 확인"})).toBeVisible();
  await expect(page.getByLabel("작업본 이름")).toBeDisabled();
  await page.getByRole("button", {name: "같은 요청으로 결과 확인"}).click();
  await expect(page.getByLabel("목록 갱신 횟수")).toHaveText("1");
  expect(bodies).toHaveLength(2); expect(bodies[0]).toEqual(bodies[1]);
  expect(bodies[0]).toMatchObject({source_version:"v2", name:"위험 분석"});
});
test("MOCK branches: graph opens only own branch and submission freezes saved source and target", async ({page}, info) => {
  let submitted: unknown;
  await page.route("**/api/documents/branch/integration-requests", route => { submitted = route.request().postDataJSON(); return route.fulfill({json: request}); });
  await load(page, {...flow, branches:[branch, {...branch,document_id:"other",owner_id:"reviewer",can_edit:false,current_version:null,name:"다른 작업본"}]});
  await expect(page.getByLabel("문서 분기와 통합 흐름")).toContainText("다른 작업본");
  await expect(page.getByRole("button", {name:"작업본 편집",exact:true})).toHaveCount(1);
  await page.getByRole("button", {name:"작업본 편집",exact:true}).click();
  await expect(page.getByLabel("편집 문서")).toHaveText("branch");
  await page.getByRole("button", {name:"통합 요청",exact:true}).click();
  await expect(page.getByLabel("목록 갱신 횟수")).toHaveText("1");
  expect(submitted).toMatchObject({source_version:"b2",target_version:"v2"});
  await page.setViewportSize({width:430,height:800});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(430);
  await page.screenshot({path:info.outputPath("branch-flow-mobile.png")});
});
test("MOCK branches: reviewer must inspect candidate and acceptance sends reviewed hash", async ({page}) => {
  let accepted: unknown;
  await page.route("**/api/integration-requests/request/review", route => route.fulfill({json: review}));
  await page.route("**/api/integration-requests/request/accept", route => { accepted=route.request().postDataJSON(); return route.fulfill({json:{...request,status:"merged",merged_version_id:"v3"}}); });
  await load(page,{...flow,branches:[{...branch,can_edit:false,current_version:null}],requests:[request]});
  await page.getByRole("button",{name:"수정안 검토"}).click();
  await expect(page.locator(".branch-text-changes del")).not.toHaveCount(0);
  await expect(page.locator(".branch-text-changes ins")).not.toHaveCount(0);
  await expect(page.getByRole("button",{name:"승인·통합"})).toBeDisabled();
  await page.getByLabel("수정안을 확인했습니다.").check();
  await page.getByRole("button",{name:"승인·통합"}).click();
  await expect(page.getByRole("dialog").getByRole("status")).toContainText("새 버전으로 통합");
  expect(accepted).toEqual({merged_sha256:review.merged_sha256});
});
test("MOCK branches: author cannot self approve; stale target permits a fresh submission", async ({page}) => {
  await page.route("**/api/integration-requests/request/review", route => route.fulfill({json:{...review,request:{...request,can_review:false}}}));
  await load(page,{...flow,source_document:{...source,current_version:"v3"},requests:[{...request,can_review:false}]});
  await expect(page.getByRole("button",{name:"새 저장본으로 통합 요청"})).toBeEnabled();
  await page.getByRole("button",{name:"제출 내용 확인"}).click();
  await expect(page.getByRole("button",{name:"승인·통합"})).toHaveCount(0);
  await expect(page.getByText("본인이 제출한 수정안은 직접 승인할 수 없습니다.",{exact:false})).toBeVisible();
});
