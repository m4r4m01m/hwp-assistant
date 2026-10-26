// Pure TS fragment fixtures. Real WASM apply/export evidence is a separate check.
import test from 'node:test';
import assert from 'node:assert/strict';
import ts from '../frontend/node_modules/typescript/lib/typescript.js';
import { fixedPrefixHelpers, controllerReceipt, validateControllerReceipt,
  patchedController, assertControllerSource } from './studio-controller-patch.mjs';

const emitted = ts.transpileModule(`
class DocumentAgentError extends Error { constructor(public code: string, message: string) { super(message); } }
const codePointLength = (text: string) => Array.from(text).length;
${fixedPrefixHelpers}
export { collectFixedPrefix, assertFixedPrefixReplacement, applyTargetCharShapes };
`, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const functions = {};
new Function('exports', emitted)(functions);
const { collectFixedPrefix, assertFixedPrefixReplacement, applyTargetCharShapes } = functions;
const fixture = (parts) => ({ text: parts.map(([text]) => text).join(''),
  ids: parts.flatMap(([text, id]) => Array.from(text).map(() => id)) });

test('uniform formatting keeps legacy policy and imposes no fixed text prefix', () => {
  for (const text of ['', '일반 문장', '□ (기존) 본문']) {
    const ids = Array.from(text || ' ').map(() => 7);
    assert.equal(collectFixedPrefix(text, ids), undefined);
    assert.doesNotThrow(() => assertFixedPrefixReplacement(undefined, '다른 문장'));
  }
});

test('only a bounded formatted lead label plus uniform body receives a fixed prefix', () => {
  const { text, ids } = fixture([['□ ', 1], ['(영향범위)', 2], [' 합성 본문', 1]]);
  const prefix = collectFixedPrefix(text, ids);
  assert.deepEqual(prefix, { text: '□ (영향범위)', runs: [{ text: '□ ', charShapeId: 1 }, { text: '(영향범위)', charShapeId: 2 }] });
  assert.doesNotThrow(() => assertFixedPrefixReplacement(prefix, '□ (영향범위) 간결한 본문'));
  for (const replacement of ['□ (현황) 간결한 본문', '간결한 본문', '□ (영향범위)', '□ (영향범위)  '])
    assert.throws(() => assertFixedPrefixReplacement(prefix, replacement), { code: 'TARGET_FORMAT_MISMATCH' });
});

test('arbitrary mixed text, inline emphasis, empty body and overlong prefixes stay rejected', () => {
  for (const parts of [
    [['임의 ', 1], ['강조', 2], [' 본문', 1]],
    [['□ ', 1], ['(범위)', 2], [' 본문 ', 1], ['중간 강조', 2], [' 꼬리', 1]],
    [['□ ', 1], ['(범위)', 2], [' ', 1]],
    [['□ ', 1], ['(' + '가'.repeat(41) + ')', 2], [' 본문', 1]],
    [[' '.repeat(120) + '□ ', 1], ['(범위)', 2], [' 본문', 1]],
    [['□ ', 1], ['(범\n위)', 2], [' 본문', 1]],
  ]) {
    const value = fixture(parts);
    assert.throws(() => collectFixedPrefix(value.text, value.ids), { code: 'TARGET_FORMAT_MISMATCH' });
  }
});

test('apply and inverse restore every fixed prefix range with Unicode codepoint offsets', () => {
  const before = fixture([['□ ', 3], ['(점검🧪)', 8], [' 긴 합성 본문', 3]]);
  const prefix = collectFixedPrefix(before.text, before.ids);
  const evidence = { charShapeId: 3, fixedPrefix: prefix };
  for (const text of ['□ (점검🧪) 본문', before.text]) {
    const target = { section: 0, paragraph: 4, length: Array.from(text).length };
    const ids = Array(target.length).fill(99), calls = [];
    const wasm = { setCharShapeId(section, paragraph, start, end, id) {
      assert.equal(section, 0); assert.equal(paragraph, 4);
      calls.push([start, end, id]); ids.fill(id, start, end);
    } };
    applyTargetCharShapes(wasm, target, evidence);
    assert.deepEqual(ids.slice(0, Array.from(prefix.text).length), before.ids.slice(0, Array.from(prefix.text).length));
    assert.ok(ids.slice(Array.from(prefix.text).length).every(id => id === 3));
    assert.equal(calls.length, 3);
    assert.deepEqual(collectFixedPrefix(text, ids), prefix);
  }
});

test('source and receipt gates reject edits, missing and tampered proof', () => {
  assert.throws(() => patchedController('unverified source'), /source hash/);
  assert.throws(() => assertControllerSource('local edits', 'unverified source'), /source hash/);
  const receipt = controllerReceipt();
  assert.equal(validateControllerReceipt(receipt), receipt);
  assert.throws(() => validateControllerReceipt(undefined), /controller receipt/);
  for (const key of Object.keys(receipt))
    assert.throws(() => validateControllerReceipt({ ...receipt, [key]: 'different' }), /controller receipt/);
});
