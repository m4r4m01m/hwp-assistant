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
    const bytes = Buffer.from([0,97,115,109,1,0,0,0]), receipt = { publicBase:"/hwp-assistant/rhwp/", bundle:{ "assets/core-abcdefgh.wasm":sha(bytes) } };
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

async function withWarmupEnvironment(run) {
  const navigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const oldDocument = globalThis.document, oldFetch = globalThis.fetch;
  const oldCompile = WebAssembly.compile, oldInstantiate = WebAssembly.instantiate;
  const oldTimeout = globalThis.setTimeout;
  try {
    Object.defineProperty(globalThis, "navigator", { configurable: true, value: { onLine: true } });
    globalThis.document = { visibilityState: "visible" };
    await run();
  } finally {
    if (navigator) Object.defineProperty(globalThis, "navigator", navigator); else delete globalThis.navigator;
    globalThis.document = oldDocument; globalThis.fetch = oldFetch;
    WebAssembly.compile = oldCompile; WebAssembly.instantiate = oldInstantiate;
    globalThis.setTimeout = oldTimeout;
  }
}

const minimalWasm = Buffer.from([0, 97, 115, 109, 1, 0, 0, 0]);
const nextTurn = () => new Promise(resolve => setImmediate(resolve));
function serveWasm(bytes = minimalWasm) {
  const receipt = { publicBase: "/hwp-assistant/rhwp/", bundle: { "assets/core-abcdefgh.wasm": sha(bytes) } };
  globalThis.fetch = async url => url.endsWith(".json") ? new Response(JSON.stringify(receipt)) : new Response(bytes);
}

test("warmup waits for real WASM compilation without instantiating or claiming document readiness", async () => {
  await withWarmupEnvironment(async () => {
    serveWasm();
    const nativeCompile = WebAssembly.compile;
    let release, compiles = 0;
    const gate = new Promise(resolve => { release = resolve; });
    WebAssembly.compile = async bytes => { compiles++; await gate; return nativeCompile(bytes); };
    WebAssembly.instantiate = () => { throw Error("warmup must not instantiate WASM"); };
    const warmup = load(), stages = [];
    warmup.subscribeWarmup(() => stages.push({ ...warmup.warmState() }));
    const pending = warmup.startEngineWarmup();
    for (let i = 0; i < 20 && !compiles; i++) await nextTurn();
    assert.equal(compiles, 1);
    assert.deepEqual(warmup.warmState(), { phase: "loading", stage: "compile", completed: 1, total: 1 });
    release(); await pending;
    assert.equal(warmup.warmState().phase, "ready");
    assert.ok(stages.some(state => state.stage === "download"));
    assert.equal(stages.filter(state => state.phase === "ready").length, 1);
  });
});

test("verified downloads containing invalid WASM never report successful compilation", async () => {
  await withWarmupEnvironment(async () => {
    serveWasm(Buffer.from("not a WASM module"));
    const warmup = load(); await warmup.startEngineWarmup();
    assert.equal(warmup.warmState().phase, "failed");
  });
});

test("concurrent streams enforce one aggregate download budget before compilation", async () => {
  await withWarmupEnvironment(async () => {
    const chunk = new Uint8Array(17 * 1024 * 1024);
    const bundle = { "assets/core-abcdefgh.wasm": sha(minimalWasm) };
    for (let index = 0; index < 4; index++) bundle[`assets/part${index}-abcdefgh.js`] = "a".repeat(64);
    let compileCalls = 0, aborted = false;
    WebAssembly.compile = async () => { compileCalls++; throw Error("aggregate cap must reject first"); };
    globalThis.fetch = async (url, options) => {
      options.signal.addEventListener("abort", () => { aborted = true; }, { once: true });
      if (url.endsWith(".json")) return new Response(JSON.stringify({ publicBase: "/hwp-assistant/rhwp/", bundle }));
      if (url.endsWith(".wasm")) return new Response(minimalWasm);
      return new Response(new ReadableStream({ start(controller) { controller.enqueue(chunk); controller.close(); } }));
    };
    const warmup = load(); await warmup.startEngineWarmup();
    assert.equal(warmup.warmState().phase, "failed");
    assert.equal(compileCalls, 0);
    assert.equal(aborted, true);
  });
});

test("compile deadline releases optional warmup and ignores late native completion", async () => {
  await withWarmupEnvironment(async () => {
    serveWasm();
    let expire, release;
    const nativeTimeout = globalThis.setTimeout;
    globalThis.setTimeout = (callback, delay, ...args) => {
      if (delay === 45_000) { expire = callback; return 0; }
      return nativeTimeout(callback, delay, ...args);
    };
    WebAssembly.compile = () => new Promise(resolve => { release = resolve; });
    const warmup = load(), pending = warmup.startEngineWarmup();
    for (let i = 0; i < 20 && !release; i++) await nextTurn();
    assert.equal(warmup.warmState().stage, "compile");
    expire(); await pending;
    assert.equal(warmup.warmState().phase, "failed");
    release(); await nextTurn();
    assert.equal(warmup.warmState().phase, "failed");
  });
});
