import {test,expect} from '@playwright/test';
test('MOCK explorer: folders persist placement independently, private and shared views separate, delete preserves file',async({page})=>{
  let folders:{id:string;name:string;parent_id:string|null}[]=[];
  const mine={id:'my',name:'내 계획.hwpx',current_version:'v1',created_at:1,role:'owner',can_trash:true,personal_document:true,owned_by_me:true,folder_id:null as string|null};
  const shared={...mine,id:'shared',name:'받은 문서.hwpx',role:'reader',can_trash:false,owned_by_me:false,individually_shared:true};
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname,method=route.request().method();const ok=(data:unknown)=>route.fulfill({json:data});
    if(path==='/api/auth/status')return ok({accounts_enabled:true});
    if(path==='/api/auth/me')return ok({user_id:'test2',authentication_required:true,library_access_mode:'shared'});
    if(path==='/api/capabilities')return ok({features:{}});
    if(path==='/api/workspaces')return ok([]);
    if(path==='/api/organization/me')return ok({user_id:'test2',department:null,team:null,revision:null});
    if(path==='/api/documents')return ok([mine,shared]);
    if(path==='/api/folders')return ok(folders);
    if(path.startsWith('/api/folders/')){const id=path.split('/').pop()!;if(method==='DELETE'){folders=folders.filter(f=>f.id!==id);mine.folder_id=null;}else folders=[{id,...route.request().postDataJSON()}];return ok({ok:true});}
    if(path==='/api/documents/my/folder'){mine.folder_id=route.request().postDataJSON().folder_id;return ok({ok:true});}
    return route.fulfill({status:404,json:{detail:'unused in mock'}});
  });
  await page.goto('/');const locations=page.getByRole('navigation',{name:'문서함'});
  await locations.getByRole('button',{name:'내 문서함',exact:true}).click();await expect(page.getByRole('button',{name:shared.name,exact:true})).toHaveCount(0);

  await page.getByRole('button',{name:'새 폴더',exact:true}).click();const newFolder=page.getByRole('dialog',{name:'새 폴더',exact:true});await newFolder.getByLabel('폴더 이름').fill('이번 주');await newFolder.getByRole('button',{name:'만들기',exact:true}).click();await expect(newFolder).toHaveCount(0);
  await expect(page.locator('.library-folder-rows').getByRole('button',{name:'이번 주',exact:true})).toBeVisible();
  await page.getByRole('button',{name:mine.name+' 문서 정보',exact:true}).click();const info=page.getByRole('complementary',{name:'선택한 문서 정보'});
  await info.getByLabel('문서 폴더').selectOption({label:'이번 주'});await expect(info.getByLabel('문서 폴더')).not.toHaveValue('');await expect(page.getByRole('button',{name:mine.name,exact:true})).toHaveCount(0);
  await page.locator('.library-folder-rows').getByRole('button',{name:'이번 주',exact:true}).click();await expect(page.getByRole('button',{name:mine.name,exact:true})).toBeVisible();
  await page.reload();await page.getByRole('button',{name:'내 문서함',exact:true}).click();await page.locator('.library-folder-rows').getByRole('button',{name:'이번 주',exact:true}).click();await expect(page.getByRole('button',{name:mine.name,exact:true})).toBeVisible();
  await page.getByRole('button',{name:'폴더 삭제',exact:true}).click();await page.getByRole('dialog',{name:'폴더 삭제',exact:true}).getByRole('button',{name:'삭제',exact:true}).click();await expect(page.locator('.library-folder-rows').getByRole('button',{name:'이번 주',exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:mine.name,exact:true})).toBeVisible();
  await locations.getByRole('button',{name:'공유받은 문서',exact:true}).click();await expect(page.getByRole('button',{name:mine.name,exact:true})).toHaveCount(0);await page.getByRole('button',{name:shared.name+' 문서 정보',exact:true}).click();await expect(page.getByRole('complementary',{name:'선택한 문서 정보'}).getByRole('button',{name:'공유 권한',exact:true})).toHaveCount(0);
  for(const width of [1440,1024,430]){await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();}
});

