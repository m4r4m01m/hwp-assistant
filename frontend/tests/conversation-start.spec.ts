import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { openConversationTools, startConversation } from "./conversation-helpers";

async function openDocument(page: Page) {
  // Embedded browsers may reject native dialogs instead of displaying them.
  await page.addInitScript(() => { window.prompt = () => { throw new Error("prompt() is not supported."); }; });
  const source = await readFile(new URL("../../samples/paragraph-and-table.hwpx", import.meta.url));
  const name = `인라인 대화 검증 ${Date.now()}.hwpx`;
  await page.goto("/");
  await page.getByLabel("HWPX 업로드", { exact: true }).setInputFiles({ name, mimeType: "application/hwp+zip", buffer: source });
  const row = page.getByRole("row").filter({ has: page.getByRole("button", { name, exact: true }) });
  const file = (await row.getByRole("link", { name: "다운로드", exact: true }).getAttribute("href"))!;
  await row.getByRole("button", { name, exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("저장 완료", { timeout: 90000 });
  await page.getByRole("button", { name: "문서 AI 대화 열기", exact: true }).click();
  return { source, file, path: file.split("/versions/")[0] };
}

test("CONVERSATION real rhwp: inline creation works without native prompt and reopens latest conversation", async ({ page, request }) => {
  const state = await openDocument(page);
  await startConversation(page, "첫 검토", { openTools: false });
  await startConversation(page, "최신 검토", { openTools: false });
  const selected = await page.getByRole("combobox", { name: "대화 선택", exact: true }).inputValue();
  await page.getByRole("button", { name: "문서 AI 대화 닫기", exact: true }).click();
  await page.getByRole("button", { name: "문서 AI 대화 열기", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "대화 선택", exact: true })).toHaveValue(selected);
  await page.getByRole("button", { name: "새 대화", exact: true }).click();
  await page.getByRole("textbox", { name: "대화 이름", exact: true }).fill("버리면 안 되는 새 이름");
  const rows = await (await request.get(state.path + "/threads")).json();
  await page.getByRole("combobox", { name: "대화 선택", exact: true }).selectOption(rows[0].id);
  await expect(page.getByRole("group", { name: "대화 전환 확인", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "현재 대화 유지", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "대화 이름", exact: true })).toHaveValue("버리면 안 되는 새 이름");
  await expect(page.getByRole("combobox", { name: "대화 선택", exact: true })).toHaveValue(selected);
  expect(await (await request.get(state.file)).body()).toEqual(state.source);
  expect(await (await request.get(state.path + "/versions")).json()).toHaveLength(1);
});

test("CONVERSATION real rhwp: lost creation response reuses frozen ID and failed list refresh keeps new conversation open", async ({ page, request }) => {
  const state = await openDocument(page);
  const posted: unknown[] = [];
  let failList = false;
  await page.route("**/api/documents/*/threads", async route => {
    if (route.request().method() === "POST") {
      posted.push(route.request().postDataJSON());
      const result = await route.fetch();
      expect(result.ok()).toBeTruthy();
      if (posted.length === 1) return route.abort("failed");
      failList = true;
      return route.fulfill({ response: result });
    }
    if (failList) return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "합성 목록 실패" }) });
    return route.continue();
  });
  await page.getByRole("textbox", { name: "대화 이름", exact: true }).fill("응답 유실 검증");
  await page.getByRole("button", { name: "AI와 대화 시작", exact: true }).click();
  const retry = page.getByRole("button", { name: "대화 생성 결과 재확인", exact: true });
  await expect(retry).toBeEnabled();
  await expect(page.getByRole("textbox", { name: "대화 이름", exact: true })).toBeDisabled();
  await retry.click();
  await expect(page.getByRole("textbox", { name: "대화 이름", exact: true })).toHaveCount(0);
  expect(posted).toHaveLength(2);
  expect(posted[1]).toEqual(posted[0]);
  const rows = await (await request.get(state.path + "/threads")).json();
  expect(rows).toHaveLength(1);
  await expect(page.getByRole("combobox", { name: "대화 선택", exact: true })).toHaveValue(rows[0].id);
  await expect(page.getByRole("alert").filter({ hasText: "대화를 만들었습니다" })).toBeVisible();
  await openConversationTools(page);
  await expect(page.getByRole("textbox", { name: "대화 메시지", exact: true })).toBeEditable();
  expect(await (await request.get(state.file)).body()).toEqual(state.source);
});
