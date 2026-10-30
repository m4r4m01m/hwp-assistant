import { test, expect, type Page } from "@playwright/test";
const source = {id: "source", name: "합성 보고서.hwpx", current_version: "v2", created_at: 1, role: "owner"};
const branch = {document_id: "branch", source_document_id: "source", base_version_id: "v1", owner_id: "writer", name: "대응방안 수정", current_version: "b2", created_at: 2, can_edit: true};
const request = {id: "request", branch_document_id: "branch", source_document_id: "source", source_version_id: "b2", target_version_id: "v2", author_id: "writer", status: "pending", created_at: 3, reviewed_by: null, reviewed_at: null, merged_version_id: null, can_review: true};
const nodes = [
  {id:"v1",document_id:"source",kind:"version",base_version:null,created_at:1,can_read:true,can_restore:true,can_edit:false,current:false,file_url:"/documents/source/versions/v1/file"},
  {id:"b1",document_id:"branch",kind:"version",base_version:null,created_at:2,can_read:true,can_restore:true,can_edit:false,current:false,file_url:"/documents/branch/versions/b1/file"},
  {id:"v2",document_id:"source",kind:"version",base_version:"v1",created_at:3,can_read:true,can_restore:true,can_edit:true,current:true,file_url:"/documents/source/versions/v2/file"},
  {id:"b2",document_id:"branch",kind:"version",base_version:"b1",created_at:4,can_read:true,can_restore:true,can_edit:true,current:true,file_url:"/documents/branch/versions/b2/file"},
];
const graph = {nodes,edges:[{from:"v1",to:"v2",kind:"save"},{from:"v1",to:"b1",kind:"branch"},{from:"b1",to:"b2",kind:"save"}],truncated:false};
test.beforeEach(async ({page})=>{await page.route("**/api/**/file",route=>route.fulfill({status:503,json:{detail:"Mock suite does not mount the native editor"}}));});
const flow = {graph,source_document: source, branches: [branch], requests: [], versions: [{id: "v1", base_version: null, created_at: 1}, {id: "v2", base_version: "v1", created_at: 2}]};
const review = {request, changes: [{path: "Contents/section0.xml"}], before_text: "대응방안을 검토할 예정이다.", after_text: "우선 대응방안 검토 예정", merged_sha256: "a".repeat(64), truncated: false};
async function load(page: Page, value: unknown = flow) {
  await page.route("**/api/documents/source/branches", route => route.fulfill({json: value}));
  await page.goto("/tests/fixtures/document-branches.html");
  await expect(page.getByRole("dialog", {name: "문서 이력"})).toBeVisible();
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
  await page.getByRole("button", {name: "작업본 만들기", exact:true}).click();
  await page.getByLabel("새 작업본 이름").fill("위험 분석");
  await page.getByRole("button", {name: "이 저장본에서 작업본 만들기"}).click();
  await expect(page.getByRole("button", {name: "닫기", exact:true})).toBeDisabled();
  await page.keyboard.press("Escape"); await expect(page.getByRole("dialog")).toBeVisible();
  release();
  await expect(page.getByRole("button", {name: "같은 요청으로 결과 확인"})).toBeVisible();
  await expect(page.getByLabel("새 작업본 이름")).toBeDisabled();
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
  await page.locator('[data-version-id="b2"]').click();
  await page.getByRole("button", {name:"편집",exact:true}).click();
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
  await page.locator('[data-version-id="b2"]').click();
  await page.getByRole("button",{name:"수정안 검토"}).click();
  await expect(page.locator(".branch-text-changes del")).not.toHaveCount(0);
  await expect(page.locator(".branch-text-changes ins")).not.toHaveCount(0);
  await expect(page.getByRole("button",{name:"승인·통합"})).toBeDisabled();
  await page.getByLabel("수정안을 확인했습니다.").check();
  await page.getByRole("button",{name:"승인·통합"}).click();
  await expect(page.locator(".branch-notice")).toContainText("새 버전으로 통합");
  expect(accepted).toEqual({merged_sha256:review.merged_sha256});
});
test("MOCK branches: author cannot self approve; stale target permits a fresh submission", async ({page}) => {
  await page.route("**/api/integration-requests/request/review", route => route.fulfill({json:{...review,request:{...request,can_review:false}}}));
  await load(page,{...flow,source_document:{...source,current_version:"v3"},requests:[{...request,can_review:false}]});
  await page.locator('[data-version-id="b2"]').click();
  await expect(page.getByRole("button",{name:"통합 요청",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"제출 내용 확인"}).click();
  await expect(page.getByRole("button",{name:"승인·통합"})).toHaveCount(0);
  await expect(page.getByText("수정안 작성·편집 참여자는 직접 승인할 수 없습니다.",{exact:false})).toBeVisible();
});
test("MOCK history: actual save nodes ascend, selection fetches only saved bytes and historical fork pins selected version", async ({page}, info) => {
  const writes: unknown[] = [], files:string[]=[];
  await page.route("**/api/**/file",route=>{files.push(new URL(route.request().url()).pathname);return route.fulfill({status:503,json:{detail:"synthetic preview unavailable"}});});
  await page.route("**/api/documents/source/branches",route=>{if(route.request().method()==="POST"){writes.push(route.request().postDataJSON());return route.fulfill({json:{document:source}});}return route.fulfill({json:flow});});
  await page.goto("/tests/fixtures/document-branches.html");
  const old=page.locator('[data-version-id="v1"]'),latest=page.locator('[data-version-id="v2"]');
  await expect(old).toBeVisible();
  expect((await latest.boundingBox())!.y).toBeLessThan((await old.boundingBox())!.y);
  await expect(page.locator('path[data-edge-kind="branch"]')).toHaveCount(1);
  await old.click();await expect(page.getByRole("button",{name:"편집",exact:true})).toHaveCount(0);
  await expect(page.getByRole("button",{name:"이 저장본 복원",exact:true})).toBeEnabled();
  await expect.poll(()=>files.includes("/api/documents/source/versions/v1/file")).toBeTruthy();expect(writes).toHaveLength(0);
  await page.getByRole("button",{name:"작업본 만들기",exact:true}).click();
  await page.getByRole("button",{name:"이 저장본에서 작업본 만들기"}).click();
  await expect.poll(()=>writes.length).toBe(1);expect(writes[0]).toMatchObject({source_version:"v1"});
  await page.screenshot({path:info.outputPath("history-tree-desktop.png")});
});
test("MOCK history: uncertain restore preserves current head and receipt while blocking other nodes", async ({page}) => {
  const keys:string[]=[];const urls:string[]=[];
  await page.route("**/versions/v1/restore?*",route=>{keys.push(route.request().headers()["idempotency-key"]);urls.push(route.request().url());return keys.length===1 ? route.fulfill({status:503,json:{detail:"uncertain"}}):route.fulfill({json:{id:"v3",document_id:"source"}});});
  await load(page);page.on("dialog",dialog=>dialog.accept());
  await page.locator('[data-version-id="v1"]').click();await page.getByRole("button",{name:"이 저장본 복원"}).click();
  await expect(page.getByRole("button",{name:"같은 복원 요청으로 확인"})).toBeVisible();
  await expect(page.locator('[data-version-id="b2"]')).toBeDisabled();
  await page.getByRole("button",{name:"같은 복원 요청으로 확인"}).click();
  await expect(page.locator(".branch-notice")).toContainText("새 버전으로 복원");
  expect(keys).toHaveLength(2);expect(keys[0]).toBe(keys[1]);expect(urls[0]).toBe(urls[1]);expect(urls[0]).toContain("base_version=v2");
});
test("MOCK history: one edit entry and owner explicitly adds participants",async({page})=>{
  const calls:string[]=[];
  await page.route("**/branch-participants",route=>route.fulfill({json:[]}));
  await page.route("**/branch-participants/colleague",route=>{calls.push(route.request().method());return route.fulfill({json:{user_id:"colleague",role:"editor"}});});
  await load(page,{...flow,branches:[{...branch,can_manage_participants:true}]});
  await page.locator('[data-version-id="b2"]').click();await expect(page.getByRole("button",{name:"공동편집",exact:true})).toHaveCount(0);await page.getByRole("button",{name:"편집",exact:true}).click();
  await expect(page.getByLabel("편집 문서")).toHaveText("branch");
  await page.locator('.history-more > summary').click();
  await page.getByText("작업본 참여자 관리",{exact:true}).click();await page.getByLabel("추가할 계정 ID").fill("colleague");await page.getByRole("button",{name:"참여자 추가"}).click();
  await expect.poll(()=>calls.length).toBe(1);expect(calls).toEqual(["PUT"]);
});
test("MOCK history: saved download, restore provenance, structure and arbitrary same-document comparison remain available",async({page})=>{
  const restored={...nodes[2],cause:"restore",restore_source:{version_id:"v1",created_at:1,author:{user_id:"writer",identity_kind:"authenticated"}}};
  const fullVersions=[nodes[0],restored].map(node=>({...node,size:100,sha256:"a".repeat(64),cause:"web-edit"}));
  let compared="";
  await page.route("**/api/documents/source/versions",route=>route.fulfill({json:fullVersions}));
  await page.route("**/versions/v2/changes",route=>route.fulfill({json:{version:restored,recorded:true,entries:[{path:"Contents/section0.xml",before_sha256:"before",after_sha256:"after"}]}}));
  await page.route("**/api/documents/source/compare?*",route=>{compared=route.request().url();return route.fulfill({status:503,json:{detail:"comparison engine excluded in mocked test"}});});
  await load(page,{...flow,graph:{...graph,nodes:[nodes[0],nodes[1],restored,nodes[3]]}});
  await page.locator('.history-more > summary').click();
  await expect(page.getByRole("link",{name:"이 버전 다운로드",exact:true})).toHaveAttribute("href","/api/documents/source/versions/v2/file");
  await expect(page.getByText("저장본에서 복원",{exact:false})).toBeVisible();
  await page.getByText("파일 진단 정보",{exact:true}).click();await expect(page.getByText("Contents/section0.xml",{exact:true})).toBeVisible();
  await page.getByRole("button",{name:"복원 원본 보기"}).click();await expect(page.locator('[data-version-id="v1"]')).toHaveAttribute("aria-pressed","true");
  await page.locator('.history-more > summary').click();
  await page.getByRole("button",{name:"비교 기준으로 지정"}).click();await page.locator('[data-version-id="v2"]').click();
  await page.getByRole("button",{name:"선택한 두 저장본 비교"}).click();await expect(page.getByRole("dialog",{name:"합성 보고서.hwpx · 문서 비교"})).toBeVisible();
  await expect.poll(()=>compared).toContain("left=v1&right=v2");
  await page.keyboard.press("Escape");await expect(page.getByRole("dialog",{name:"문서 이력"})).toBeVisible();
});
test("MOCK history: switching nodes exits integration preview and restores selected saved preview",async({page})=>{
  await page.route("**/api/integration-requests/request/review",route=>route.fulfill({json:review}));
  await page.route("**/api/integration-requests/request/preview",route=>route.fulfill({status:503,json:{detail:"synthetic preview"}}));
  await load(page,{...flow,requests:[request]});await page.locator('[data-version-id="b2"]').click();
  await page.getByRole("button",{name:"수정안 검토"}).click();await page.getByRole("button",{name:"문서 모양으로 전후 비교"}).click();
  await expect(page.getByRole("heading",{name:"통합 후 예상 문서"})).toBeVisible();await page.locator('[data-version-id="v1"]').click();
  await expect(page.getByRole("heading",{name:"선택한 저장본 미리보기"})).toBeVisible();await expect(page.getByRole("heading",{name:"통합 후 예상 문서"})).toHaveCount(0);
});
test("MOCK history: stale restore reloads graph and unlocks selection without an uncertain receipt loop",async({page})=>{
  let reads=0;
  await page.route("**/api/documents/source/branches",route=>{reads++;return route.fulfill({json:flow});});
  await page.route("**/versions/v1/restore?*",route=>route.fulfill({status:409,json:{detail:"최신 문서가 변경됐습니다."}}));
  await page.goto("/tests/fixtures/document-branches.html");page.on("dialog",dialog=>dialog.accept());
  await page.locator('[data-version-id="v1"]').click();await page.getByRole("button",{name:"이 저장본 복원"}).click();
  await expect(page.getByRole("alert").filter({hasText:"최신 문서가 변경"})).toBeVisible();
  await expect.poll(()=>reads).toBe(2);await expect(page.locator('[data-version-id="b2"]')).toBeEnabled();
  await expect(page.getByRole("button",{name:"같은 복원 요청으로 확인"})).toHaveCount(0);
});
test("MOCK history: private anchors respect actual ancestry and inspector fills its grid column",async({page},info)=>{
  const anchorGraph={nodes:[nodes[0],nodes[2],nodes[3],{id:"private-first",document_id:"branch",kind:"anchor"},{id:"private-middle",document_id:"branch",kind:"anchor"}],edges:[{from:"v1",to:"v2",kind:"save"},{from:"v1",to:"private-first",kind:"branch"},{from:"private-first",to:"private-middle",kind:"save"},{from:"private-middle",to:"b2",kind:"save"}],truncated:false};
  await load(page,{...flow,graph:anchorGraph});
  const y=async(selector:string)=>(await page.locator(selector).boundingBox())!.y;
  expect(await y('[data-version-id="b2"]')).toBeLessThan(await y('[data-anchor-id="private-middle"]'));
  expect(await y('[data-anchor-id="private-middle"]')).toBeLessThan(await y('[data-anchor-id="private-first"]'));
  expect(await y('[data-anchor-id="private-first"]')).toBeLessThan(await y('[data-version-id="v1"]'));
  const workspace=(await page.locator(".history-workspace").boundingBox())!,inspector=(await page.locator(".history-inspector").boundingBox())!;
  expect(inspector.width).toBeGreaterThan(workspace.width*.45);expect(Math.abs(inspector.x+inspector.width-workspace.x-workspace.width)).toBeLessThan(2);
  await page.screenshot({path:info.outputPath("history-tree-anchors-full-width.png")});
});

test("MOCK history: repeated node switches keep one live diagnostic and one matching preview",async({page})=>{
  const fetched:string[]=[],keyWarnings:string[]=[];
  page.on("console",message=>{if(/same key|unique.*key/i.test(message.text()))keyWarnings.push(message.text());});
  await page.route("**/versions/*/changes",route=>{
    const id=new URL(route.request().url()).pathname.split("/").at(-2)!;fetched.push(id);
    return route.fulfill({json:{version:nodes.find(node=>node.id===id),recorded:true,entries:[{path:`Contents/${id}.xml`,before_sha256:"before",after_sha256:"after"}]}});
  });
  await load(page);
  await expect(page.getByLabel("새 작업본 이름")).toHaveCount(0);
  expect(fetched).toEqual([]);
  for(const id of ["v1","v2","v1","v2","v1","v2"]){
    await page.locator(`[data-version-id="${id}"]`).click();
    await expect(page.locator('.history-more')).toHaveCount(1);
    await expect(page.locator('.history-structure-detail')).toHaveCount(1);
    await expect(page.locator('.history-inspector .branch-preview')).toHaveCount(1);
    await expect(page.locator('.history-more')).not.toHaveAttribute("open","");
    await page.locator('.history-more > summary').click();
    await page.getByText("파일 진단 정보",{exact:true}).click();
    await expect(page.locator('.history-structure-detail code')).toHaveText(`Contents/${id}.xml`);
    await expect(page.getByRole("link",{name:"이 버전 다운로드",exact:true})).toHaveAttribute("href",`/api/documents/source/versions/${id}/file`);
  }
  expect(fetched).toEqual(["v1","v2","v1","v2","v1","v2"]);
  expect(keyWarnings).toEqual([]);
});

for(const recorded of [false,true])test(`MOCK history: ${recorded?"unchanged":"unrecorded"} file diagnostics show an explicit explanation`,async({page})=>{
  await page.route("**/versions/v2/changes",route=>route.fulfill({json:{version:nodes[2],recorded,entries:[]}}));
  await load(page);await page.locator('.history-more > summary').click();await page.getByText("파일 진단 정보",{exact:true}).click();
  const detail=page.locator('.history-structure-detail');
  await expect(detail).toContainText(recorded?"직전 버전과 파일 구성의 차이가 없습니다.":"이 버전에는 파일 변경 기록이 없습니다.");
  await expect(detail.getByRole("alert")).toHaveCount(0);
  await expect(detail.getByRole("status")).toHaveCount(0);
  await expect(detail).toContainText("글·표·서식은 ‘변경 내용 보기’에서 비교하세요.");
});

test("MOCK history: wrong-version diagnostics show a retryable error instead of blank content",async({page})=>{
  let attempts=0;
  await page.route("**/versions/v2/changes",route=>{
    attempts++;
    return route.fulfill({json:{version:nodes[attempts===1?0:2],recorded:true,entries:[{path:"Contents/section0.xml",before_sha256:"before",after_sha256:"after"}]}});
  });
  await load(page);await page.locator('.history-more > summary').click();await page.getByText("파일 진단 정보",{exact:true}).click();
  const detail=page.locator('.history-structure-detail');
  await expect(detail.getByRole("alert")).toContainText("선택한 버전의 정보를 확인하지 못했습니다.");
  await expect(detail.locator('code')).toHaveCount(0);
  await detail.getByRole("button",{name:"다시 불러오기",exact:true}).click();
  await expect(detail.locator('code')).toHaveText("Contents/section0.xml");
  await expect(detail.getByRole("alert")).toHaveCount(0);expect(attempts).toBe(2);
});

test("MOCK history: selection scopes requests and merged navigation clears review",async({page})=>{
  const pending={...request,created_at:10};
  const merged={...request,id:"merged-request",status:"merged",created_at:8,reviewed_by:"independent-reviewer",merged_version_id:"v2"};
  const old={...request,id:"old-request",source_version_id:"b1",status:"rejected",author_id:"previous-writer",created_at:5};
  await page.route("**/api/integration-requests/request/review",route=>route.fulfill({json:{...review,request:pending}}));
  await load(page,{...flow,requests:[pending,merged,old]});
  await page.locator('[data-version-id="b1"]').click();
  await expect(page.locator('.history-integration-list article')).toHaveCount(1);
  await expect(page.locator('.history-integration-list')).toContainText("previous-writer 요청");
  await expect(page.getByRole("button",{name:"수정안 검토",exact:true})).toHaveCount(0);
  await page.locator('[data-version-id="b2"]').click();
  await expect(page.locator('.history-integration-list article')).toHaveCount(2);
  await expect(page.locator('.history-integration-list')).not.toContainText("previous-writer");
  await expect(page.getByRole("button",{name:"통합 요청",exact:true})).toHaveCount(0);
  await page.getByRole("button",{name:"수정안 검토",exact:true}).click();
  const reviewPanel=page.getByRole("region",{name:"통합 수정안 검토",exact:true});
  await expect(reviewPanel).toBeVisible();await reviewPanel.getByLabel("수정안을 확인했습니다.").check();
  await page.getByRole("button",{name:"통합된 문서 보기",exact:true}).click();
  await expect(page.locator('[data-version-id="v2"]')).toHaveAttribute("aria-pressed","true");
  await expect(reviewPanel).toHaveCount(0);await expect(page.getByRole("button",{name:"승인·통합",exact:true})).toHaveCount(0);
  await expect(page.locator('.history-integration-list article')).toHaveCount(1);
  await expect(page.locator('.history-integration-list')).toContainText("independent-reviewer 승인");
  await page.locator('[data-version-id="b2"]').click();await page.getByRole("button",{name:"수정안 검토",exact:true}).click();
  await expect(reviewPanel.getByLabel("수정안을 확인했습니다.")).not.toBeChecked();
  await expect(reviewPanel.getByRole("button",{name:"승인·통합",exact:true})).toBeDisabled();
});
