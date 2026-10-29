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
    if (path === "/api/workspaces") return ok([space]);
    if (path === "/api/workspaces/team1/members") return ok([{ user_id: "manager", role: "manager" }]);
    if (path === "/api/workspaces/team1/members/manager") return ok({ detail: "마지막 활성 공간 관리자는 제거하거나 권한을 낮출 수 없습니다." }, 409);
    if (path === "/api/documents/doc1/workspace") { changes.push(route.request().postDataJSON()); return ok({ ok: true, workspace_id: "team1" }); }
    return ok({ detail: "Unexpected synthetic route" }, 404);
  });
  await page.route("**/rhwp/**", route => route.fulfill({ status: 503, body: "synthetic" }));
  await page.goto("/");
  return changes;
}
test("MOCK spaces: manager cannot remove the last manager; accessible dialog fits mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await setup(page);
  await page.getByRole("button", { name: "팀·부서 공간 관리" }).click();
  const dialog = page.getByRole("dialog", { name: "팀·부서 공간", exact: true });
  await expect(dialog).toContainText("하위 팀 권한은 자동으로 생기지 않습니다");
  await dialog.getByLabel("manager 권한").selectOption("reader");
  await expect(dialog.getByRole("alert")).toContainText("마지막 활성 공간 관리자");
  await expect(dialog.getByLabel("manager 권한")).toHaveValue("manager");
  expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy();
});
test("MOCK spaces: explicit document assignment sends displayed saved-version fence", async ({ page }) => {
  const changes = await setup(page);
  await page.getByRole("button", { name: "합성 팀 보고서.hwpx 더보기" }).click();
  await page.getByRole("button", { name: "공간 지정", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "문서 공간 지정" });
  await expect(dialog).toContainText("이동하면 이전의 개별 공유 권한은 해제됩니다");
  page.once("dialog", d => d.accept());
  await dialog.getByRole("button", { name: "이 공간에 문서 배치" }).click();
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
    if(path==='/api/documents')data=[{...doc,role:'reader',can_trash:false,workspace_id:space.id,workspace_name:space.name,shared_with_me:true},{...doc,id:'other',name:'기존 문서.hwpx'}];
    return route.fulfill({json:data});
  });
  await page.goto('/');await page.getByLabel('문서 공간',{exact:true}).selectOption('shared-with-me');
  await expect(page.getByRole('button',{name:doc.name,exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'기존 문서.hwpx',exact:true})).toHaveCount(0);
  await expect(page.getByText('보안팀 · 개별 공유 · 열람',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:doc.name+' 더보기'}).click();await expect(page.getByRole('button',{name:'공유 권한',exact:true})).toHaveCount(0);
});
