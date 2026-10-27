import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { unzipSync, strFromU8 } from "fflate";

test.skip(process.env.HWP_E2E_COEDIT !== "1", "Requires an isolated real API/engine; never runs against user documents.");
async function frame(page: Page) { return (await (await page.locator(".coedit-canvas iframe").elementHandle())!.contentFrame())!; }
async function bytes(page: Page) { return Buffer.from(await (await frame(page)).evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[])); }
async function content(page: Page) { return strFromU8(unzipSync(await bytes(page))["Contents/section0.xml"]); }
async function open(page: Page, name: string) {
  await page.goto("/");
  const row = page.getByRole("row").filter({has: page.getByRole("button", {name, exact: true})});
  await row.getByRole("button", {name: "공동편집", exact: true}).click();
  await expect(page.getByRole("status")).toHaveText("공동 변경 저장 완료", {timeout: 90000});
}
async function type(page: Page, end: boolean, value: string) {
  const editor = await frame(page);
  await editor.getByRole("textbox", {name: "문서 편집 입력", exact: true}).focus();
  await page.keyboard.press(end ? "Control+End" : "Control+Home");
  await page.keyboard.insertText(value);
}
test("REAL rhwp: two native editors merge, converge, save and reopen", async ({page, context, request}, info) => {
  const original = await readFile(new URL("../../samples/paragraph-and-table.hwpx", import.meta.url));
  const name = `합성 공동편집 ${Date.now()}.hwpx`;
  const created = await request.post(`/api/documents?name=${encodeURIComponent(name)}&document_id=${crypto.randomUUID()}`, {data: original, headers:{"Idempotency-Key":crypto.randomUUID()}});
  expect(created.ok()).toBeTruthy(); const doc = await created.json();
  const peer = await context.newPage();
  await open(page, name); await open(peer, name);
  await expect(page.getByLabel("공동편집 참여자")).toContainText("2개 세션");
  await Promise.all([type(page, false, "동시작성가나다 "), type(peer, true, " 동시작성라마바")]);
  await expect.poll(() => content(page), {timeout: 30000}).toContain("동시작성라마바");
  await expect.poll(() => content(peer), {timeout: 30000}).toContain("동시작성가나다");
  await expect(page.getByRole("alert")).toHaveCount(0); await expect(peer.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveText("공동 변경 저장 완료");
  const latest = (await (await request.get("/api/documents")).json()).find((value: any) => value.id === doc.id);
  const saved = await request.get(`/api/documents/${doc.id}/versions/${latest.current_version}/file`);
  const xml = strFromU8(unzipSync(await saved.body())["Contents/section0.xml"]);
  expect(xml).toContain("동시작성가나다"); expect(xml).toContain("동시작성라마바");
  expect(await (await request.get(`/api/documents/${doc.id}/versions/${doc.current_version}/file`)).body()).toEqual(original);
  await page.screenshot({path:info.outputPath("coedit-two-participants.png"), fullPage:true});
  await page.getByRole("button", {name:"저장 후 나가기",exact:true}).click();
  await peer.getByRole("button", {name:"저장 후 나가기",exact:true}).click();
  await open(page, name);
  expect(await content(page)).toContain("동시작성라마바"); expect(await content(page)).toContain("동시작성가나다");
  await page.getByRole("button", {name:"저장 후 나가기",exact:true}).click(); await peer.close();
});

test("REAL rhwp: overlapping native input stops without losing either buffer", async ({page, context, request}, info) => {
  const original = await readFile(new URL("../../samples/paragraph-and-table.hwpx", import.meta.url));
  const name = `합성 충돌 보존 ${Date.now()}.hwpx`;
  const response = await request.post(`/api/documents?name=${encodeURIComponent(name)}&document_id=${crypto.randomUUID()}`, {data:original,headers:{"Idempotency-Key":crypto.randomUUID()}});
  expect(response.ok()).toBeTruthy(); const doc = await response.json(); const peer = await context.newPage();
  await open(page, name); await open(peer, name);
  let count = 0; let release!: () => void; const both = new Promise<void>(resolve => { release = resolve; });
  await context.route("**/coedit/changes?*", async route => { if (++count === 2) release(); await both; await route.continue(); });
  await Promise.all([type(page, false, "첫번째편집보존"), type(peer, false, "두번째편집보존")]);
  await expect.poll(async () => (await page.getByRole("alert").count()) + (await peer.getByRole("alert").count()), {timeout:30000}).toBe(1);
  const loser = await page.getByRole("alert").count() ? page : peer;
  expect(await content(loser)).toContain(loser === page ? "첫번째편집보존" : "두번째편집보존");
  await expect(loser.getByRole("alert")).toContainText("동시에 수정");
  await loser.screenshot({path:info.outputPath("coedit-overlap-preserved.png"),fullPage:true});
  const current = (await (await request.get("/api/documents")).json()).find((value: any) => value.id === doc.id);
  const xml = strFromU8(unzipSync(await (await request.get(`/api/documents/${doc.id}/versions/${current.current_version}/file`)).body())["Contents/section0.xml"]);
  expect(xml).toContain(loser === page ? "두번째편집보존" : "첫번째편집보존");
  await context.unroute("**/coedit/changes?*"); await peer.close();
});

test("REAL rhwp: an exclusive editor leaves the second tab a readable saved snapshot", async ({page, context, request}) => {
  const original = await readFile(new URL("../../samples/paragraph-and-table.hwpx", import.meta.url));
  const name = `합성 읽기 전용 ${Date.now()}.hwpx`;
  const response = await request.post(`/api/documents?name=${encodeURIComponent(name)}&document_id=${crypto.randomUUID()}`, {data:original,headers:{"Idempotency-Key":crypto.randomUUID()}});
  expect(response.ok()).toBeTruthy();
  await page.goto("/"); await page.getByRole("button", {name,exact:true}).click();
  await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료", {timeout:90000});
  const peer = await context.newPage(); await peer.goto("/"); await peer.getByRole("button", {name,exact:true}).click();
  await expect(peer.getByText("읽기 전용 · 저장본", {exact:true})).toBeVisible();
  await expect(peer.locator(".session-viewer-page svg")).toBeVisible({timeout:90000});
  await page.getByRole("button", {name:"← 문서 목록",exact:true}).click();
  await peer.getByRole("button", {name:"편집권 다시 확인",exact:true}).click();
  await expect(peer.getByLabel("현재 문서와 저장 상태").getByRole("status")).toHaveText("저장 완료", {timeout:90000});
  await peer.close();
});
