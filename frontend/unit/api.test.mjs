import test from "node:test";
import assert from "node:assert/strict";
import { loadSource } from "./load-source.mjs";

const source = () => loadSource("api.ts", { "./public-path": { publicPath: value => `/${value}` } });
const abortable = signal => new Promise((_resolve, reject) => {
  if (signal.aborted) reject(new DOMException("aborted", "AbortError"));
  else signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
});
function fetchWith(t, fetch) {
  const previous = globalThis.fetch; globalThis.fetch = fetch;
  t.after(() => { globalThis.fetch = previous; });
}

test("GET deadline includes waiting for response headers", async t => {
  const { api, RequestTimeoutError } = source();
  fetchWith(t, (_url, init) => abortable(init.signal));
  await assert.rejects(api("/slow", { timeoutMs: 25 }), error => error instanceof RequestTimeoutError && !error.write);
});

test("download deadline stays active while the body is being consumed", async t => {
  const { downloadFile, RequestTimeoutError } = source();
  fetchWith(t, async (_url, init) => ({ ok: true, status: 200, arrayBuffer: () => abortable(init.signal) }));
  await assert.rejects(downloadFile("/slow-file", { timeoutMs: 25 }), RequestTimeoutError);
});

test("caller cancellation stays AbortError and does not masquerade as timeout", async t => {
  const { api, isAbortError, RequestTimeoutError } = source();
  fetchWith(t, (_url, init) => abortable(init.signal));
  const controller = new AbortController();
  const request = api("/cancel", { signal: controller.signal, timeoutMs: 1000 });
  controller.abort();
  await assert.rejects(request, error => isAbortError(error) && !(error instanceof RequestTimeoutError));
});

test("unknown write outcome retains request body and idempotency key without automatic retry", async t => {
  const { api, ApiError, RequestTimeoutError } = source();
  const calls = [], body = new Blob(["frozen output"]), headers = { "Idempotency-Key": "frozen-request" };
  fetchWith(t, (_url, init) => { calls.push(init); return abortable(init.signal); });
  await assert.rejects(api("/write", { method: "POST", headers, body, timeoutMs: 25 }), error => error instanceof RequestTimeoutError && error.write && !(error instanceof ApiError));
  assert.equal(calls.length, 1); assert.equal(calls[0].body, body); assert.equal(new Headers(calls[0].headers).get("Idempotency-Key"), "frozen-request");
});

test("workspace requests retain pinned login identity without blocking fresh login", async t => {
  const { api, pinAuthContext } = source(); const calls = [];
  fetchWith(t, async (_url, init) => { calls.push(new Headers(init.headers)); return new Response("{}"); });
  pinAuthContext("original-login");
  await api("/documents", {headers:{"X-Edit-Session":"tab-token"}});
  await api("/auth/me");
  assert.equal(calls[0].get("X-Auth-Context"), "original-login");
  assert.equal(calls[0].get("X-Edit-Session"), "tab-token");
  assert.equal(calls[1].get("X-Auth-Context"), null);
});

test("HTTP errors retain status, parse Retry-After, and redact server details", async t => {
  const { api, ApiError } = source();
  let status = 429;
  fetchWith(t, async () => new Response(JSON.stringify({ detail: status === 429 ? "잠시 기다리세요." : "private stack trace" }), { status, headers: { "Content-Type": "application/json", "Retry-After": "8" } }));
  await assert.rejects(api("/limited"), error => error instanceof ApiError && error.status === 429 && error.retryAfterMs === 8000);
  status = 503;
  await assert.rejects(api("/error"), error => error instanceof ApiError && error.status === 503 && !error.message.includes("private stack"));
});

test("success removes caller abort listener and supports empty 204 response", async t => {
  const { api } = source();
  const controller = new AbortController(), signal = controller.signal;
  const add = signal.addEventListener.bind(signal), remove = signal.removeEventListener.bind(signal);
  let adds = 0, removes = 0;
  signal.addEventListener = (...args) => { adds++; return add(...args); };
  signal.removeEventListener = (...args) => { removes++; return remove(...args); };
  fetchWith(t, async () => new Response(null, { status: 204 }));
  assert.equal(await api("/empty", { signal, timeoutMs: 50 }), undefined);
  assert.equal(adds, 1); assert.equal(removes, 1);
});