test('MOCK affiliation: own team is distinct from other accessible teams; administrator can change affiliation separately',async({page})=>{
  const units=[{id:'dep',name:'사업부',kind:'department',parent_id:null},{id:'plan',name:'기획팀',kind:'team',parent_id:'dep'},{id:'ops',name:'운영팀',kind:'team',parent_id:'dep'}];
  let mine={user_id:'manager',department:{id:'dep',name:'사업부',can_access:true},team:{id:'plan',name:'기획팀',can_access:true},revision:'r1'};
  const changes:unknown[]=[];
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname,method=route.request().method(),ok=(data:unknown)=>route.fulfill({json:data});
    if(path==='/api/auth/status')return ok({accounts_enabled:true});
    if(path==='/api/auth/me')return ok({user_id:'manager',authentication_required:true,is_admin:true});
    if(path==='/api/capabilities')return ok({features:{}});
    if(path==='/api/workspaces')return ok(units.map(u=>({...u,role:'manager'})));
    if(path==='/api/documents'||path==='/api/folders'||path.endsWith('/members'))return ok([]);
    if(path==='/api/organization/me')return ok(mine);
    if(path==='/api/organization/units')return ok(units);
    if(path==='/api/organization/accounts/manager'){
      if(method==='PUT'){const body=route.request().postDataJSON();changes.push(body);mine={...mine,team:{id:'ops',name:'운영팀',can_access:true},revision:'r2'};}
      return ok(mine);
    }
    return route.fulfill({status:404,json:{detail:'unused'}});
  });
  await page.goto('/');const nav=page.getByRole('navigation',{name:'문서함'});
  await expect(nav.getByRole('button',{name:'우리 팀 문서함 기획팀',exact:true})).toBeVisible();
  await page.setViewportSize({width:1250,height:768});
  await expect(page.locator('main').getByRole('navigation',{name:'문서함',exact:true})).toBeVisible();
  await expect(page.locator('.workspace-nav').getByRole('navigation',{name:'문서함',exact:true})).toHaveCount(0);
  await expect(nav.getByRole('button',{name:'우리 부서 문서함 사업부',exact:true})).toBeInViewport();
  await expect(page.getByLabel('현재 접속 계정')).toContainText('기획팀');

  await expect(nav.getByRole('button',{name:'기획팀',exact:true})).toHaveCount(0);
  await nav.getByRole('button',{name:'공유받은 문서',exact:true}).click();await nav.locator('summary').filter({hasText:/^다른 공유 문서함$/}).click();
  await expect(nav.getByRole('button',{name:'운영팀',exact:true})).toBeVisible();
  await nav.getByRole('button',{name:'우리 팀 문서함 기획팀',exact:true}).click();
  await expect(nav.getByRole('button',{name:'우리 팀 문서함 기획팀',exact:true})).toHaveAttribute('aria-current','page');
  await nav.getByRole('button',{name:'공유받은 문서',exact:true}).click();await nav.locator('summary').filter({hasText:/^다른 공유 문서함$/}).click();
  await nav.getByRole('button',{name:'공유 문서함 관리'}).click();
  const dialog=page.getByRole('dialog',{name:'공유 문서함 관리',exact:true});
  await dialog.getByText('계정 소속 부서·팀 지정',{exact:true}).click();
  await dialog.getByLabel('소속을 지정할 계정 ID').fill('manager');await dialog.getByRole('button',{name:'소속 조회'}).click();
  await expect(dialog.getByLabel('소속 팀',{exact:true})).toHaveValue('plan');
  await dialog.getByLabel('소속 팀',{exact:true}).selectOption('ops');await dialog.getByRole('button',{name:'소속 저장'}).click();
  await expect(dialog.getByRole('status')).toContainText('소속을 저장했습니다');
  await expect(page.getByLabel('현재 접속 계정')).toContainText('운영팀');
  expect(changes).toEqual([{department_id:'dep',team_id:'ops',expected_revision:'r1'}]);
  await dialog.getByRole('button',{name:'공간 관리 닫기'}).click();
  await expect(nav.getByRole('button',{name:'우리 팀 문서함 운영팀',exact:true})).toBeVisible();
  await nav.getByRole('button',{name:'공유받은 문서',exact:true}).click();await nav.locator('summary').filter({hasText:/^다른 공유 문서함$/}).click();await expect(nav.getByRole('button',{name:'기획팀',exact:true})).toBeVisible();
  for(const width of [1440,430]){await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();}
});


