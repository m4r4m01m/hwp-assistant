import { test, expect } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { unzipSync, strFromU8 } from "fflate";

test("REAL rhwp: saved document copy recovers a lost response, sorts, edits independently and preserves source", async ({ page, request }, info) => {
  const original = await readFile(new URL("../../samples/paragraph-and-table.hwpx", import.meta.url));
  const prefix = `복제 검증 ${Date.now()}`;
  const sourceName = `${prefix} 나.hwpx`, copyName = `${prefix} 가.hwpx`;
  await page.goto("/");
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({ name: sourceName, mimeType: "application/hwp+zip", buffer: original });
  const sourceRow = page.getByRole("row").filter({ has: page.getByRole("button", { name: sourceName, exact: true }) });
  const sourceUrl = (await sourceRow.getByRole("link", { name: "다운로드", exact: true }).getAttribute("href"))!;
  const sourcePath = sourceUrl.split("/versions/")[0];
  const sent: any[] = [];
  await page.route(`**${sourcePath}/copies`, async route => {
    sent.push(route.request().postDataJSON());
    if (sent.length === 1) { expect((await route.fetch()).ok()).toBeTruthy(); return route.abort("failed"); }
    return route.continue();
  });
  try {
    page.once("dialog", d => d.accept(copyName));
    await sourceRow.getByRole("button", { name: "저장본 복제", exact: true }).click();
    await expect(page.getByRole("button", { name: "같은 사본 생성 결과 재확인", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "같은 사본 생성 결과 재확인", exact: true }).click();
    await expect(page.getByRole("button", { name: "사본 열기", exact: true })).toBeVisible();
    expect(sent).toHaveLength(2); expect(sent[1]).toEqual(sent[0]);
    await page.getByLabel("파일명 검색", { exact: true }).fill(prefix);
    await expect(page.locator("tbody tr")).toHaveCount(2);
    const copyRow = page.getByRole("row").filter({ has: page.getByRole("button", { name: copyName, exact: true }) });
    const copyUrl = (await copyRow.getByRole("link", { name: "다운로드", exact: true }).getAttribute("href"))!;
    expect(copyUrl).not.toBe(sourceUrl);
    expect(await (await request.get(copyUrl)).body()).toEqual(original);
    await page.getByLabel("문서 정렬", { exact: true }).selectOption("oldest");
    await expect(page.locator("tbody tr").first().locator("td").first()).toHaveText(sourceName);
    await page.getByLabel("문서 정렬", { exact: true }).selectOption("name");
    await expect(page.locator("tbody tr").first().locator("td").first()).toHaveText(copyName);
    await page.screenshot({ path: info.outputPath("document-copy.png"), fullPage: true });
    await page.getByRole("button", { name: "사본 열기", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    const frame = page.frames().find(f => f.url().includes("/rhwp/"))!;
    await frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
    await page.keyboard.press("Control+End");
    await page.keyboard.insertText(" 사본에서만 수정");
    await page.getByRole("button", { name: "저장", exact: true }).click();
    await expect(page.getByRole("status")).toHaveText("저장 완료");
    const latestUrl = (await page.getByRole("link", { name: "저장본 다운로드" }).getAttribute("href"))!;
    expect(strFromU8(unzipSync(await (await request.get(latestUrl)).body())["Contents/section0.xml"])).toContain("사본에서만 수정");
    expect(await (await request.get(sourceUrl)).body()).toEqual(original);
    expect(await (await request.get(copyUrl)).body()).toEqual(original);
    expect(await (await request.get(sourcePath + "/versions")).json()).toHaveLength(1);
    expect(await (await request.get(copyUrl.split("/versions/")[0] + "/versions")).json()).toHaveLength(2);
    await writeFile(info.outputPath("copy-proof.json"), JSON.stringify({ engine: "real rhwp", actual_model_calls: 0,
      retry_same_body: true, copies_created: 1, source_versions: 1, copy_versions: 2,
      original_sha256: createHash("sha256").update(original).digest("hex"), original_preserved: true }, null, 2));
  } finally { await page.unrouteAll({ behavior: "wait" }); }
});
