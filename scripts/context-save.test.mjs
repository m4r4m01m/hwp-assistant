// Execute the actual context-save callback with a disposed component and late promises.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from '../frontend/node_modules/typescript/lib/typescript.js';
const parse = name => ts.createSourceFile(name, readFileSync(new URL(`../frontend/src/${name}`, import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const source = parse('context.tsx');
let saveSource;
function walk(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === 'save') saveSource = node.getText(source);
  ts.forEachChild(node, walk);
}
walk(source); assert.ok(saveSource);
const api = parse('api.ts');
const apiCode = api.statements.filter(n => ['ApiError', 'jsonRequest'].includes(n.name?.text)).map(n => n.getText(api).replace(/^export /, '')).join('\n');
const compile = value => ts.transpileModule(value, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

for (const result of ['success', '4xx', 'network']) test(`disposed writing-context ${result} cannot clear a newer panel's dirty guard`, async () => {
  const gate = deferred(), writes = [], dirtyWrites = [];
  let loads = 0;
  const active = { current: true };
  const context = vm.createContext({ active, busy: false, pending: { current: null }, edits: { current: 1 },
    context: { selection: null }, capture: undefined, revision: 1, baseVersion: 'v1', thread: 'thread',
    draft: { purpose: 'old draft' }, confirmed: true, crypto: { randomUUID: () => 'fixed-write-id' },
    api: () => gate.promise, onSaved: async () => { loads++; }, onDirty: value => dirtyWrites.push(value),
    ...Object.fromEntries(['setBusy','setError','setNotice','setNeedsRecheck','setRevision','setDirty','setConfirmed'].map(name => [name, value => writes.push([name, value])])),
  });
  vm.runInContext(compile(apiCode + '\nglobalThis.ApiError=ApiError;\n' + saveSource + '\nglobalThis.save=save;'), context);
  const saving = context.save(false);
  active.current = false;
  const before = writes.length, beforeDirty = dirtyWrites.length;
  if (result === 'success') gate.resolve({ revision: 2 });
  else gate.reject(result === '4xx' ? new context.ApiError(409, 'late conflict') : Error('late network failure'));
  await saving;
  assert.equal(loads, 0);
  assert.equal(writes.length, before);
  assert.equal(dirtyWrites.length, beforeDirty);
});
