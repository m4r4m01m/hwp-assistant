import test from "node:test";
import assert from "node:assert/strict";
import { loadSource } from "./load-source.mjs";

const { assertRewritePreviewSource } = loadSource("rewrite-preview.tsx", {
  "./editor": {}, "./api": {}, "./block-insert": {}, "./candidate-text": {}, "./rewrite-preview.css": {},
});
const snapshot = () => ({ revision: 7, state: { documentEpoch: 2 },
  selection: { target: { kind: "body_paragraph", section: 0, paragraph: 3, charOffset: 0, length: 5 } },
  evidence: { text: "합성 본문", textSha256: "a".repeat(64), formatSha256: "b".repeat(64), adjacentContextSha256: "c".repeat(64) } });

test("isolated rewrite preview accepts identical paragraph evidence under its own new epoch", () => {
  const original = snapshot(), loaded = structuredClone(original);
  loaded.revision = 0; loaded.state.documentEpoch = 1;
  assert.doesNotThrow(() => assertRewritePreviewSource(original, loaded));
  assert.deepEqual(original, snapshot());
});

test("isolated rewrite preview rejects text, style, neighbour or target drift before applying a candidate", () => {
  for (const key of ["text", "textSha256", "formatSha256", "adjacentContextSha256"]) {
    const changed = snapshot(); changed.evidence[key] += "different";
    assert.throws(() => assertRewritePreviewSource(snapshot(), changed), /원문·서식/);
  }
  for (const key of ["section", "paragraph", "charOffset", "length"]) {
    const changed = snapshot(); changed.selection.target[key]++;
    assert.throws(() => assertRewritePreviewSource(snapshot(), changed), /원문·서식/);
  }
});
