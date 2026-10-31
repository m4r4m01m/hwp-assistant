import {test,expect} from '@playwright/test';
test('live preview reconnects only to the same user and requires a choice for ambiguous tabs',async({page})=>{
 let peers=[{id:'old',user_id:'writer',typing:false,page:1,page_count:1,available:true}],watched='';
 await page.route('**/api/documents/mock/coedit/live',route=>{
  const body=route.request().postDataJSON();watched=body.watch||'';const peer=peers.find(p=>p.id===watched);
  return route.fulfill({json:{peers,requested_pages:[],preview:peer?{...peer,base_version:'v1',sequence:1,svg:`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 1100"><text x="100" y="100">${peer.id}</text></svg>`}:null}});
 });
 await page.goto('/tests/fixtures/coedit-live.html');await page.getByRole('button',{name:/writer.*작성 화면 보기/}).click();await expect.poll(()=>watched).toBe('old');
 peers=[];await expect(page.getByLabel('실시간 작성 화면')).toContainText('writer의 재접속을 기다리고 있습니다.');
 peers=[{id:'unrelated',user_id:'other',typing:false,page:1,page_count:1,available:true}];await expect(page.getByRole('button',{name:/other.*작성 화면 보기/})).toBeVisible();expect(watched).toBe('old');
 peers.push({id:'new',user_id:'writer',typing:false,page:1,page_count:1,available:true});await expect.poll(()=>watched).toBe('new');await expect(page.getByLabel('실시간 작성 화면')).toContainText('writer의 새 접속에 다시 연결했습니다.');
 peers=[{id:'tab-a',user_id:'writer',typing:false,page:1,page_count:1,available:true},{id:'tab-b',user_id:'writer',typing:false,page:1,page_count:1,available:true}];
 await expect(page.getByLabel('실시간 작성 화면')).toContainText('같은 계정의 창이 여러 개입니다.');expect(watched).toBe('new');
 await page.getByRole('button',{name:/writer.*작성 화면 보기/}).last().click();await expect.poll(()=>watched).toBe('tab-b');
 await page.getByLabel('실시간 작성 화면').getByRole('button',{name:'닫기',exact:true}).click();peers=[{id:'later',user_id:'writer',typing:false,page:1,page_count:1,available:true}];await expect.poll(()=>watched).toBe('');await expect(page.getByLabel('실시간 작성 화면')).toHaveCount(0);
});
