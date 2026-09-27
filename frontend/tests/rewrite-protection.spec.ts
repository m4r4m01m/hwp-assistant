import { test, expect } from "@playwright/test";

test("REAL installed rhwp adapter + FIXED text: persisted key order accepted, changed preimage blocked, replay does not duplicate", async ({ page }) => {
  await page.goto("/");
  // Test harness mounts the same product adapter/SDK. Fixed text, no model call.
  await page.evaluate(async () => {
    const modulePath = "/src/editor.ts";
    const { EditorAdapter } = await import(/* @vite-ignore */ modulePath);
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;inset:0;z-index:9999;background:white";
    document.body.append(host);
    const adapter = await EditorAdapter.mount(host);
    await adapter.bridge.newDocument();
    (window as any).rewriteTest = { adapter };
  });
  const frame = page.frames().find(f => f.url().includes("/rhwp/"))!;
  await frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("합성 미저장 원문");
  await page.evaluate(() => {
    const t = (window as any).rewriteTest;
    t.before = t.adapter.selection();
  });
  await page.keyboard.insertText(" 후속 변경");
  const conflict = await page.evaluate(async () => {
    const t = (window as any).rewriteTest;
    try { await t.adapter.apply(t.before, "고정 후보", crypto.randomUUID()); return "unexpected success"; }
    catch (e) { return String(e); }
  });
  expect(conflict).toContain("다시 검토");
  expect(await frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toBe("합성 미저장 원문 후속 변경");
  const replay = await page.evaluate(async () => {
    const t = (window as any).rewriteTest;
    t.fresh = t.adapter.selection(); t.command = crypto.randomUUID();
    // Match the backend's json.dumps(sort_keys=True), including nested target keys.
    const persisted = JSON.parse(JSON.stringify(t.fresh, (_key, value) =>
      value && typeof value === "object" && !Array.isArray(value)
        ? Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]])) : value));
    if (JSON.stringify(persisted.selection) === JSON.stringify(t.fresh.selection))
      throw Error("Test must exercise different property order");
    const wrongTarget = structuredClone(persisted);
    wrongTarget.selection.target.paragraph++;
    let targetConflict = "";
    try { await t.adapter.apply(wrongTarget, "고정 후보", crypto.randomUUID()); }
    catch (e) { targetConflict = String(e); }
    t.first = await t.adapter.apply(persisted, "고정 후보", t.command);
    const revision = t.adapter.selection().revision;
    const second = await t.adapter.apply(t.fresh, "고정 후보", t.command);
    let bindingConflict = "";
    try { await t.adapter.apply(t.fresh, "다른 후보", t.command); }
    catch (e) { bindingConflict = String(e); }
    return { first: t.first, second, revision, after: t.adapter.selection(), targetConflict, bindingConflict };
  });
  expect(replay.targetConflict).toContain("다시 검토");
  expect(replay.bindingConflict).toContain("같은 적용 ID의 내용이 다릅니다.");
  expect(replay.second).toEqual(replay.first);
  expect(replay.after.revision).toBe(replay.revision);
  expect(replay.after.evidence.text).toBe("고정 후보");
  await page.keyboard.insertText(" 이후 입력");
  const lateReplay = await page.evaluate(async () => {
    const t = (window as any).rewriteTest;
    try { await t.adapter.apply(t.fresh, "고정 후보", t.command); return "unexpected success"; }
    catch (e) { return String(e); }
  });
  expect(lateReplay).toContain("같은 commandId가 다른 binding 또는 terminal 상태로 이미 사용되었습니다.");
  expect(await frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toContain("이후 입력");
});
