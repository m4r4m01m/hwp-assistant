import {test,expect} from '@playwright/test';
test('live focus zooms out as changed bounds grow without resizing its panel; unchanged text is not painted',async({page})=>{
 await page.goto('/tests/fixtures/coedit-focus.html');const svg=page.locator('.coedit-focus-paper svg');
 const width=async()=>Number((await svg.getAttribute('viewBox'))!.split(' ')[2]);
 await expect.poll(width).toBe(320);const panel=(await page.locator('.coedit-focus-paper').boundingBox())!.width;
 await expect(svg.locator('[data-live-mark] rect')).toHaveCount(1);
 await page.getByRole('button',{name:'범위 넓히기'}).click();await expect.poll(width).toBeGreaterThan(450);
 expect((await page.locator('.coedit-focus-paper').boundingBox())!.width).toBe(panel);
 await expect(svg.locator('[data-live-mark] rect')).toHaveCount(2);
 await page.getByRole('button',{name:'삭제 표시'}).click();await expect(page.locator('.coedit-removed del')).toHaveText('기준');
 await page.getByRole('button',{name:'전체 전환'}).click();await expect(svg).toHaveAttribute('viewBox','0 0 800 1100');
});

test('wheel and drag retain a manual viewport across new frames, and page-edge drag requests another page',async({page})=>{
 await page.goto('/tests/fixtures/coedit-focus.html');const host=page.getByLabel('작성 화면 이동 및 확대'),svg=host.locator('svg');
 const box=async()=>(await svg.getAttribute('viewBox'))!.split(' ').map(Number);
 await host.hover();const scroll=await page.evaluate(()=>scrollY);await page.mouse.wheel(0,120);
 await expect.poll(async()=>(await box())[2]).toBeGreaterThan(320);expect(await page.evaluate(()=>scrollY)).toBe(scroll);
 const before=await box(),rect=(await host.boundingBox())!;
 await page.mouse.move(rect.x+rect.width/2,rect.y+rect.height/2);await page.mouse.down();await page.mouse.move(rect.x+rect.width/2-35,rect.y+rect.height/2-30,{steps:5});await page.mouse.up();
 const moved=await box();expect(moved[0]).toBeGreaterThan(before[0]);
 await page.getByRole('button',{name:'범위 넓히기'}).click();expect(await box()).toEqual(moved);
 await page.getByRole('button',{name:'전체 전환'}).click();await expect(svg).toHaveAttribute('viewBox','0 0 800 1100');
 await page.mouse.move(rect.x+rect.width/2,rect.y+230);await page.mouse.down();await page.mouse.move(rect.x+rect.width/2,rect.y+60,{steps:5});await page.mouse.up();
 await expect(page.getByTestId('page-step')).toHaveText('1');
});

test('zoom keeps following at the chosen scale on a new page, while dragging pauses following',async({page})=>{
 await page.goto('/tests/fixtures/coedit-focus.html');const host=page.getByLabel('작성 화면 이동 및 확대');
 const box=async()=>(await host.locator('svg').getAttribute('viewBox'))!.split(' ').map(Number);
 await host.hover();await page.mouse.wheel(0,120);await expect.poll(async()=>(await box())[2]).toBeGreaterThan(320);
 const zoomed=await box();await expect(page.getByTestId('following')).toHaveText('따라가기');
 await page.getByRole('button',{name:'작성 쪽 변경',exact:true}).click();
 await expect.poll(async()=>(await box())[1]).toBeGreaterThan(zoomed[1]+300);
 expect((await box())[2]).toBeCloseTo(zoomed[2],4);expect((await box())[3]).toBeCloseTo(zoomed[3],4);
 await page.getByRole('button',{name:'미리보기 확대',exact:true}).click();await expect(page.getByTestId('following')).toHaveText('따라가기');
 const rect=(await host.boundingBox())!;await page.mouse.move(rect.x+150,rect.y+150);await page.mouse.down();await page.mouse.move(rect.x+120,rect.y+120,{steps:3});await page.mouse.up();
 await expect(page.getByTestId('following')).toHaveText('고정');
});
