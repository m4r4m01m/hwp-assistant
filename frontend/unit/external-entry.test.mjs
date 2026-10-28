import test from "node:test";
import assert from "node:assert/strict";
import { loadSource } from "./load-source.mjs";
const { parseExternalEntry, captureExternalEntry } = loadSource("external-entry.ts");
const id = "01234567-89ab-4cde-8123-456789abcdef";

test("external entry validates targets independently of a deployment subpath", () => {
  assert.deepEqual(parseExternalEntry(new URL(`https://synthetic.invalid/hwp-assistant/?document=${id}&version=${id}`)), { kind: "document", documentId: id, versionId: id });
  assert.deepEqual(parseExternalEntry(new URL(`https://synthetic.invalid/?report=${id.toUpperCase()}`)), { kind: "report", taskId: id });
  for (const query of [`document=${id}&report=${id}`, `document=${id}&document=${id}`, `document=${id}&version=wrong`, "document=", `version=${id}`, "report=../secrets"]) {
    assert.equal(parseExternalEntry(new URL(`https://synthetic.invalid/?${query}`)).kind, "error");
  }
  assert.equal(parseExternalEntry(new URL("https://synthetic.invalid/hwp-assistant/")), null);
});

test("link capture removes credentials including invalid ones before authentication", () => {
  const token = "synthetic_".repeat(5);
  for (const [suffix, expected] of [[`#mattermost-link=${token}`, "mattermost"], ["#mattermost-link=bad", "error"], [`?report=${id}#mattermost-link=${token}`, "error"]]) {
    const replacements = [];
    const browser = { location: { href: `https://synthetic.invalid/hwp-assistant/${suffix}` }, history: { state: { retained: true }, replaceState: (...args) => replacements.push(args) } };
    const result = captureExternalEntry(browser);
    assert.equal(result.kind, expected);
    assert.equal(replacements.length, 1);
    assert.ok(replacements[0][2].startsWith("/hwp-assistant/"));
    assert.ok(!replacements[0][2].includes("mattermost-link"));
    assert.ok(!replacements[0][2].includes(token));
    assert.deepEqual(replacements[0][0], { retained: true });
  }
});
