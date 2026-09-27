// Read-only live checks on an existing Playwright page; no browser launch or recovery.
import { expect } from '../frontend/node_modules/@playwright/test/index.mjs';
import { unzipSync, strFromU8 } from '../frontend/node_modules/fflate/esm/index.mjs';
import { createHash } from 'node:crypto';
export function liveTools({browser, context, page}) {
  let frame = page.frames().find(f => f.url().includes('/rhwp/'));
  const hash = data => createHash('sha256').update(data).digest('hex');
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const closed = () => !browser.isConnected() || page.isClosed();
async function get(url) {
  const r = await context.request.get(url);
  if (!r.ok()) throw Error(`GET ${url}: ${r.status()}`);
  return r.json();
}
const snapshot = () => frame.evaluate(() => window.hwpAssistantBridge.snapshot());
async function exported() {
  const result = await frame.evaluate(() => {
    const s = window.hwpAssistantBridge.exportSnapshot();
    return { bytes: Array.from(s.bytes), loss: s.contentLoss.count, revision: s.revision, epoch: s.epoch };
  });
  expect(result.loss).toBe(0);
  return Buffer.from(result.bytes);
}
async function inspect(bytes) {
  // Read-only ZIP/XML inspection; never constructs or modifies an output HWPX.
  const files = unzipSync(bytes);
  const sections = Object.keys(files).filter(n => /^Contents\/section\d+\.xml$/.test(n)).sort();
  const paragraphs = await page.evaluate(sections => sections.flatMap(({ name, xml }) => {
    const tree = new DOMParser().parseFromString(xml, 'application/xml');
    if (tree.querySelector('parsererror')) throw Error('HWPX XML parse error');
    return Array.from(tree.documentElement.children).filter(e => e.localName === 'p').map((p, index) => ({
      section: Number(name.match(/section(\d+)/)[1]), paragraph: index,
      text: Array.from(p.getElementsByTagNameNS('*', 't')).map(t => t.textContent).join(''),
      xml: new XMLSerializer().serializeToString(p),
    }));
  }), sections.map(name => ({ name, xml: strFromU8(files[name]) })));
  return { paragraphs: paragraphs.map(p => ({ ...p, xml: undefined, structure_sha256: hash(p.xml) })),
    binary_resources: Object.fromEntries(Object.entries(files).filter(([n]) => !n.endsWith('/') && !/\.(xml|hpf)$/i.test(n) && !n.startsWith('Preview/')).map(([n, bytes]) => [n, hash(bytes)])) };
}
const isTarget = (p, target) => p.section === target.section && p.paragraph === target.paragraph;
function preserved(before, after, target) {
  expect(after.paragraphs.filter(p => !isTarget(p, target))).toEqual(before.paragraphs.filter(p => !isTarget(p, target)));
  expect(after.binary_resources).toEqual(before.binary_resources);
}
async function focusEnd() {
  await frame.locator('#scroll-container canvas').first().click({ position: { x: 160, y: 150 }, force: true });
  await page.keyboard.press('Control+End');
}

  return {get, snapshot, exported, inspect, preserved, focusEnd, hash, sleep, closed, isTarget,
    setFrame(value) { frame = value; }};
}
