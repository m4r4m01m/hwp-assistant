import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from '../frontend/node_modules/typescript/lib/typescript.js';
const source = readFileSync(new URL('../frontend/src/text-diff.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { textDifference } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'));

for (const [name, before, after] of [
  ['unchanged', '변경하지 않은 문장', '변경하지 않은 문장'],
  ['insert', '원문', '원문 확인'], ['delete', '삭제할 표현', '표현'],
  ['separate edits', '팀은 오늘 검토하고 내일 공유한다.', '팀은 오늘 확인하고 내일 보고한다.'],
  ['emoji', '승인 👨‍👩‍👧‍👦 👍🏽 없음', '승인 👨‍👩‍👧‍👦 👍🏻 있음'],
  ['combining', 'Cafe\u0301', 'Café'],
  ['spaces', '정확한  공백\t과\n개행', '정확한 공백 과\n개행'],
  ['empty', '', ''], ['all added', '', '추가'], ['all removed', '삭제', ''],
  ['bounded long comparison', '가'.repeat(4000), '나'.repeat(4000)],
  ['html remains text', '<script>alert(1)</script>', '<b>원문</b>'],
]) test(`diff preserves exact text: ${name}`, () => {
  const parts = textDifference(before, after);
  assert.equal(parts.filter(p => p.kind !== 'added').map(p => p.text).join(''), before);
  assert.equal(parts.filter(p => p.kind !== 'removed').map(p => p.text).join(''), after);
  assert.ok(parts.every(p => p.text.length > 0));
  if (before === after) assert.ok(parts.every(p => p.kind === 'same'));
  if (name === 'emoji') {
    assert.ok(parts.some(p => p.kind === 'removed' && p.text.includes('👍🏽')));
    assert.ok(parts.some(p => p.kind === 'added' && p.text.includes('👍🏻')));
  }
});
