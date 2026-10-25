import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { loadSource } from "./load-source.mjs";

const load = () => loadSource("engine-warmup.ts", { "./public-path": { publicPath: path => `/hwp-assistant/${path}` } });
const sha = bytes => createHash("sha256").update(bytes).digest("hex");
const manifest = () => ({ publicBase: "/hwp-assistant/rhwp/", bundle: {
  "index.html": "a".repeat(64), "rhwp.js": "b".repeat(64),
  "assets/rhwp_bg-abcdefgh.wasm": "c".repeat(64), "assets/index-ABCDEFGH.js": "d".repeat(64),
  "assets/index-aB12_cdE.css": "e".repeat(64), "fonts/private-font.woff2": "f".repeat(64),
} });

test("warmup selects exact hashed same-origin iframe URLs and SRI, never API or fonts", () => {
  const { warmAssets } = load();
  const assets = warmAssets(manifest());
  assert.equal(assets.length, 3);
  assert.equal(assets[0].path, "/hwp-assistant/rhwp/assets/rhwp_bg-abcdefgh.wasm");
  assert.equal(assets[0].integrity, `sha256-${Buffer.from("c".repeat(64), "hex").toString("base64")}`);
  assert.ok(assets.every(item => item.path.startsWith("/hwp-assistant/rhwp/assets/")));
  for (const bad of [null, [], {}, { ...manifest(), publicBase: "/rhwp/" },
    { ...manifest(), bundle: { "assets/rhwp_bg-abcdefgh.wasm": "invalid" } },
    { ...manifest(), bundle: { "../api/documents": "a".repeat(64) } }])
    assert.throws(() => warmAssets(bad));
});

test("warmup defers expensive transfers for offline, data saving or hidden pages", () => {
  const { deferWarmup } = load();
  assert.equal(deferWarmup(undefined), false);
  for (const [connection, online, visible] of [[{saveData:true},true,true], [{effectiveType:"2g"},true,true], [{effectiveType:"slow-2g"},true,true], [{},false,true], [{},true,false]])
    assert.equal(deferWarmup(connection, online, visible), true);
});

test("warmup shares in-flight transfer, consumes streams and bounds retries without throwing to login", async () => {
  const old = Object.getOwnPropertyDescriptor(globalThis, "navigator"), oldDocument = globalThis.document, oldFetch = globalThis.fetch;
  try {
    Object.defineProperty(globalThis, "navigator", { configurable:true, value: { onLine:true } });
    globalThis.document = { visibilityState:"visible" };
    const bytes = Buffer.from("synthetic asset"), receipt = { publicBase:"/hwp-assistant/rhwp/", bundle:{ "assets/core-abcdefgh.wasm":sha(bytes) } };
    const calls = [];
    globalThis.fetch = async (url, options) => {
      calls.push({url,options});
      return url.endsWith(".json") ? new Response(JSON.stringify(receipt)) : new Response(bytes);
    };
    const {startEngineWarmup, warmState} = load();
    const first = startEngineWarmup(), second = startEngineWarmup();
    assert.equal(first,second);
    await first;
    assert.equal(warmState().phase,"ready");
    assert.equal(warmState().completed,1);
    await startEngineWarmup();
    assert.equal(calls.length,2);
    assert.equal(calls[1].options.cache,"default");
    assert.ok(calls[1].options.integrity.startsWith("sha256-"));
    const failed = load(); let failures=0;
    globalThis.fetch = async () => { failures++; throw Error("synthetic offline"); };
    await failed.startEngineWarmup(); await failed.startEngineWarmup(); await failed.startEngineWarmup();
    assert.equal(failures,2);
    assert.equal(failed.warmState().phase,"failed");
    await failed.startEngineWarmup(true);
    assert.equal(failures,3);
  } finally {
    if(old) Object.defineProperty(globalThis,"navigator",old); else delete globalThis.navigator;
    globalThis.document=oldDocument;globalThis.fetch=oldFetch;
  }
});