test('MOCK confidential document keeps a frozen retry, appears in its filter and can be unmarked',async({page})=>{
 const doc={id:'secret',name:'합성 비밀 검수.hwpx',current_version:'v1',created_at:1,role:'owner',personal_document:true,owned_by_me:true,can_set_confidential:true,confidential:false,confidential_revision:null as string|null};
 const calls:any[]=[];
 await page.route('**/api/**',route=>{const path=new URL(route.request().url()).pathname;const ok=(json:unknown)=>route.fulfill({json});
 if(path==='/api/auth/me')return ok({user_id:'test1',authentication_required:true,library_access_mode:'shared'});
 if(path==='/api/capabilities')return ok({features:{}});
 if(path==='/api/organization/me')return ok({department:null,team:null});
 if(path==='/api/documents')return ok([doc]);
 if(path==='/api/workspaces'||path==='/api/folders')return ok([]);
 if(path==='/api/documents/secret/confidential'){const body=route.request().postDataJSON();calls.push(body);if(calls.length===1)return route.abort();doc.confidential=body.enabled;doc.confidential_revision='r'+calls.length;return ok(doc);}
 return route.fulfill({status:404,json:{detail:'unused'}});
 });
 await page.goto('/');await page.locator('#document-more-secret').click();await page.getByRole('button',{name:'비밀 문서로 지정',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'비밀 문서로 지정',exact:true});await expect(dialog).toContainText('직접 초대한 사용자');
 await dialog.getByRole('button',{name:'비밀 문서로 지정',exact:true}).click();await expect(dialog.getByRole('alert')).toBeVisible();await dialog.getByRole('button',{name:'같은 설정 다시 확인',exact:true}).click();await expect(dialog).toHaveCount(0);expect(calls[0]).toEqual(calls[1]);
 await page.getByRole('navigation',{name:'문서함',exact:true}).locator('summary').filter({hasText:/^더보기$/}).click();await page.getByRole('navigation',{name:'문서함',exact:true}).getByRole('button',{name:'비밀 문서',exact:true}).click();await expect(page.getByRole('button',{name:doc.name,exact:true})).toBeVisible();
 await expect(page.locator('.library-document-title').getByRole('img',{name:'비밀 문서'})).toBeVisible();await expect(page.getByText('개인 문서',{exact:true})).toHaveCount(0);await page.locator('#document-more-secret').click();await expect(page.getByRole('button',{name:'저장본 복제',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'부서·팀으로 보내기',exact:true})).toBeDisabled();await page.getByRole('button',{name:'비밀 문서 해제',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'비밀 문서 해제',exact:true}).click();await expect(page.getByRole('button',{name:doc.name,exact:true})).toHaveCount(0);expect(calls[2]).toEqual({enabled:false,expected_revision:'r2',expected_version:'v1'});
});


