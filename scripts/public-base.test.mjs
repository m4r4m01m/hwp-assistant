import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizePublicBase, withPublicBase } from './public-base.mjs';

for (const [value, expected] of [['/', '/'], ['/hwp-assistant', '/hwp-assistant/'], ['/hwp-assistant/', '/hwp-assistant/'], ['/demo/hwp/', '/demo/hwp/']]) {
  test(`normalize ${value}`, () => assert.equal(normalizePublicBase(value), expected));
}
for (const value of ['', '.', './', '//other/', 'https://example.com/', '/a/../', '/a b/', '/a?x=1', '/a/#fragment', '/a\\b/']) {
  test(`reject invalid public base ${JSON.stringify(value)}`, () => assert.throws(() => normalizePublicBase(value)));
}
for (const path of ['api/documents', '/api/auth/login', 'rhwp/index.html?chrome=embed', 'compare-renderer.html', 'comparison-core.js', 'rhwp/assets/rhwp_bg-abc.wasm', 'rhwp/fonts/NanumGothic-Regular.woff2']) {
  test(`prefixed resource ${path}`, () => {
    assert.equal(withPublicBase('/hwp-assistant/', path), '/hwp-assistant/' + path.replace(/^\//, ''));
    assert.equal(withPublicBase('/', path), '/' + path.replace(/^\//, ''));
  });
}
for (const path of ['//other/path', 'https://example.com/api', 'javascript:alert(1)']) {
  test(`reject foreign URL ${path}`, () => assert.throws(() => withPublicBase('/hwp-assistant/', path)));
}
