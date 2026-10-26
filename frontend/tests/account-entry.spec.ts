import {test, expect, type Page} from "@playwright/test";
import {createHash} from "node:crypto";

test.use({ trace: "off", video: "off", screenshot: "off" });

async function entry(page: Page, shared = false) {
  let authenticated = false;
  const writes: {path:string;body:any}[] = [];
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({status:authenticated ? 200 : 401, json:authenticated ? {user_id:"reviewer",authentication_required:true} : {detail:"login"}});
    if (path.endsWith("/auth/status")) return route.fulfill({json:{authentication_required:true,accounts_enabled:true,registration_enabled:true,guest_enabled:true,library_access_mode:shared ? "shared" : "private",token_login_enabled:!shared}});
    if (path.endsWith("/auth/register")) {
      writes.push({path,body:route.request().postDataJSON()});
      return route.fulfill({status:202,json:{status:"pending",user_id:"reviewer"}});
    }
    if (path.endsWith("/auth/password-login")) {
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
  const bytes=Buffer.from([0,97,115,109,1,0,0,0]), hash=createHash("sha256").update(bytes).digest("hex");
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

test("ACCOUNT UI: registration confirmation submits a pending request without signing in or storing applicant data", async ({page}) => {
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
  await page.getByRole("button",{name:"가입 승인 요청"}).click();
  await expect(page.getByRole("alert")).toContainText("일치하지 않습니다");
  expect(writes).toEqual([]);
  await page.getByLabel("비밀번호 확인",{exact:true}).fill("synthetic-password");
  await page.getByRole("button",{name:"가입 승인 요청"}).click();
  await expect(page.getByRole("heading",{name:"가입 승인을 기다리고 있습니다",exact:true})).toBeVisible();
  await expect(page.getByRole("heading",{name:"HWPX 문서함",exact:true})).toHaveCount(0);
  await expect(page.getByLabel("비밀번호",{exact:true})).toHaveCount(0);
  expect(writes.length).toBe(1);
  expect(writes[0].body).toEqual({username:"reviewer",password:"synthetic-password",name:"합성 검수자",employee_number:"TEST-001"});
  const stored=await page.evaluate(()=>JSON.stringify(localStorage));
  expect(stored).not.toMatch(/reviewer|synthetic-password|TEST-001|합성 검수자/);
  await page.getByRole("button",{name:"로그인 화면으로",exact:true}).click();
  await expect(page.getByLabel("아이디",{exact:true})).toHaveValue("reviewer");
  await expect(page.getByLabel("비밀번호",{exact:true})).toHaveValue("");
});

test("ACCOUNT UI: pending WASM compilation stays distinct and never blocks password login", async ({ page }) => {
  const writes = await entry(page), bytes = Buffer.from([0,97,115,109,1,0,0,0]);
  const hash = createHash("sha256").update(bytes).digest("hex");
  await page.addInitScript(() => {
    const state = window as typeof window & { releaseWarmupCompile?: () => void; warmupCompileCalls?: number };
    const compile = WebAssembly.compile.bind(WebAssembly);
    state.warmupCompileCalls = 0;
    const gate = new Promise<void>(resolve => { state.releaseWarmupCompile = resolve; });
    WebAssembly.compile = async bytes => { state.warmupCompileCalls!++; await gate; return compile(bytes); };
  });
  await page.route("**/rhwp/build-info.json", route => route.fulfill({ json: { publicBase: "/rhwp/", bundle: { "assets/core-abcdefgh.wasm": hash } } }));
  await page.route("**/rhwp/assets/*", route => route.fulfill({ body: bytes, contentType: "application/wasm" }));
  await page.goto("/");
  await expect(page.locator(".engine-warmup")).toContainText("WASM을 컴파일합니다");
  await expect(page.locator(".engine-warmup")).not.toContainText("WASM 컴파일 완료");
  await expect(page.locator("iframe")).toHaveCount(0);
  expect(writes).toEqual([]);
  await page.getByRole("button", { name: /guest로 체험/ }).click();
  await page.getByLabel("비밀번호", { exact: true }).fill("synthetic-demo-password");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
  expect(writes).toEqual([{ path: "/api/auth/password-login", body: { username: "guest", password: "synthetic-demo-password" } }]);
  expect(await page.evaluate(() => (window as any).warmupCompileCalls)).toBe(1);
  await page.evaluate(() => (window as any).releaseWarmupCompile());
  await expect(page.locator("iframe")).toHaveCount(0);
});

for (const selected of ["sample-a", "sample-b"]) {
  test(`ACCOUNT UI: explicitly configured ${selected} requires its own password and stays distinct from remembered IDs`, async ({ page }) => {
    let authenticated = "";
    const writes: { username: string; password: string }[] = [];
    await page.route("**/api/**", route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/auth/me")) return route.fulfill({ status: authenticated ? 200 : 401,
        json: authenticated ? { user_id: authenticated, authentication_required: true } : { detail: "login" } });
      if (path.endsWith("/auth/status")) return route.fulfill({ json: { authentication_required: true,
        accounts_enabled: true, registration_enabled: false, guest_enabled: false,
        sample_accounts: [{ user_id: "sample-a", label: "샘플 A" }, { user_id: "sample-b", label: "샘플 B" }] } });
      if (path.endsWith("/auth/password-login")) {
        const input = route.request().postDataJSON(); writes.push(input); authenticated = input.username;
        return route.fulfill({ json: { user_id: authenticated } });
      }
      if (path.endsWith("/documents")) return route.fulfill({ json: [] });
      if (path.endsWith("/capabilities")) return route.fulfill({ json: { features: {} } });
      return route.fulfill({ status: 404, json: { detail: "unexpected synthetic path" } });
    });
    await page.route("**/rhwp/build-info.json", route => route.fulfill({ status: 503, body: "offline fixture" }));
    await page.addInitScript(() => localStorage.setItem("hwp-remembered-usernames-v1", JSON.stringify(["sample-a", "personal-demo"])));
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "샘플 계정 선택", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /sample-a/ })).toHaveCount(1);
    await expect(page.getByRole("button", { name: /personal-demo/ })).toBeVisible();
    expect(writes).toEqual([]);
    const other = selected === "sample-a" ? "sample-b" : "sample-a";
    await page.getByRole("button", { name: new RegExp(other) }).click();
    await page.getByLabel("비밀번호", { exact: true }).fill("discarded-synthetic-password");
    await page.getByRole("button", { name: "계정 선택으로", exact: true }).click();
    await page.getByRole("button", { name: new RegExp(selected) }).click();
    await expect(page.getByLabel("아이디", { exact: true })).toHaveValue(selected);
    await expect(page.getByLabel("비밀번호", { exact: true })).toHaveValue("");
    await page.getByRole("button", { name: "로그인", exact: true }).click();
    expect(writes).toEqual([]);
    await page.getByLabel("비밀번호", { exact: true }).fill(`synthetic-${selected}-password`);
    await page.getByRole("button", { name: "로그인", exact: true }).click();
    await expect(page.getByRole("heading", { name: "HWPX 문서함", exact: true })).toBeVisible();
    expect(writes).toEqual([{ username: selected, password: `synthetic-${selected}-password` }]);
    expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain("password");
  });
}


test("ACCOUNT UI: shared library policy explains common documents while retaining per-account attribution", async ({ page }) => {
  await entry(page, true);
  await page.route("**/rhwp/build-info.json", route => route.fulfill({ status: 503, body: "offline fixture" }));
  await page.goto("/");
  await expect(page.getByRole("button", { name: /내 계정으로 로그인/ })).toContainText("같은 공용 문서함에서 작업");
  await expect(page.getByText(/모든 계정이 같은 공용 문서함을 사용합니다/)).toBeVisible();
  await expect(page.getByText("개인 문서와 공유받은 문서", { exact: true })).toHaveCount(0);
  await expect(page.getByText("기존 인증 토큰으로 로그인", { exact: true })).toHaveCount(0);
});
