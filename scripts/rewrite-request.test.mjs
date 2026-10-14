// Execute the actual TSX prop callbacks with controlled API promises and refs.
// No browser, engine, model, or persistence claims are made by this unit test.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from '../frontend/node_modules/typescript/lib/typescript.js';

const main = ts.createSourceFile('main.tsx', readFileSync(new URL('../frontend/src/main.tsx', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const callbacks = new Map();
function walk(node) {
  if (ts.isJsxAttribute(node) && ['requestRewrite', 'cancelRewrite'].includes(node.name.getText(main)) && node.initializer?.expression)
    callbacks.set(node.name.getText(main), node.initializer.expression.getText(main));
  ts.forEachChild(node, walk);
}
walk(main);
assert.equal(callbacks.size, 2);
const apiSource = ts.createSourceFile('api.ts', readFileSync(new URL('../frontend/src/api.ts', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true);
const apiParts = apiSource.statements.filter(n => ['ApiError', 'jsonRequest'].includes(n.name?.getText(apiSource)))
  .map(n => n.getText(apiSource).replace(/^export /, '')).join('\n');
const compile = text => ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const observe = promise => promise.then(value => ({ value }), error => ({ error }));
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }

function fixture(t) {
  const calls = [], holdWrites = [];
  t.after(() => { for (const call of calls) call.resolve({}); });
  let uuid = 0, selection = { evidence: { text: 'unsaved A' }, revision: 1 };
  const refs = { rewriteRequest: { current: null }, reviewHold: { current: false }, busy: { current: false },
    pending: { current: null }, applied: { current: null }, blockHold: { current: null }, doc: { current: { current_version: 'base-version' } },
    token: { current: 'synthetic-only' }, adapter: { current: { selection: () => structuredClone(selection) } } };
  const env = vm.createContext({ ...refs, crypto: { randomUUID: () => `request-${++uuid}` },
    setHoldingReview: value => { env.holdingReview = value; holdWrites.push(value); }, holdingReview: false,
    api: (path, init) => { const gate = deferred(); calls.push({ path, init, ...gate }); return gate.promise; } });
  vm.runInContext(compile(apiParts + '\nglobalThis.ApiError = ApiError;'), env);
  for (const [name, code] of callbacks) vm.runInContext(compile(`globalThis.${name} = ${code};`), env);
  return { ...refs, env, calls, holdWrites, selection: value => { selection = value; } };
}

for (const response of ['success', '4xx', 'network']) {
  test(`late A ${response} cannot clear B; lost B response retries its identical input`, async t => {
    const f = fixture(t);
    const a = observe(f.env.requestRewrite('same-thread', 'instruction A'));
    const aCall = f.calls[0];
    const cancel = f.env.cancelRewrite(f.rewriteRequest.current.id);
    f.calls[1].resolve({}); await cancel;
    f.selection({ evidence: { text: 'unsaved B, non-target remains' }, revision: 2 });
    const b = observe(f.env.requestRewrite('same-thread', 'instruction B'));
    const frozenB = f.rewriteRequest.current, bodyB = f.calls[2].init.body;
    const writesBefore = f.holdWrites.length;
    if (response === 'success') aCall.resolve({});
    else aCall.reject(response === '4xx' ? new f.env.ApiError(409, 'synthetic late conflict') : Error('synthetic lost A response'));
    const outcome = await a;
    assert.equal(f.rewriteRequest.current, frozenB);
    assert.equal(f.reviewHold.current, true); assert.equal(f.env.holdingReview, true);
    assert.equal(f.holdWrites.length, writesBefore);
    if (response !== 'success') assert.doesNotMatch(String(outcome.error), /요청 원문과 ID를 보관/);
    f.calls[2].reject(Error('synthetic lost B response'));
    assert.match(String((await b).error), /요청 원문과 ID를 보관/);
    assert.equal(f.rewriteRequest.current, frozenB);
    f.selection({ evidence: { text: 'later typing must not replace B snapshot' }, revision: 3 });
    const retry = f.env.requestRewrite('same-thread', 'instruction B');
    assert.equal(f.calls[3].init.body, bodyB);
    assert.equal(f.rewriteRequest.current, frozenB);
    f.calls[3].resolve({}); await retry;
    assert.equal(f.rewriteRequest.current, null);
    assert.equal(f.reviewHold.current, true); assert.equal(f.env.holdingReview, true);
  });
}

for (const response of ['success', '4xx', 'network']) {
  test(`late cancellation ${response} cannot touch a newer active request`, async t => {
    const f = fixture(t);
    const a = observe(f.env.requestRewrite('same-thread', 'instruction A'));
    const old = f.rewriteRequest.current;
    const cancel = observe(f.env.cancelRewrite(old.id));
    // A second panel can finish cancellation while the first panel's response is delayed.
    const reconfirm = f.env.cancelRewrite(old.id);
    f.calls[2].resolve({}); await reconfirm;
    const b = observe(f.env.requestRewrite('same-thread', 'instruction B'));
    const frozenB = f.rewriteRequest.current;
    if (response === 'success') f.calls[1].resolve({});
    else f.calls[1].reject(response === '4xx' ? new f.env.ApiError(409, 'late cancellation') : Error('lost cancel response'));
    await cancel;
    assert.equal(f.rewriteRequest.current, frozenB);
    assert.equal(f.reviewHold.current, true); assert.equal(f.env.holdingReview, true);
    f.calls[0].reject(Error('late A')); await a;
    f.calls[3].reject(Error('lost B')); await b;
  });
}

for (const previousHold of [false, true]) {
  test(`current request 4xx restores only its own previous hold (${previousHold})`, async t => {
    const f = fixture(t); f.reviewHold.current = previousHold;
    const request = observe(f.env.requestRewrite('same-thread', 'current'));
    f.calls[0].reject(new f.env.ApiError(429, 'capacity full')); await request;
    assert.equal(f.rewriteRequest.current, null);
    assert.equal(f.reviewHold.current, previousHold); assert.equal(f.env.holdingReview, previousHold);
  });
}

test('lost contextual request retries its frozen revision after later context and typing changes', async t => {
  const f = fixture(t);
  const first = observe(f.env.requestRewrite('thread', 'instruction', 7));
  const body = f.calls[0].init.body;
  assert.equal(JSON.parse(body).context_revision, 7);
  f.calls[0].reject(Error('lost response')); await first;
  f.selection({ evidence: { text: 'later edit' }, revision: 99 });
  const retry = f.env.requestRewrite('thread', 'instruction', 8);
  assert.equal(f.calls[1].init.body, body);
  f.calls[1].resolve({}); await retry;
});
