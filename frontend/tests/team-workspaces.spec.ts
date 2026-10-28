import { test, expect, type Page } from "@playwright/test";
const doc = { id: "doc1", name: "합성 팀 보고서.hwpx", current_version: "v1", created_at: 1, role: "owner", can_trash: true };
const space = { id: "team1", name: "보안팀", kind: "team", parent_id: null, role: "manager" };
async function setup(page: Page) {
  const changes: unknown[] = [];
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    const ok = (data: unknown, status = 200) => route.fulfill({ status, json: data });
    if (path === "/api/auth/status") return ok({ accounts_enabled: true });
    if (path === "/api/auth/me") return ok({ user_id: "manager", authentication_required: true, is_admin: true });
    if (path === "/api/capabilities") return ok({ features: {} });
    if (path === "/api/documents") return ok([doc]);
    if (path === "/api/workspaces") return ok([space]);
    if (path === "/api/workspaces/team1/members") return ok([{ user_id: "manager", role: "manager" }]);
    if (path === "/api/workspaces/team1/members/manager") return ok({ detail: "마지막 활성 공간 관리자는 제거하거나 권한을 낮출 수 없습니다." }, 409);
    if (path === "/api/documents/doc1/workspace") { changes.push(route.request().postDataJSON()); return ok({ ok: true, workspace_id: "team1" }); }
    return ok({ detail: "Unexpected synthetic route" }, 404);
  });
  await page.route("**/rhwp/**", route => route.fulfill({ status: 503, body: "synthetic" }));
  await page.goto("/");
  return changes;
}
test("MOCK spaces: manager cannot remove the last manager; accessible dialog fits mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await setup(page);
  await page.getByRole("button", { name: "팀·부서 공간 관리" }).click();
  const dialog = page.getByRole("dialog", { name: "팀·부서 공간", exact: true });
  await expect(dialog).toContainText("하위 팀 권한은 자동으로 생기지 않습니다");
  await dialog.getByLabel("manager 권한").selectOption("reader");
  await expect(dialog.getByRole("alert")).toContainText("마지막 활성 공간 관리자");
  await expect(dialog.getByLabel("manager 권한")).toHaveValue("manager");
  expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy();
});
test("MOCK spaces: explicit document assignment sends displayed saved-version fence", async ({ page }) => {
  const changes = await setup(page);
  await page.getByRole("button", { name: "합성 팀 보고서.hwpx 더보기" }).click();
  await page.getByRole("button", { name: "공간 지정", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "문서 공간 지정" });
  await expect(dialog).toContainText("기존 개별 공유 권한은 공간 안에서는 적용되지 않습니다");
  page.once("dialog", d => d.accept());
  await dialog.getByRole("button", { name: "이 공간에 문서 배치" }).click();
  await expect(dialog).toHaveCount(0);
  expect(changes).toEqual([{ workspace_id: "team1", expected_version: "v1" }]);
});
