import test from 'node:test';
import assert from 'node:assert/strict';
import { sameSelectionFence } from './verify-codex-human-continuation.mjs';

test('same paragraph is accepted across JSON key order; no AI or browser approval', () => {
  const live = { revision: 4, state: { documentSha256: 'original', documentEpoch: 2, changeSeq: 5 },
    evidence: { textSha256: 'text', formatSha256: 'format', adjacentContextSha256: 'adjacent' }, selection: { target: { kind: 'body_paragraph', section: 0, paragraph: 2, charOffset: 0, length: 48 } } };
  const expected = structuredClone(live);
  expected.selection.target = { charOffset: 0, kind: 'body_paragraph', length: 48, paragraph: 2, section: 0 };
  assert.equal(sameSelectionFence(live, expected, 'base', 'base'), true);
  assert.equal(sameSelectionFence(live, expected, 'new-version', 'base'), false);
  for (const change of [
    x => x.revision++, x => x.state.documentEpoch++, x => x.state.changeSeq++,
    x => x.state.documentSha256 = 'changed', x => x.evidence.textSha256 = 'changed',
    x => x.evidence.formatSha256 = 'changed', x => x.evidence.adjacentContextSha256 = 'changed',
    x => x.selection.target.paragraph++,
  ]) {
    const edited = structuredClone(live); change(edited);
    assert.equal(sameSelectionFence(edited, expected, 'base', 'base'), false);
  }
});
