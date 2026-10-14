import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { unzipSync, strFromU8 } from "fflate";

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

test("REAL installed rhwp: every document, text, format and adjacent-context fence rejects without mutation", async ({ page }) => {
  await page.goto("/");
  await page.evaluate(async () => {
    const modulePath = "/src/editor.ts";
    const { EditorAdapter } = await import(/* @vite-ignore */ modulePath);
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;inset:0;z-index:9999;background:white";
    document.body.append(host);
    const adapter = await EditorAdapter.mount(host);
    await adapter.bridge.newDocument();
    (window as any).fenceTest = adapter;
  });
  const frame = page.frames().find(f => f.url().includes("/rhwp/"))!;
  await frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("서식과 주변 문맥도 지켜야 하는 합성 원문 😀");
  const results = await page.evaluate(async () => {
    const adapter = (window as any).fenceTest;
    const original = adapter.selection();
    const mutations: [string, (s: any) => void][] = [
      ["text hash", s => s.evidence.textSha256 = "f".repeat(64)],
      ["format hash", s => s.evidence.formatSha256 = "f".repeat(64)],
      ["adjacent context hash", s => s.evidence.adjacentContextSha256 = "f".repeat(64)],
      ["document hash", s => s.state.documentSha256 = "f".repeat(64)],
      ["document epoch", s => s.state.documentEpoch++],
      ["change sequence", s => s.state.changeSeq++],
      ["revision", s => s.revision++],
      ["target paragraph", s => s.selection.target.paragraph++],
    ];
    const observations = [];
    for (const [name, change] of mutations) {
      const altered = structuredClone(original);
      change(altered);
      let error = "";
      try { await adapter.apply(altered, "적용되면 안 되는 후보", crypto.randomUUID()); }
      catch (e) { error = String(e); }
      observations.push({ name, error, unchanged: JSON.stringify(adapter.selection()) === JSON.stringify(original) });
    }
    for (const replacement of ["x".repeat(4001), "두 줄\n후보", "탭\t후보"]) {
      let error = "";
      try { await adapter.apply(original, replacement, crypto.randomUUID()); }
      catch (e) { error = String(e); }
      observations.push({ name: "invalid replacement", error, unchanged: JSON.stringify(adapter.selection()) === JSON.stringify(original) });
    }
    return observations;
  });
  for (const result of results) {
    expect(result.error, result.name).not.toBe("");
    expect(result.unchanged, result.name).toBe(true);
  }
});

for (const changesPages of [false, true]) {
test(`REAL installed rhwp, NO AI: 4000 Unicode codepoints ${changesPages ? "roll back when pagination changes" : "apply and undo when pagination is preserved"}`, async ({ page }) => {
  await page.goto("/");
  await page.evaluate(async () => {
    const modulePath = "/src/editor.ts";
    const { EditorAdapter } = await import(/* @vite-ignore */ modulePath);
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;inset:0;z-index:9999;background:white";
    document.body.append(host);
    const adapter = await EditorAdapter.mount(host);
    await adapter.bridge.newDocument();
    (window as any).unicodeBoundary = adapter;
  });
  const frame = page.frames().find(f => f.url().includes("/rhwp/"))!;
  await frame.locator("#scroll-container canvas").first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press("Control+End");
  await page.keyboard.press("Enter");
  const original = changesPages ? "유니코드 경계 원문" : "나😀".repeat(2000);
  await page.keyboard.insertText(original);
  // 4,000 Unicode codepoints, 6,000 UTF-16 code units: catches an accidental JS .length gate.
  const replacement = "가😀".repeat(2000);
  const result = await page.evaluate(async text => {
    const adapter = (window as any).unicodeBoundary;
    const before = adapter.selection();
    const beforeBytes = Array.from(adapter.exportHwpx().bytes) as number[];
    let receipt, error = "";
    try { receipt = await adapter.apply(before, text, crypto.randomUUID()); }
    catch (failure) { error = String(failure); }
    return { receipt, error, before, beforeBytes, after: adapter.selection(), bytes: Array.from(adapter.exportHwpx().bytes) as number[] };
  }, replacement);
  if (changesPages) {
    expect(result.error).toContain("문서 페이지 수가 변경되었습니다");
    expect(result.receipt).toBeUndefined();
    expect(result.after.evidence.text).toBe(original);
    expect(result.after.state.pageCount).toBe(result.before.state.pageCount);
    expect(result.after.state.documentSha256).toBe(result.before.state.documentSha256);
    expect(Buffer.from(result.bytes)).toEqual(Buffer.from(result.beforeBytes));
    return;
  }
  expect(result.error).toBe("");
  expect(result.after.evidence.text).toBe(replacement);
  expect(result.after.selection.target.length).toBe(4000);
  expect(result.receipt.afterTextSha256).toBe(createHash("sha256").update(replacement).digest("hex"));
  expect(strFromU8(unzipSync(Buffer.from(result.bytes))["Contents/section0.xml"])).toContain(replacement);
  await page.keyboard.press("Control+z");
  await expect.poll(() => frame.evaluate(() => (window as any).hwpAssistantBridge.snapshot().evidence.text)).toBe(original);
});
}
