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
