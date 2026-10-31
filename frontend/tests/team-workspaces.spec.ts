import { test, expect, type Page } from "@playwright/test";
const doc = { id: "doc1", name: "합성 팀 보고서.hwpx", current_version: "v1", created_at: 1, role: "owner", can_trash: true };
const space = { id: "team1", name: "보안팀", kind: "team", parent_id: null, role: "manager" };
async function setup(page: Page) {
  const changes: unknown[] = [];
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const ok = (data: unknown, status = 200) => route.fulfill({ status, json: data });
    if (path === "/api/auth/status") return ok({ accounts_enabled: true });
    if (path === "/api/auth/me") return ok({ user_id: "manager", authentication_required: true, is_admin: true });
    if (path === "/api/capabilities") return ok({ features: {} });
    if (path === "/api/documents") return ok([doc]);
    if (path === "/api/workspaces" || path === "/api/workspaces/send-targets") return ok([space]);
    if (path === "/api/folders") return ok([]);
    if (path === "/api/organization/me") return ok({user_id:'manager',department:null,team:null,revision:null});
    if (path === "/api/organization/units") return ok([]);
    if (path === "/api/workspaces/team1/members") return ok([{ user_id: "manager", role: "manager" }]);
    if (path === "/api/workspaces/team1/members/manager") return ok({ detail: "마지막 활성 공간 관리자는 제거하거나 권한을 낮출 수 없습니다." }, 409);
    if (path === "/api/documents/doc1/workspace") { changes.push(route.request().postDataJSON()); return ok({ ok: true, workspace_id: "team1" }); }
    return ok({ detail: "Unexpected synthetic route" }, 404);
  });
  await page.route("**/rhwp/**", route => route.fulfill({ status: 503, body: "synthetic" }));
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "문서함", exact: true })).toBeVisible();
  return changes;
}
test("MOCK spaces: manager cannot remove the last manager; accessible dialog fits mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await setup(page);
  await page.getByRole("button", { name: "공유받은 문서",exact:true }).click();
  await page.getByRole("navigation",{name:"문서함",exact:true}).locator("summary").filter({hasText:"다른 공유 문서함"}).click();
  await page.getByRole("button", { name: "공유 문서함 관리" }).click();
  const dialog = page.getByRole("dialog", { name: "공유 문서함 관리", exact: true });
  await expect(dialog).toContainText("계정의 소속 지정만으로 공유 권한이 생기지는 않습니다");
  await dialog.getByLabel("manager 권한").selectOption("reader");
  await expect(dialog.getByRole("alert")).toContainText("마지막 활성 공간 관리자");
  await expect(dialog.getByLabel("manager 권한")).toHaveValue("manager");
  expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy();
});
test("MOCK spaces: explicit document assignment sends displayed saved-version fence", async ({ page }) => {
  const changes = await setup(page);
  await page.getByRole("button", { name: "합성 팀 보고서.hwpx 더보기" }).click();
  await page.getByRole("button", { name: "부서·팀으로 보내기", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "부서·팀으로 보내기" });
  await expect(dialog).toContainText("이전 문서함과 개별 초대자의 공유 권한은 해제됩니다");
  await dialog.getByLabel("받는 문서함").selectOption("team1");
  await dialog.getByRole("button", { name: "이동하기" }).click();
  await expect(dialog).toHaveCount(0);
  expect(changes).toEqual([{ workspace_id: "team1", expected_version: "v1" }]);
});

test("MOCK spaces: manager shares one team document even in legacy shared-library mode", async ({ page }) => {
  await page.setViewportSize({width:430,height:900});
  let grants: {user_id:string;role:string}[]=[];
  await page.route("**/api/**",async route=>{
    const path=new URL(route.request().url()).pathname, method=route.request().method();
    const ok=(data:unknown)=>route.fulfill({json:data});
    if(path==='/api/auth/status')return ok({accounts_enabled:true});
    if(path==='/api/auth/me')return ok({user_id:'manager',authentication_required:true,is_admin:true,library_access_mode:'shared'});
    if(path==='/api/capabilities')return ok({features:{}});
    if(path==='/api/workspaces')return ok([space]);
    if(path==='/api/folders')return ok([]);
    if(path==='/api/documents')return ok([{...doc,workspace_id:space.id,workspace_name:space.name}]);
    if(path==='/api/documents/doc1/access'){
      if(method==='PUT')grants=[route.request().postDataJSON()];
      return ok(method==='GET'?grants:{ok:true});
    }
    if(path==='/api/documents/doc1/access/test4'&&method==='DELETE'){grants=[];return ok({ok:true});}
    return route.fulfill({status:404,json:{detail:'synthetic unused'}});
  });
  await page.goto('/');await page.getByRole('button',{name:doc.name+' 더보기'}).click();
  await page.getByRole('button',{name:'공유 권한',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:doc.name+' · 공유 권한'});
  await expect(dialog).toContainText('다른 팀 사람에게 이 문서만 공유합니다');
  await dialog.getByLabel('공유 대상 사용자').fill('test4');await dialog.getByRole('button',{name:'권한 저장'}).click();
  await expect(dialog.getByLabel('test4 공유 권한')).toHaveValue('reader');
  expect(grants).toEqual([{user_id:'test4',role:'reader'}]);
  await dialog.getByLabel('test4 공유 권한').selectOption('editor');await expect(dialog.getByRole('status')).toHaveText('공유 권한을 반영했습니다.');
  expect(grants[0].role).toBe('editor');
  expect(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth+1)).toBeTruthy();
  await dialog.getByRole('button',{name:'권한 회수'}).click();await expect(dialog).toContainText('별도로 초대한 사람이 없습니다');expect(grants).toEqual([]);
});

