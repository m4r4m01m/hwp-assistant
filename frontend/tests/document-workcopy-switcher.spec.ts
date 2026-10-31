import {test,expect} from '@playwright/test';
const source={id:'source',name:'합성 원본',current_version:'s1',created_at:1,role:'editor'};
const copy=(id:string,can_edit=true)=>({document_id:id,source_document_id:'source',name:id,owner_id:'author',created_at:1,current_version:can_edit?'v1':null,can_edit,can_manage_participants:id==='current'});
test('MOCK workcopy selector filters review-only branches and leaves selection unchanged until navigation succeeds',async({page})=>{
 await page.route('**/api/documents/current/branches',route=>route.fulfill({json:{source_document:source,branches:[copy('current'),copy('invited'),copy('review-only',false)]}}));
 await page.goto('/tests/fixtures/document-workcopy-switcher.html');const select=page.getByRole('combobox',{name:'작업본 선택'});
 await expect(select.locator('option')).toHaveCount(3);await expect(select.locator('option[value="review-only"]')).toHaveCount(0);
 await expect(select).toHaveValue('current');await select.selectOption('current');await expect(page.getByLabel('전환 횟수')).toHaveText('0');
 await select.selectOption('invited');await expect(page.getByLabel('전환 대상')).toHaveText('invited');await expect(select).toHaveValue('current');
 await select.selectOption('source');await expect(page.getByLabel('전환 대상')).toHaveText('source');
});
test('MOCK workcopy selector retains current branch beyond history limit and refreshes newly available branches',async({page})=>{
 let branches=[copy('other')];await page.route('**/api/documents/current/branches',route=>route.fulfill({json:{source_document:source,branches,graph:{truncated:true}}}));
 await page.goto('/tests/fixtures/document-workcopy-switcher.html');const select=page.getByRole('combobox',{name:'작업본 선택'});
 await expect(select.locator('option[value="other"]')).toHaveCount(1);await expect(select).toHaveValue('current');
 branches=[copy('new')];await select.focus();await expect(select.locator('option[value="new"]')).toHaveCount(1);await expect(select).toHaveValue('current');
});
test('MOCK workcopy selector clears unavailable choices on permission failure and allows retry',async({page})=>{
 let fail=false;await page.route('**/api/documents/current/branches',route=>fail?route.fulfill({status:403,json:{detail:'접근 권한 변경'}}):route.fulfill({json:{source_document:{...source,role:'reader'},branches:[copy('current')]}}));
 await page.goto('/tests/fixtures/document-workcopy-switcher.html');const select=page.getByRole('combobox',{name:'작업본 선택'});
 await expect(select.locator('option[value="source"]')).toHaveText('공동 원본 · 읽기 전용');fail=true;await select.focus();
 await expect(page.getByRole('button',{name:'작업본 목록 다시 불러오기'})).toBeVisible();await expect(select.locator('option')).toHaveCount(1);
 fail=false;await page.getByRole('button',{name:'작업본 목록 다시 불러오기'}).click();await expect(select.locator('option[value="source"]')).toHaveCount(1);
});
