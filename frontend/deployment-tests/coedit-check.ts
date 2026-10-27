import {expect, type Page, type BrowserContext} from "@playwright/test";
import {readFileSync} from "node:fs";
import {randomUUID} from "node:crypto";
import {unzipSync, strFromU8} from "fflate";

/** Runs only inside the existing disposable PostgreSQL/account browser test. */
export async function checkCoedit(page: Page, context: BrowserContext, base: string) {
  const original = readFileSync(new URL("../../samples/paragraph-and-table.hwpx", import.meta.url));
  const name = "Synthetic collaborative document " + randomUUID() + ".hwpx";
  const response = await page.request.post(base + `api/documents?name=${encodeURIComponent(name)}&document_id=${randomUUID()}`, {data:original, headers:{"Idempotency-Key":randomUUID()}});
  expect(response.ok()).toBe(true); const doc = await response.json();
  await page.getByRole("button", {name:"← 문서 목록",exact:true}).click();
  const peer = await context.newPage();
  async function open(target: Page) {
    await target.goto(base);
    await target.getByRole("row").filter({has:target.getByRole("button",{name,exact:true})}).getByRole("button",{name:"공동편집",exact:true}).click();
    await expect(target.getByRole("status")).toHaveText("공동 변경 저장 완료",{timeout:90000});
  }
  async function edit(target: Page, end: boolean, value: string) {
    const frame = await (await target.locator(".coedit-canvas iframe").elementHandle())!.contentFrame();
    await frame!.getByRole("textbox",{name:"문서 편집 입력",exact:true}).focus();
    await target.keyboard.press(end ? "Control+End" : "Control+Home");
    await target.keyboard.insertText(value);
  }
  async function content(target: Page) {
    const frame = await (await target.locator(".coedit-canvas iframe").elementHandle())!.contentFrame();
    const bytes = await frame!.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]);
    return strFromU8(unzipSync(Uint8Array.from(bytes))["Contents/section0.xml"]);
  }
  try {
    await open(page); await open(peer);
    await Promise.all([edit(page,false,"공동입력가나다 "),edit(peer,true," 공동입력라마바")]);
    await expect.poll(() => content(page),{timeout:30000}).toContain("공동입력라마바");
    await expect.poll(() => content(peer),{timeout:30000}).toContain("공동입력가나다");
    expect(await (await page.request.get(base+`api/documents/${doc.id}/versions/${doc.current_version}/file`)).body()).toEqual(original);
    await expect(page.getByRole("alert")).toHaveCount(0); await expect(peer.getByRole("alert")).toHaveCount(0);
    await page.getByRole("button",{name:"저장 후 나가기",exact:true}).click();
    await peer.getByRole("button",{name:"저장 후 나가기",exact:true}).click();
  } finally { await peer.close(); }
}
