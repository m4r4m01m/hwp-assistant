import test from "node:test";
import assert from "node:assert/strict";
import { loadSource } from "./load-source.mjs";

function fixture(t, resolveImmediately = false) {
  const previous = { location: globalThis.location, HTMLIFrameElement: globalThis.HTMLIFrameElement };
  globalThis.location = { origin: "http://test.invalid" };
  class Frame { removed = false; contentWindow = { hwpAssistantBridge: {} }; remove() { this.removed = true; } }
  globalThis.HTMLIFrameElement = Frame;
  t.after(() => {
    if (previous.location === undefined) delete globalThis.location; else globalThis.location = previous.location;
    if (previous.HTMLIFrameElement === undefined) delete globalThis.HTMLIFrameElement; else globalThis.HTMLIFrameElement = previous.HTMLIFrameElement;
  });
  const existing = new Frame(), created = new Frame();
  const container = { children: [existing] };
  let finish, destroyed = 0, calls = 0;
  const sdk = { element: created, destroy() { destroyed++; created.remove(); } };
  const pending = new Promise(resolve => { finish = () => resolve(sdk); });
  const { EditorAdapter } = loadSource("editor.ts", {
    "./public-path": { publicPath: value => `/${value}` },
    "@rhwp/editor": { createEditor: () => { calls++; container.children.push(created); return resolveImmediately ? Promise.resolve(sdk) : pending; } },
  });
  return { EditorAdapter, container, existing, created, finish, destroyed: () => destroyed, calls: () => calls };
}

test("cancelling a pending editor removes only its iframe and destroys late completion", async t => {
  const state = fixture(t), controller = new AbortController();
  const pending = state.EditorAdapter.mount(state.container, { signal: controller.signal, timeoutMs: 1000 });
  controller.abort();
  await assert.rejects(pending, error => error.name === "AbortError");
  assert.equal(state.existing.removed, false); assert.equal(state.created.removed, true);
  state.finish(); await Promise.resolve(); await Promise.resolve();
  assert.equal(state.destroyed(), 1);
});

test("initial iframe load has a finite deadline and leaves existing editor intact", async t => {
  const state = fixture(t);
  await assert.rejects(state.EditorAdapter.mount(state.container, { timeoutMs: 25 }), /준비 시간이 길어졌습니다/);
  assert.equal(state.created.removed, true); assert.equal(state.existing.removed, false);
  state.finish(); await Promise.resolve(); await Promise.resolve();
  assert.equal(state.destroyed(), 1);
});

test("pre-aborted mount creates no iframe and normal mount remains live after listener cleanup", async t => {
  const state = fixture(t, true), controller = new AbortController(); controller.abort();
  await assert.rejects(state.EditorAdapter.mount(state.container, { signal: controller.signal }), error => error.name === "AbortError");
  assert.equal(state.calls(), 0);
  const live = new AbortController();
  const adapter = await state.EditorAdapter.mount(state.container, { signal: live.signal, timeoutMs: 1000 });
  live.abort();
  assert.equal(state.created.removed, false); assert.equal(state.destroyed(), 0);
  adapter.destroy(); assert.equal(state.destroyed(), 1);
});
