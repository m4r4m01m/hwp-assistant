// One bounded Studio-only extension. The Rust/core and public command schema stay pinned.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

export const controllerPath = 'rhwp-studio/src/document-agent/controller.ts';
export const controllerSourceSha256 = 'a2a1e104076a882bc8f58e9962bffc2a214c7bcc22cbca269ff2c3c5f5cfa8a6';
export const controllerPatchedSha256 = '652e8f1d1da500d1502d2b55b9f87a159cc1dbef08c16a358df3a802342d8986';
const hash = value => createHash('sha256').update(value).digest('hex');
const normalize = value => value.replaceAll('\r\n', '\n');

// These exact fragments are compiled in the controller and exercised in fixture tests.
export const fixedPrefixHelpers = String.raw`
type FixedPrefix = { text: string; runs: Array<{ text: string; charShapeId: number }> };

function collectFixedPrefix(text: string, ids: number[]): FixedPrefix | undefined {
  if (ids.every(id => id === ids[0])) return undefined;
  const points = Array.from(text), bodyId = ids[ids.length - 1];
  let boundary = ids.length - 1;
  while (boundary > 0 && ids[boundary - 1] === bodyId) boundary -= 1;
  const prefix = points.slice(0, boundary).join('');
  if (boundary < 1 || boundary > 120 || points.length !== ids.length
      || !/^\s*[□ㅇ○\-․]\s+\([^()\r\n]{1,40}\)\s*$/.test(prefix)
      || !points.slice(boundary).join('').trim()) {
    throw new DocumentAgentError('TARGET_FORMAT_MISMATCH', '고정된 요약 말머리 외의 혼합 서식은 편집할 수 없습니다.');
  }
  const runs: FixedPrefix['runs'] = [];
  for (let offset = 0; offset < boundary; offset += 1) {
    const last = runs[runs.length - 1];
    if (last && last.charShapeId === ids[offset]) last.text += points[offset];
    else runs.push({ text: points[offset], charShapeId: ids[offset] });
  }
  return { text: prefix, runs };
}

function assertFixedPrefixReplacement(prefix: FixedPrefix | undefined, replacement: string): void {
  if (prefix && (!replacement.startsWith(prefix.text) || !replacement.slice(prefix.text.length).trim())) {
    throw new DocumentAgentError('TARGET_FORMAT_MISMATCH', '요약 말머리 원문을 유지하고 본문만 수정하세요.');
  }
}

function applyTargetCharShapes(wasm: DocumentAgentWasm, target: RhwpBodyParagraphTargetV1, evidence: TargetEvidence): void {
  if (target.length > 0) wasm.setCharShapeId(target.section, target.paragraph, 0, target.length, evidence.charShapeId);
  let offset = 0;
  for (const run of evidence.fixedPrefix?.runs || []) {
    const end = offset + codePointLength(run.text);
    wasm.setCharShapeId(target.section, target.paragraph, offset, end, run.charShapeId);
    offset = end;
  }
}
`;

const oldShapes = `  const charShapeId = charShapeIds[0];
  if (!charShapeIds.every(id => id === charShapeId)) {
    throw new DocumentAgentError(
      'TARGET_FORMAT_MISMATCH',
      '혼합 글자 서식 문단은 에이전트 명령으로 편집할 수 없습니다.',
    );
  }
`;
const newShapes = `  const charShapeId = charShapeIds[charShapeIds.length - 1];
`;
function replaceOnce(source, before, after) {
  if (source.split(before).length !== 2) throw Error('Pinned controller patch anchor is missing or ambiguous');
  return source.replace(before, after);
}
export function transformController(source) {
  let result = normalize(source);
  if (hash(result) !== controllerSourceSha256) throw Error('Pinned controller source hash differs; preserving vendor edits');
  result = replaceOnce(result, '  styleId: number;\n}\n\ninterface AgentJournalEntry', '  styleId: number;\n  fixedPrefix?: FixedPrefix;\n}\n\ninterface AgentJournalEntry');
  result = replaceOnce(result, 'export function collectTargetEvidence(', fixedPrefixHelpers + '\nexport function collectTargetEvidence(');
  result = replaceOnce(result, oldShapes, newShapes);
  result = replaceOnce(result, `    : '';
  return {
    text,
    textSha256: digestText(text),`, `    : '';
  const fixedPrefix = collectFixedPrefix(text, charShapeIds);
  return {
    text,
    textSha256: digestText(text),`);
  result = replaceOnce(result, `      styleId,
    })),`, `      styleId,
      ...(fixedPrefix ? { fixedPrefix } : {}),
    })),`);
  result = replaceOnce(result, `    charShapeId,
    paraShapeId,
    styleId,
  };`, `    charShapeId,
    paraShapeId,
    styleId,
    ...(fixedPrefix ? { fixedPrefix } : {}),
  };`);
  result = replaceOnce(result, `    const beforeNonTarget = nonTargetManifestSha256(this.deps.wasm, command.target);`, `    assertFixedPrefixReplacement(beforeEvidence.fixedPrefix, command.replacement);
    const beforeNonTarget = nonTargetManifestSha256(this.deps.wasm, command.target);`);
  result = replaceOnce(result, `            if (afterTarget.length > 0) {
              wasm.setCharShapeId(
                command.target.section,
                command.target.paragraph,
                0,
                afterTarget.length,
                beforeEvidence.charShapeId,
              );
            }`, `            applyTargetCharShapes(wasm, afterTarget, beforeEvidence);`);
  result = replaceOnce(result, `            if (entry.beforeTarget.length > 0) {
              wasm.setCharShapeId(
                entry.beforeTarget.section,
                entry.beforeTarget.paragraph,
                0,
                entry.beforeTarget.length,
                entry.beforeEvidence.charShapeId,
              );
            }`, `            applyTargetCharShapes(wasm, entry.beforeTarget, entry.beforeEvidence);`);
  return result;
}

export function patchedController(original) {
  const result = transformController(original);
  if (hash(result) !== controllerPatchedSha256) throw Error('Pinned controller patch result hash differs');
  return result;
}
export function assertControllerSource(current, original) {
  const patched = patchedController(original), normalized = normalize(current);
  if (normalized !== normalize(original) && normalized !== patched)
    throw Error('Preserving unexpected vendor controller changes');
  return patched;
}
export function controllerReceipt() {
  return { policy: 'fixed-summary-prefix-v1', path: controllerPath,
    source_sha256: controllerSourceSha256, patched_sha256: controllerPatchedSha256,
    helper_sha256: hash(readFileSync(new URL(import.meta.url))) };
}
export function validateControllerReceipt(receipt) {
  const expected = controllerReceipt();
  if (!receipt || Object.keys(expected).some(key => receipt[key] !== expected[key]))
    throw Error('Missing or mismatched pinned Studio controller receipt; rebuild the engine');
  return receipt;
}
