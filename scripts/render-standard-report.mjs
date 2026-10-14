// Opt-in local artifact authoring. Never opens an existing user browser tab.
import { chromium } from '../frontend/node_modules/@playwright/test/index.mjs';
import { readFile, writeFile, mkdir, access } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';
const phase = process.argv[2], work = resolve(process.argv[3]);
const base = 'http://127.0.0.1:15180';
const plan = JSON.parse(await readFile(resolve(work, 'report-plan.json'), 'utf8'));
const browser = await chromium.launch({ channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1250, height: 1080 }, deviceScaleFactor: 1.5 });
await page.route('**/standard-lab/**', async route => {
  const name = new URL(route.request().url()).pathname.slice('/standard-lab/'.length);
  if (name.endsWith('.js')) return route.fulfill({ contentType: 'application/javascript', body: await readFile(resolve('frontend/node_modules/@rhwp/editor', name)) });
  await route.fulfill({ contentType: 'text/html', body: '<html><body style="margin:0"><div id="editor" style="height:1080px"></div></body></html>' });
});
try {
  if (phase === 'apply') {
    await page.goto(base + '/standard-lab/');
    await page.evaluate(async () => {
      const { createEditor } = await import('/standard-lab/index.js');
      window.sdk = await createEditor('#editor', { studioUrl: location.origin + '/rhwp/index.html?chrome=embed', requestTimeoutMs: 90000 });
    });
    let receipts = await readFile(resolve(work, 'exact-command-receipts.json'), 'utf8').then(s => JSON.parse(s).receipts).catch(() => []);
    for (const batch of [...new Set(plan.items.map(i => i.batch))]) {
      const batchItems = plan.items.filter(i => i.batch === batch);
      const hasExport = await access(resolve(work, `applied-${batch}.hwpx`)).then(() => true).catch(() => false);
      if (hasExport && batchItems.every(i => receipts.some(r => r.id === i.id && r.text_sha256 === createHash('sha256').update(i.text).digest('hex')))) continue;
      const pending = [];
      const data = await readFile(resolve(work, `scaffold-${batch}.hwpx`));
      await page.evaluate(async b64 => {
        const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        await window.sdk.loadFile(bytes.buffer, 'new-standard-scaffold.hwpx', { suppressDialogs: true, skipUnsavedGuard: true });
      }, data.toString('base64'));
      for (const item of plan.items.filter(i => i.batch === batch)) {
        const receipt = await page.evaluate(async ({ item, commandId }) => {
          const sdk = window.sdk, bridge = sdk.element.contentWindow.hwpAssistantBridge;
          await sdk.focusTarget({ kind: 'body_paragraph', section: 0, paragraph: item.paragraph, charOffset: 0, length: [...item.before].length });
          const s = bridge.snapshot();
          if (s.evidence.text !== item.before || s.selection.target.paragraph !== item.paragraph) throw Error('Preimage mismatch ' + item.id);
          return sdk.applyTextCommand({ schemaVersion: 1, commandId,
            expectedDocumentEpoch: s.state.documentEpoch, expectedChangeSeq: s.state.changeSeq,
            expectedDocumentSha256: s.state.documentSha256, target: s.selection.target,
            expectedBeforeSha256: s.evidence.textSha256, expectedFormatSha256: s.evidence.formatSha256,
            expectedAdjacentContextSha256: s.evidence.adjacentContextSha256, replacement: item.text });
        }, { item, commandId: randomUUID() });
        pending.push({ id: item.id, text_sha256: createHash('sha256').update(item.text).digest('hex'), receipt });
      }
      const exported = await page.evaluate(() => {
        const result = window.sdk.element.contentWindow.hwpAssistantBridge.exportSnapshot();
        return { bytes: Array.from(result.bytes), contentLoss: result.contentLoss };
      });
      if (exported.contentLoss.count) throw Error(JSON.stringify(exported.contentLoss));
      await writeFile(resolve(work, `applied-${batch}.hwpx`), Buffer.from(exported.bytes));
      receipts = receipts.filter(r => !batchItems.some(i => i.id === r.id)).concat(pending);
      await writeFile(resolve(work, 'exact-command-receipts.json'), JSON.stringify({ approval_basis: 'User expressly requested a new report using the supplied standard. Agent executes this authorized artifact creation; no claim of a person clicking product approval.', receipts }, null, 2));
      console.log(JSON.stringify({ batch, applied: receipts.length }));
    }
  } else if (phase === 'prototype') {
    const core = await import(pathToFileURL(resolve('.cache/rhwp/pkg/rhwp.js')).href);
    await core.default({ module_or_path: await readFile('.cache/engine-release-2026-09-27/public/rhwp/assets/rhwp_bg-DdftfNrC.wasm') });
    const doc = new core.HwpDocument(await readFile(resolve(work, 'scaffold-0.hwpx')));
    const pixel = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==', 'base64');
    console.log(doc.insertPicture(0, 1, 0, '', pixel, 48190, 18312, 1500, 570, 'png', ''));
    await writeFile(resolve(work, 'picture-prototype.hwpx'), doc.exportHwpx());
    doc.free();
  } else if (phase === 'diagrams') {
    const approved = JSON.parse(await readFile(resolve(work, 'exact-command-receipts.json'), 'utf8')).receipts;
    const esc = s => s.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
    for (const block of plan.pages.flat().filter(b => b.type === 'image')) {
      const labels = block.labels.map(id => {
        const item = plan.items.find(i => i.id === id);
        if (!approved.some(r => r.id === id && r.text_sha256 === createHash('sha256').update(item.text).digest('hex'))) throw Error('Unapplied label');
        return esc(item.text);
      });
      const risk = block.name === 'risk-flow', height = risk ? 570 : 395;
      const text = (x,y,s,size=29,weight=400,color='#111') => `<text x="${x}" y="${y}" text-anchor="middle" fill="${color}" font-family="Noto Sans KR" font-size="${size}" font-weight="${weight}">${s}</text>`;
      let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="${height}" viewBox="0 0 1500 ${height}"><rect width="1500" height="${height}" fill="white"/>`;
      for(let c=0;c<3;c++) {
        const x=10+c*510, cy=x+235;
        svg += `<rect x="${x}" y="25" width="460" height="${risk?320:245}" fill="white" stroke="#171717" stroke-width="2"/><rect x="${x}" y="25" width="460" height="75" fill="#e5e5e5"/>`;
        if(risk) {
          svg += text(cy,76,labels[c*3],36,700) + text(cy,173,labels[c*3+1],30) + text(cy,228,labels[c*3+2],30);
          svg += `<rect x="${x}" y="390" width="460" height="116" fill="white" stroke="#1a4d83" stroke-width="3"/>` + text(cy,458,labels[9+c],31,700,'#1a4d83');
          svg += `<path d="M${cy} 352 V380" stroke="#1a4d83" stroke-width="3"/>`;
        } else {
          svg += text(cy,76,labels[c*2],36,700) + text(cy,162,labels[c*2+1],34,700) + text(cy,218,labels[6+c],28);
          svg += `<path d="M${x+25} 322 H${x+435}" stroke="#1a4d83" stroke-width="6"/>`;
        }
        if(c<2) svg += `<path d="M${x+472} 195 h27 l-10 -10 m10 10 l-10 10" fill="none" stroke="#171717" stroke-width="4"/>`;
      }
      svg+='</svg>';
      await writeFile(resolve(work, block.name + '.svg'), svg);
      await page.setContent(`<style>@font-face{font-family:'Noto Sans KR';src:url('${base}/rhwp/fonts/NotoSansKR-Regular.woff2')}body{margin:0}</style>${svg}`);
      await page.evaluate(() => document.fonts.ready);
      await page.locator('svg').screenshot({ path: resolve(work, block.name + '.png') });
    }
  } else if (phase === 'render') {
    await page.goto(base + '/standard-lab/');
    await page.evaluate(async () => {
      const { createEditor } = await import('/standard-lab/index.js');
      window.sdk = await createEditor('#editor', { studioUrl: location.origin + '/rhwp/index.html?chrome=embed', requestTimeoutMs: 90000 });
    });
    const input = await readFile(resolve(work, 'financial-cyber-standard.hwpx'));
    const loaded = await page.evaluate(async b64 => window.sdk.loadFile(Uint8Array.from(atob(b64), c => c.charCodeAt(0)).buffer, '금융권 해킹 대응 보고서.hwpx', { suppressDialogs: true }), input.toString('base64'));
    const count = await page.evaluate(() => window.sdk.pageCount());
    console.log(JSON.stringify({ loaded, count }));
    await mkdir(resolve(work, 'pages'), { recursive: true });
    const rendered = [];
    for(let p=0;p<count;p++) {
      const result = await page.evaluate(p => window.sdk.getPageSvg(p), p);
      let svg = typeof result === 'string' ? result : result.svg;
      if(!svg) throw Error(JSON.stringify(result).slice(0,300));
      // Each rhwp page reuses clip ids. Make them unique in the combined preview/PDF.
      const ids = [...svg.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
      for (const id of ids) {
        svg = svg.replaceAll(`id="${id}"`, `id="p${p}-${id}"`)
          .replaceAll(`url(#${id})`, `url(#p${p}-${id})`).replaceAll(`href="#${id}"`, `href="#p${p}-${id}"`);
      }
      await writeFile(resolve(work, 'pages', `page-${p+1}.svg`), svg);
      rendered.push(svg);
    }
    const exported = await page.evaluate(() => {
      const r = window.sdk.element.contentWindow.hwpAssistantBridge.exportSnapshot();
      return { bytes: Array.from(r.bytes), contentLoss: r.contentLoss, warnings: window.sdk.element.contentWindow.hwpAssistantBridge.warnings() };
    });
    await writeFile(resolve(work, 'roundtrip.hwpx'), Buffer.from(exported.bytes));
    const reopened = await page.evaluate(async bytes => window.sdk.loadFile(Uint8Array.from(bytes).buffer, 'roundtrip-verification.hwpx', { suppressDialogs: true, skipUnsavedGuard: true }), exported.bytes);
    if (reopened.pageCount !== count) throw Error('Roundtrip page count changed');
    await writeFile(resolve(work, 'render-proof.json'), JSON.stringify({ count, reopened, contentLoss: exported.contentLoss, warnings: exported.warnings, engine: 'real rhwp diagnostic engine; no Hancom verification', source_sha256: createHash('sha256').update(input).digest('hex') }, null, 2));
    const preview = await browser.newPage({ viewport: { width: 920, height: 1280 }, deviceScaleFactor: 1 });
    const faces = ['NotoSansKR-Regular','NotoSerifKR-Regular','NanumMyeongjo-Regular','NanumGothic-Regular'].map(f => `@font-face{font-family:'${f.replace('-Regular','')}';src:url('${base}/rhwp/fonts/${f}.woff2')}`).join('');
    const html = `<html lang="ko"><head><meta charset="utf-8"><title>금융권 해킹 대응 보고서</title><style>${faces}body{background:#d7d9dc;margin:0;padding:24px}article{width:794px;background:white;margin:0 auto 24px;box-shadow:0 2px 12px #0002}article svg{display:block;width:794px;height:auto}@media print{@page{size:A4;margin:0}body{background:white;padding:0}article{margin:0;box-shadow:none;break-after:page;width:210mm}article svg{width:210mm}}</style></head><body>${rendered.map(svg=>`<article>${svg}</article>`).join('')}</body></html>`;
    await writeFile(resolve(work,'preview.html'),html);
    await preview.setContent(html); await preview.evaluate(() => document.fonts.ready);
    for(let i=0;i<rendered.length;i++) await preview.locator('article').nth(i).screenshot({path:resolve(work,'pages',`page-${i+1}.png`)});
    await preview.pdf({ path:resolve(work,'financial-cyber-standard.pdf'), printBackground:true, preferCSSPageSize:true });
  } else throw Error('Unknown phase');
} finally { await browser.close(); }
