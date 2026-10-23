import test from "node:test";
import assert from "node:assert/strict";
import { loadSource } from "./load-source.mjs";

const { PreviewCache } = loadSource("preview-cache.ts");
test("template preview cache is isolated, expires, and requires the exact engine signature", () => {
  let now = 100;
  const first = new PreviewCache(3, 10000, 120000, () => now), second = new PreviewCache();
  const value = { svgs: ["<svg/>"], pageCount: 2 };
  first.set("document:version:sha", "engine-a", value);
  assert.deepEqual(first.get("document:version:sha", "engine-a"), value);
  assert.equal(first.get("document:version:sha", "engine-b"), null);
  assert.equal(first.get("document:version:sha", ""), null);
  assert.equal(second.get("document:version:sha", "engine-a"), null);
  now += 120000;
  assert.equal(first.has("document:version:sha"), false);
  assert.equal(first.get("document:version:sha", "engine-a"), null);
});

test("template cache evicts least recently used entry and bounds total memory", () => {
  const cache = new PreviewCache(2, 120, 120000);
  const value = { svgs: ["1234567890"], pageCount: 1 };
  cache.set("a", "engine", value); cache.set("b", "engine", value);
  cache.get("a", "engine"); cache.set("c", "engine", value);
  assert.equal(cache.has("b"), false); assert.equal(cache.has("a"), true); assert.equal(cache.has("c"), true);
  cache.set("too-large", "engine", { svgs: ["x".repeat(200)], pageCount: 1 });
  assert.equal(cache.has("too-large"), false);
  cache.set("d", "engine", { svgs: ["x".repeat(35)], pageCount: 1 });
  assert.equal(cache.has("a"), false); assert.equal(cache.has("c"), false); assert.equal(cache.has("d"), true);
});