test("MOCK spaces: invited reader finds only individually shared rows and cannot reshare",async({page})=>{
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/ai-connection')return route.fulfill({status:404,json:{detail:'not used in this fixture'}});
    let data:unknown={};
    if(path==='/api/auth/status')data={accounts_enabled:true};
    if(path==='/api/auth/me')data={user_id:'test4',authentication_required:true,library_access_mode:'shared'};
    if(path==='/api/capabilities')data={features:{}};
    if(path==='/api/workspaces')data=[];
    if(path==='/api/folders')data=[];
    if(path==='/api/documents')data=[{...doc,role:'reader',can_trash:false,workspace_id:space.id,workspace_name:space.name,shared_with_me:true},{...doc,id:'other',name:'기존 문서.hwpx'}];
    return route.fulfill({json:data});
  });
  await page.goto('/');await page.getByRole('navigation',{name:'문서함'}).getByRole('button',{name:'공유받은 문서',exact:true}).click();
  await expect(page.getByRole('button',{name:doc.name,exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'기존 문서.hwpx',exact:true})).toHaveCount(0);
  await expect(page.locator('.library-document-path')).toHaveText('보안팀');
  await page.getByRole('button',{name:doc.name+' 더보기'}).click();await expect(page.getByRole('button',{name:'공유 권한',exact:true})).toHaveCount(0);
});


test('MOCK send copy freezes destination, version and id after response loss',async({page})=>{
 await setup(page);const calls:unknown[]=[];
 await page.route('**/api/documents/doc1/copies',route=>{calls.push(route.request().postDataJSON());return calls.length===1?route.abort():route.fulfill({json:{...doc,id:'copy',workspace_id:'team1'}});});
 await page.getByRole('button',{name:doc.name+' 더보기'}).click();await page.getByRole('button',{name:'부서·팀으로 보내기',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'부서·팀으로 보내기'});
 await dialog.getByLabel('받는 문서함').selectOption('team1');await dialog.getByRole('radio',{name:/사본 보내기/}).check();await dialog.getByLabel('사본 이름').fill('공유용 사본.hwpx');
 await dialog.getByRole('button',{name:'사본 보내기',exact:true}).click();await expect(dialog.getByRole('alert')).toBeVisible();await expect(dialog.getByLabel('받는 문서함')).toBeDisabled();
 await dialog.getByRole('button',{name:'전송 결과 다시 확인'}).click();await expect(dialog).toHaveCount(0);expect(calls[0]).toEqual(calls[1]);expect(calls[0]).toMatchObject({workspace_id:'team1',source_version:'v1',name:'공유용 사본.hwpx'});await expect(page.getByRole('status').filter({hasText:'원본은 그대로'})).toBeVisible();
});


test("MOCK spaces: original owner with workspace editor access can return a document privately", async ({ page }) => {
  await setup(page);
  await page.route("**/api/documents", route => route.fulfill({ json: [{
    ...doc, role: "editor", owned_by_me: true, workspace_id: space.id, workspace_name: space.name,
  }] }));
  const placements: unknown[] = [];
  await page.route("**/api/documents/doc1/workspace", route => {
    placements.push(route.request().postDataJSON());
    return route.fulfill({ json: { ok: true, workspace_id: null } });
  });
  await page.reload();
  await page.getByRole("button", { name: doc.name + " 더보기" }).click();
  await page.getByRole("button", { name: "내 문서함으로 이동", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "문서함 이동", exact: true });
  await dialog.getByLabel("문서함 선택").selectOption("");
  page.once("dialog", prompt => prompt.accept());
  await dialog.getByRole("button", { name: "내 문서함으로 이동", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(placements).toEqual([{ workspace_id: null, expected_version: "v1" }]);
});
