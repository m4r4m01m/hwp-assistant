import {test,expect} from '@playwright/test';
test('MOCK automatic entry keeps open request identity after a lost response',async({page})=>{
  const calls:unknown[]=[];
  await page.route('**/api/documents/source/edit-target',route=>{calls.push(route.request().postDataJSON());return calls.length===1?route.abort():route.fulfill({json:{mode:'workcopy',document:{id:'mine'}}});});
  await page.goto('/tests/fixtures/document-edit-entry.html');
  await expect(page.getByRole('alert')).toBeVisible();await page.getByRole('button',{name:'같은 열기 요청 다시 확인'}).click();
  await expect(page.getByLabel('편집 대상')).toHaveText('workcopy:mine');expect(calls).toHaveLength(2);expect(calls[0]).toEqual(calls[1]);
});
for(const mode of ['direct','view','coedit'])test(`MOCK automatic entry retains ${mode} routing`,async({page})=>{
  await page.route('**/api/documents/source/edit-target',route=>route.fulfill({json:{mode,document:{id:'source'}}}));
  await page.goto('/tests/fixtures/document-edit-entry.html');await expect(page.getByLabel('편집 대상')).toHaveText(mode+':source');
});
