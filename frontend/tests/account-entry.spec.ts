import {test, expect, type Page} from "@playwright/test";
import {createHash} from "node:crypto";

async function entry(page: Page) {
  let authenticated = false;
  const writes: {path:string;body:any}[] = [];
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({status:authenticated ? 200 : 401, json:authenticated ? {user_id:"reviewer",authentication_required:true} : {detail:"login"}});
    if (path.endsWith("/auth/status")) return route.fulfill({json:{authentication_required:true,accounts_enabled:true,registration_enabled:true,guest_enabled:true}});
    if (path.endsWith("/auth/password-login") || path.endsWith("/auth/register")) {
      writes.push({path,body:route.request().postDataJSON()}); authenticated=true;
      return route.fulfill({json:{user_id:"reviewer"}});
    }
    if(path.endsWith("/documents")) return route.fulfill({json:[]});
    if(path.endsWith("/capabilities")) return route.fulfill({json:{features:{}}});
    return route.fulfill({status:404,json:{detail:"unexpected synthetic path"}});
  });
  return writes;
}

test("ACCOUNT UI: guest selection uses password without storing credentials and begins public resource downloads before login", async ({page}) => {
  const writes = await entry(page), assets:string[]=[];
  const bytes=Buffer.from("synthetic wasm bytes"), hash=createHash("sha256").update(bytes).digest("hex");
  await page.route("**/rhwp/build-info.json", route => route.fulfill({json:{publicBase:"/rhwp/",bundle:{"assets/core-abcdefgh.wasm":hash}}}));
  await page.route("**/rhwp/assets/*", route => {assets.push(route.request().url());return route.fulfill({body:bytes,contentType:"application/wasm"});});
  await page.goto("/");
  await expect(page.getByRole("button",{name:/guest로 체험/})).toBeVisible();
  await expect.poll(()=>assets.length).toBe(1);
  await expect(page.locator("iframe")).toHaveCount(0);
  expect(writes).toEqual([]);
  await page.getByRole("button",{name:/guest로 체험/}).click();
  await page.getByLabel("비밀번호",{exact:true}).fill("synthetic-demo-password");
  await page.getByRole("button",{name:"로그인",exact:true}).click();
  await expect(page.getByRole("heading",{name:"HWPX 문서함",exact:true})).toBeVisible();
  expect(writes).toEqual([{path:"/api/auth/password-login",body:{username:"guest",password:"synthetic-demo-password"}}]);
  expect(await page.evaluate(()=>JSON.stringify(localStorage))).not.toContain("password");
});

test("ACCOUNT UI: registration confirmation, private profile cards and failed warmup do not block access", async ({page}) => {
  const writes = await entry(page);
  await page.route("**/rhwp/build-info.json", route=>route.fulfill({status:503,body:"unavailable"}));
  await page.addInitScript(()=>localStorage.setItem("hwp-remembered-usernames-v1", JSON.stringify(["remembered","guest","<img onerror=bad>"])));
  await page.setViewportSize({width:400,height:760});
  await page.goto("/");
  await expect(page.getByRole("button",{name:/remembered/})).toBeVisible();
  await page.getByRole("button",{name:/계정 만들기/}).click();
  await page.getByLabel("아이디",{exact:true}).fill("reviewer");
  await page.getByLabel("비밀번호",{exact:true}).fill("synthetic-password");
  await page.getByLabel("비밀번호 확인",{exact:true}).fill("different-password");
  await page.getByLabel("이름",{exact:true}).fill("합성 검수자");
  await page.getByLabel("사번",{exact:true}).fill("TEST-001");
  await page.getByRole("button",{name:"계정 만들고 시작"}).click();
  await expect(page.getByRole("alert")).toContainText("일치하지 않습니다");
  expect(writes).toEqual([]);
  await page.getByLabel("비밀번호 확인",{exact:true}).fill("synthetic-password");
  await page.getByRole("checkbox").check();
  await page.getByRole("button",{name:"계정 만들고 시작"}).click();
  await expect(page.getByRole("heading",{name:"HWPX 문서함",exact:true})).toBeVisible();
  expect(writes.length).toBe(1);
  expect(writes[0].body).toEqual({username:"reviewer",password:"synthetic-password",name:"합성 검수자",employee_number:"TEST-001"});
  const stored=await page.evaluate(()=>JSON.stringify(localStorage));
  expect(stored).toContain("reviewer");
  expect(stored).not.toMatch(/synthetic-password|TEST-001|합성 검수자/);
});
