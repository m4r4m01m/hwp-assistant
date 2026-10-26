import { expect, type Page } from "@playwright/test";

export async function openConversationTools(page: Page) {
  for (const title of ["문단 수정 후보 기록", "작성 정보·검토 메모"]) {
    const summary = page.getByText(title, { exact: true });
    await expect(summary).toBeVisible();
    if (await summary.locator("xpath=ancestor::details[1]").getAttribute("open") === null) await summary.click();
  }
}

export async function startConversation(page: Page, title: string, options: { openTools?: boolean } = {}) {
  const name = page.getByRole("textbox", { name: "대화 이름", exact: true });
  if (!await name.isVisible()) {
    const create = page.getByRole("button", { name: "새 대화", exact: true });
    // Initial loading can reveal either an empty form or an existing conversation.
    await expect(name.or(create)).toBeVisible();
    if (!await name.isVisible()) await create.click();
  }
  await name.fill(title);
  await page.getByRole("button", { name: "AI와 대화 시작", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "대화 선택", exact: true })).not.toHaveValue("");
  await expect(name).toHaveCount(0);
  if (options.openTools !== false) await openConversationTools(page);
}