test('MOCK shared folders use in-page dialog, scope navigation, placement and recent paths',async({page})=>{
 const team={id:'team',name:'기획팀',kind:'team',parent_id:'dep',role:'editor'},dep={id:'dep',name:'사업부',kind:'department',parent_id:null,role:'manager'};
 const folders:any[]=[{id:'private',name:'개인 전용',parent_id:null}];const writes:any[]=[];
 const doc:any={id:'doc',name:'팀 보고.hwpx',current_version:'v1',created_at:1,role:'editor',workspace_id:'team',workspace_name:'기획팀',workspace_folder_id:null,workspace_folder_path:[]};
 await page.addInitScript(()=>{window.prompt=()=>null;});
 await page.route('**/api/**',route=>{const path=new URL(route.request().url()).pathname,ok=(json:unknown)=>route.fulfill({json});
 if(path==='/api/auth/status')return ok({accounts_enabled:true});if(path==='/api/auth/me')return ok({user_id:'writer',authentication_required:true});if(path==='/api/capabilities')return ok({features:{}});
 if(path==='/api/organization/me')return ok({department:{id:'dep',name:'사업부',can_access:true},team:{id:'team',name:'기획팀',can_access:true}});if(path==='/api/workspaces')return ok([dep,team]);if(path==='/api/documents')return ok([doc]);if(path==='/api/folders')return ok(folders);
 if(path.startsWith('/api/workspaces/team/folders/')){const body=route.request().postDataJSON();writes.push(body);if(body.name==='중복')return route.fulfill({status:409,json:{detail:'같은 이름의 폴더가 있습니다.'}});folders.push({id:path.split('/').pop(),workspace_id:'team',revision:'r1',...body});return ok(folders.at(-1));}
 if(path==='/api/documents/doc/workspace-folder'){const body=route.request().postDataJSON();writes.push(body);doc.workspace_folder_id=body.folder_id;doc.workspace_folder_path=['주간 보고'];return ok({ok:true});}
 return route.fulfill({status:404,json:{detail:'unused'}});});
 await page.goto('/');await page.getByRole('button',{name:'우리 팀 문서함 기획팀',exact:true}).click();await expect(page.locator('.library-toolbar').getByRole('button',{name:'새 폴더',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'개인 전용',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'새 폴더',exact:true}).click();const dialog=page.getByRole('dialog',{name:'새 폴더',exact:true});await dialog.getByLabel('폴더 이름').fill('중복');await dialog.getByRole('button',{name:'만들기',exact:true}).click();await expect(dialog.getByRole('alert')).toContainText('같은 이름');await dialog.getByLabel('폴더 이름').fill('주간 보고');await dialog.getByRole('button',{name:'만들기',exact:true}).click();await expect(dialog).toHaveCount(0);
 await page.getByRole('button',{name:'팀 보고.hwpx 문서 정보',exact:true}).click();await page.getByLabel('문서 폴더').selectOption({label:'주간 보고'});await expect(page.getByRole('button',{name:'팀 보고.hwpx',exact:true})).toHaveCount(0);await page.locator('.library-folder-rows').getByRole('button',{name:'주간 보고',exact:true}).click();await expect(page.getByRole('button',{name:'팀 보고.hwpx',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'우리 팀 문서함 기획팀',exact:true})).toHaveAttribute('aria-current','page');
 await page.getByRole('button',{name:'최근 문서',exact:true}).click();await expect(page.locator('.library-document-path')).toHaveText('기획팀 / 주간 보고');await page.getByRole('button',{name:'우리 부서 문서함 사업부',exact:true}).click();await expect(page.getByRole('button',{name:'새 폴더',exact:true})).toBeVisible();await expect(page.locator('.library-folder-rows').getByRole('button',{name:'주간 보고',exact:true})).toHaveCount(0);expect(writes.at(-1)).toEqual({workspace_id:'team',folder_id:folders[1].id,expected_folder_id:null});
});


test('MOCK folder rows stay above documents while name and both dates sort in each direction',async({page})=>{
 const docs=[{id:'a',name:'가 문서.hwpx',created_at:100,modified_at:400},{id:'b',name:'나 문서.hwpx',created_at:200,modified_at:300}].map(d=>({...d,current_version:'v'+d.id,role:'owner',personal_document:true,owned_by_me:true}));
 await page.route('**/api/**',route=>{
  const path=new URL(route.request().url()).pathname;
  const value=path==='/api/auth/status'?{accounts_enabled:true}:path==='/api/auth/me'?{user_id:'test1',authentication_required:true}:path==='/api/capabilities'?{features:{}}:path==='/api/documents'?docs:path==='/api/folders'?[{id:'f',name:'자료',parent_id:null}]:path==='/api/workspaces'?[]:path==='/api/organization/me'?{department:null,team:null}:undefined;
  return value===undefined?route.fulfill({status:404,json:{detail:'unused in mock'}}):route.fulfill({json:value});
 });
 await page.goto('/');await page.getByRole('button',{name:'내 문서함',exact:true}).click();
 const names=page.locator('.library-document-name .link');
 await expect(names).toHaveText(['나 문서.hwpx','가 문서.hwpx']);
 await expect(page.locator('.library-table tbody tr').first()).toHaveClass('library-folder-row');
 for(const [heading,first,second] of [['문서명','가 문서.hwpx','나 문서.hwpx'],['등록일','나 문서.hwpx','가 문서.hwpx'],['수정일','가 문서.hwpx','나 문서.hwpx']]){
  const button=page.getByRole('columnheader').getByRole('button',{name:heading,exact:true});
  await button.click();await expect(names.first()).toHaveText(first);
  await button.click();await expect(names.first()).toHaveText(second);
  await expect(page.locator('.library-table tbody tr').first()).toHaveClass('library-folder-row');
 }
 await expect(page.getByRole('columnheader',{name:'수정일',exact:true})).toHaveAttribute('aria-sort','ascending');
 await page.setViewportSize({width:430,height:900});await expect(page.getByLabel('문서 정렬')).toBeVisible();await page.getByLabel('문서 정렬').selectOption('modified-newest');await expect(names.first()).toHaveText('가 문서.hwpx');await expect(page.locator('.library-mobile-dates').first()).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();
});


test('MOCK all libraries keep folder and document rows full-width when info hides date columns',async({page})=>{
 const spaces=[{id:'dep',name:'사업부',kind:'department',role:'editor'},{id:'team',name:'기획팀',kind:'team',parent_id:'dep',role:'editor'}];
 const docs=[{id:'mine',name:'내 문서.hwpx',personal_document:true,owned_by_me:true},{id:'teamdoc',name:'팀 문서.hwpx',workspace_id:'team'},{id:'depdoc',name:'부서 문서.hwpx',workspace_id:'dep'}].map(d=>({...d,current_version:'v',created_at:1,role:'owner'}));
 const folders=[{id:'f1',name:'내 폴더',parent_id:null},{id:'f2',name:'팀 폴더',parent_id:null,workspace_id:'team'},{id:'f3',name:'부서 폴더',parent_id:null,workspace_id:'dep'}];
 await page.route('**/api/**',route=>{const path=new URL(route.request().url()).pathname;const data=path==='/api/auth/status'?{accounts_enabled:true}:path==='/api/auth/me'?{user_id:'test1',authentication_required:true}:path==='/api/capabilities'?{features:{}}:path==='/api/documents'?docs:path==='/api/folders'?folders:path==='/api/workspaces'?spaces:path==='/api/organization/me'?{department:{id:'dep',name:'사업부',can_access:true},team:{id:'team',name:'기획팀',can_access:true}}:undefined;return data===undefined?route.fulfill({status:404,json:{detail:'unused'}}):route.fulfill({json:data});});
 await page.goto('/');
 for(const [scope,name] of [['내 문서함','내 문서.hwpx'],['우리 팀 문서함 기획팀','팀 문서.hwpx'],['우리 부서 문서함 사업부','부서 문서.hwpx']]){
  await page.getByRole('button',{name:scope,exact:true}).click();await page.getByRole('button',{name:name+' 문서 정보',exact:true}).click();
  await expect(page.locator('.library-table thead th')).toHaveCount(2);await expect(page.locator('.library-folder-row td')).toHaveAttribute('colspan','2');
  const edges=await page.locator('.library-table').evaluate(table=>{const cells=table.querySelector('.library-document-actions').parentElement.querySelectorAll('td');return {end:cells[cells.length-1].getBoundingClientRect().right,tableEnd:table.getBoundingClientRect().right};});expect(Math.abs(edges.end-edges.tableEnd)).toBeLessThan(2);
  await page.getByRole('button',{name:'문서 정보 닫기'}).click();await expect(page.locator('.library-table thead th')).toHaveCount(4);await expect(page.locator('.library-folder-row td')).toHaveAttribute('colspan','4');
 }
 await page.setViewportSize({width:780,height:900});await expect(page.locator('.library-table thead th')).toHaveCount(2);await expect(page.locator('.library-folder-row td')).toHaveAttribute('colspan','2');
});
