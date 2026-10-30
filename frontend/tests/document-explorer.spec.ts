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
  await page.goto('/');const locations=page.getByRole('navigation',{name:'문서함 위치'});
  await locations.getByRole('button',{name:'내 문서함',exact:true}).click();await expect(page.getByRole('button',{name:shared.name,exact:true})).toHaveCount(0);
  page.once('dialog',d=>d.accept('이번 주'));await locations.getByRole('button',{name:'새 폴더',exact:true}).click();
  await expect(locations.getByRole('button',{name:'이번 주',exact:true})).toBeVisible();
  await page.getByRole('button',{name:mine.name+' 문서 정보',exact:true}).click();const info=page.getByRole('complementary',{name:'선택한 문서 정보'});
  await info.getByLabel('문서 폴더').selectOption({label:'이번 주'});await expect(info.getByLabel('문서 폴더')).not.toHaveValue('');
  await locations.getByRole('button',{name:'이번 주',exact:true}).click();await expect(page.getByRole('button',{name:mine.name,exact:true})).toBeVisible();
  await page.reload();await page.getByRole('navigation',{name:'문서함 위치'}).getByRole('button',{name:'이번 주',exact:true}).click();await expect(page.getByRole('button',{name:mine.name,exact:true})).toBeVisible();
  page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'폴더 삭제',exact:true}).click();await expect(locations.getByRole('button',{name:'이번 주',exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:mine.name,exact:true})).toBeVisible();
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
  await page.goto('/');const nav=page.getByRole('navigation',{name:'문서함 위치'});
  await expect(nav.getByRole('button',{name:'우리 팀 문서함 기획팀',exact:true})).toBeVisible();
  await expect(nav.getByRole('button',{name:'기획팀',exact:true})).toHaveCount(0);
  await expect(nav.getByRole('button',{name:'운영팀',exact:true})).toBeVisible();
  await nav.getByRole('button',{name:'우리 팀 문서함 기획팀',exact:true}).click();
  await expect(nav.getByRole('button',{name:'우리 팀 문서함 기획팀',exact:true})).toHaveAttribute('aria-current','page');
  await nav.getByRole('button',{name:'공유 문서함 관리'}).click();
  const dialog=page.getByRole('dialog',{name:'팀·부서 공간',exact:true});
  await dialog.getByText('계정 소속 부서·팀 지정',{exact:true}).click();
  await dialog.getByLabel('소속을 지정할 계정 ID').fill('manager');await dialog.getByRole('button',{name:'소속 조회'}).click();
  await expect(dialog.getByLabel('소속 팀',{exact:true})).toHaveValue('plan');
  await dialog.getByLabel('소속 팀',{exact:true}).selectOption('ops');await dialog.getByRole('button',{name:'소속 저장'}).click();
  await expect(dialog.getByRole('status')).toContainText('소속을 저장했습니다');
  expect(changes).toEqual([{department_id:'dep',team_id:'ops',expected_revision:'r1'}]);
  await dialog.getByRole('button',{name:'공간 관리 닫기'}).click();
  await expect(nav.getByRole('button',{name:'우리 팀 문서함 운영팀',exact:true})).toBeVisible();
  await expect(nav.getByRole('button',{name:'기획팀',exact:true})).toBeVisible();
  for(const width of [1440,430]){await page.setViewportSize({width,height:900});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBeTruthy();}
});
