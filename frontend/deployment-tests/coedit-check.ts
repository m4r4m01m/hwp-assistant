import {expect, type Page, type BrowserContext} from "@playwright/test";
import {readFileSync} from "node:fs";
import {randomUUID} from "node:crypto";
import {unzipSync, strFromU8} from "fflate";

/** Runs only inside the existing disposable PostgreSQL/account browser test. */
export async function checkCoedit(page: Page, context: BrowserContext, base: string, sharedWith: string) {
  const original = readFileSync(new URL("../../samples/paragraph-and-table.hwpx", import.meta.url));
  const name = "Synthetic collaborative document " + randomUUID() + ".hwpx";
  const response = await page.request.post(base + `api/documents?name=${encodeURIComponent(name)}&document_id=${randomUUID()}`, {data:original, headers:{"Idempotency-Key":randomUUID()}});
  expect(response.ok()).toBe(true); const doc = await response.json();
  const grant = await page.request.put(base + `api/documents/${doc.id}/access`, {data:{user_id:sharedWith,role:"editor"}});
  expect(grant.ok()).toBe(true);
  await page.getByRole("button", {name:"← 문서 목록",exact:true}).click();
  const peer = await context.newPage();
  async function open(target: Page) {
    await target.goto(base);
    await target.getByRole("button",{name,exact:true}).click();
    await expect(target.getByRole("status")).toHaveText("미반영 변경 없음",{timeout:90000});
  }
  async function edit(target: Page, end: boolean, value: string) {
    const frame = await (await target.locator(".coedit-canvas iframe").elementHandle())!.contentFrame();
    await frame!.getByRole("textbox",{name:"문서 편집 입력",exact:true}).focus();
    await target.keyboard.press(end ? "Control+End" : "Control+Home");
    await target.keyboard.insertText(value);
    await target.getByRole("button",{name:"변경사항 반영",exact:true}).click();
  }
  async function content(target: Page) {
    const frame = await (await target.locator(".coedit-canvas iframe").elementHandle())!.contentFrame();
    const bytes = await frame!.evaluate(() => Array.from((window as any).hwpAssistantBridge.exportSnapshot().bytes) as number[]);
    return strFromU8(unzipSync(Uint8Array.from(bytes))["Contents/section0.xml"]);
  }
  try {
    await open(page); await open(peer);
    const documents = await (await page.request.get(base + "api/documents")).json();
    const copies = documents.filter((item: {branch_source_id?: string}) => item.branch_source_id === doc.id);
    expect(copies).toHaveLength(0); // Ordinary opens edit the shared source directly.
    const work = doc;
    await Promise.all([edit(page,false,"공동입력가나다 "),edit(peer,true," 공동입력라마바")]);
    await expect.poll(() => content(page),{timeout:30000}).toContain("공동입력라마바");
    await expect.poll(() => content(peer),{timeout:30000}).toContain("공동입력가나다");
    expect(await (await page.request.get(base+`api/documents/${doc.id}/versions/${doc.current_version}/file`)).body()).toEqual(original);
    await expect(page.getByRole("alert")).toHaveCount(0); await expect(peer.getByRole("alert")).toHaveCount(0);
    await page.getByRole("button",{name:"문서함으로",exact:true}).click();
    await peer.getByRole("button",{name:"문서함으로",exact:true}).click();
    const source = await (await page.request.get(base + `api/documents/${doc.id}`)).json();
    expect(source.current_version).not.toBe(doc.current_version);
    const saved = await (await page.request.get(base + `api/documents/${work.id}`)).json();
    const bytes = await (await page.request.get(base + `api/documents/${work.id}/versions/${saved.current_version}/file`)).body();
    const xml = strFromU8(unzipSync(bytes)["Contents/section0.xml"]);
    expect(xml).toContain("공동입력가나다"); expect(xml).toContain("공동입력라마바");

    // Leaving dirty coedit must finish its cleanup before the advanced editor starts.
    // Exercise both destinations with the real engine and preserve the source history.
    const versionsBefore = await (await page.request.get(base + `api/documents/${doc.id}/versions`)).json();
    for (const mode of ["discard", "preserve"] as const) {
      await open(page);
      const frame = await (await page.locator(".coedit-canvas iframe").elementHandle())!.contentFrame();
      await frame!.getByRole("textbox", {name:"문서 편집 입력",exact:true}).focus();
      await page.keyboard.press("Control+End");
      const marker = mode === "discard" ? " 버릴미반영변경" : " 보관할미반영변경";
      await page.keyboard.insertText(marker);
      await expect(page.getByRole("status")).toHaveText("미반영 변경 있음");
      await expect(page.getByRole("button", {name:"AI·서식 도구",exact:true})).toBeEnabled();
      await page.getByRole("button", {name:"AI·서식 도구",exact:true}).click();
      const leaving = page.getByRole("dialog", {name:"미반영 변경 처리",exact:true});
      if (mode === "discard") {
        await leaving.getByRole("button", {name:"변경을 버리고 이동",exact:true}).click();
        await page.getByRole("dialog", {name:"내 편집 버리기 확인",exact:true})
          .getByRole("button", {name:"내 편집 버리고 최신본 열기",exact:true}).click();
      } else {
        await leaving.getByRole("button", {name:"내 작업본으로 보관 후 이동",exact:true}).click();
      }
      await expect(page.getByLabel("현재 문서와 저장 상태").getByRole("status"))
        .toHaveText("저장 완료", {timeout:90000});
      expect(await (await page.request.get(base + `api/documents/${doc.id}/versions`)).json()).toEqual(versionsBefore);
      expect(await (await page.request.get(base + `api/documents/${doc.id}/versions/${saved.current_version}/file`)).body()).toEqual(bytes);
      const all = await (await page.request.get(base + "api/documents")).json();
      const revisions = all.filter((item: {branch_source_id?: string}) => item.branch_source_id === doc.id);
      expect(revisions).toHaveLength(mode === "preserve" ? 1 : 0);
      if (mode === "preserve") {
        const revision = revisions[0];
        const preserved = await (await page.request.get(base + `api/documents/${revision.id}/versions/${revision.current_version}/file`)).body();
        expect(strFromU8(unzipSync(preserved)["Contents/section0.xml"])).toContain(marker.trim());
      }
      await page.getByRole("button", {name:"← 문서 목록",exact:true}).click();
    }
  } finally { await peer.close(); }
}

