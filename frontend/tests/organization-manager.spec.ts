import {test,expect} from '@playwright/test';
const dep={id:'dep',name:'기획부',kind:'department',parent_id:null,revision:'d1'},team={id:'team',name:'기획팀',kind:'team',parent_id:'dep',revision:'t1'};
const person={user_id:'writer',name:'테스트 사용자',kind:'member',is_admin:false,revision:'a1',affiliation:{user_id:'writer',department:null,team:null,revision:null}};
test.beforeEach(async({page})=>{
 await page.route('**/api/organization/units',route=>route.fulfill({json:[dep,team]}));
 await page.route('**/api/admin/accounts?*',route=>route.fulfill({json:{items:[person],total:1}}));
});
test('MOCK organization places/removes a user with the affiliation fence and leaves document permissions alone',async({page})=>{
 let p=structuredClone(person) as any;const calls:any[]=[];
 await page.route('**/api/admin/accounts?*',route=>route.fulfill({json:{items:[p],total:1}}));
 await page.route('**/api/organization/accounts/writer',route=>{const body=route.request().postDataJSON();calls.push(body);p={...p,affiliation:{user_id:'writer',department:body.department_id?dep:null,team:body.team_id?team:null,revision:'r2'}};return route.fulfill({json:p.affiliation});});
 await page.goto('/tests/fixtures/organization-manager.html');await page.getByRole('button',{name:'기획팀',exact:true}).click();await page.getByLabel('배치할 사용자').selectOption('writer');await page.getByRole('button',{name:'이 조직에 배치'}).click();await page.getByRole('dialog').getByRole('button',{name:'변경 확인'}).click();await expect(page.getByRole('button',{name:'소속 해제'})).toBeVisible();expect(calls[0]).toEqual({department_id:'dep',team_id:'team',expected_revision:null});
 await page.getByRole('button',{name:'소속 해제'}).click();await page.getByRole('dialog').getByRole('button',{name:'변경 확인'}).click();await expect(page.getByRole('button',{name:'소속 해제'})).toHaveCount(0);expect(calls[1]).toEqual({department_id:'dep',team_id:null,expected_revision:'r2'});
});
test('MOCK organization freezes failed creation and reuses its identity without blocking retry',async({page})=>{
 const calls:any[]=[];await page.route('**/api/organization/units',route=>{if(route.request().method()==='GET')return route.fulfill({json:[dep,team]});calls.push(route.request().postDataJSON());return calls.length===1?route.abort():route.fulfill({json:calls[0]});});
 await page.goto('/tests/fixtures/organization-manager.html');await page.getByRole('button',{name:'+ 부서 추가',exact:true}).click();await page.getByLabel('조직 이름').fill('추가 부서');await page.getByRole('dialog').getByRole('button',{name:'저장',exact:true}).click();await expect(page.getByLabel('상위 메뉴 잠금')).toHaveText('true');await expect(page.getByLabel('조직 이름')).toBeDisabled();await page.getByRole('dialog').getByRole('button',{name:'같은 요청 다시 확인'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);expect(calls).toHaveLength(2);expect(calls[0]).toEqual(calls[1]);await expect(page.getByLabel('상위 메뉴 잠금')).toHaveText('false');
});
test('MOCK organization role confirmation and HR example never perform a connection',async({page})=>{
 const calls:any[]=[];await page.route('**/api/admin/accounts/writer/role',route=>{calls.push(route.request().postDataJSON());return route.fulfill({json:{}});});
 await page.goto('/tests/fixtures/organization-manager.html');await page.getByRole('button',{name:'권한 변경'}).click();await expect(page.getByRole('dialog')).toContainText('시스템 관리자');await page.getByRole('dialog').getByRole('button',{name:'변경 확인'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);expect(calls[0]).toEqual({role:'admin',expected_revision:'a1'});
 await page.getByText('인사연동 준비',{exact:false}).click();await expect(page.getByText('실제 DB 연결·계정 생성·동기화는 수행하지 않습니다.',{exact:false})).toBeVisible();await page.getByRole('button',{name:'합성 데이터 매핑 예시 보기'}).click();await expect(page.getByRole('cell',{name:'sample01'})).toBeVisible();
});


test('MOCK three-level tree collapses and division filters users without direct assignment',async({page})=>{
 const division={id:'hq',name:'디지털본부',kind:'division',parent_id:null,revision:'h1'};
 await page.route('**/api/organization/units',route=>route.fulfill({json:[division,{...dep,parent_id:'hq'},team]}));
 await page.route('**/api/admin/accounts?*',route=>route.fulfill({json:{items:[{...person,affiliation:{department:dep,team,revision:'r1'}},{...person,user_id:'outside',name:'다른 부서'}],total:2}}));
 await page.goto('/tests/fixtures/organization-manager.html');
 await expect(page.getByRole('button',{name:'디지털본부',exact:true})).toContainText('1개 조직 · 1명');
 await expect(page.getByRole('button',{name:'기획팀',exact:true})).toContainText('1명');
 const unassigned=await page.getByRole('button',{name:'소속 미지정',exact:true}).boundingBox(),tree=await page.locator('.org-tree-roots').boundingBox();expect(unassigned!.y).toBeGreaterThan(tree!.y+tree!.height);
 await page.getByRole('button',{name:'디지털본부',exact:true}).click();
 await expect(page.locator('.org-people')).toContainText('테스트 사용자');
 await expect(page.locator('.org-people')).not.toContainText('다른 부서');
 await expect(page.getByRole('button',{name:'이 조직에 배치'})).toHaveCount(0);
 await page.getByRole('button',{name:'디지털본부 접기'}).click();
 await expect(page.getByRole('button',{name:'기획팀',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'디지털본부 펼치기'}).click();
 await page.getByRole('button',{name:'기획팀',exact:true}).click();
 await expect(page.getByRole('button',{name:'이 조직에 배치'})).toBeVisible();
});
