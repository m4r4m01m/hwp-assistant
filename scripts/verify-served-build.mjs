// Compare actual loopback HTTP bytes with the complete built entry asset graph.
import { readFileSync, writeFileSync, realpathSync, statSync } from 'node:fs';
import { resolve, relative, isAbsolute, posix } from 'node:path';
import { createHash } from 'node:crypto';
import { parseArgs } from 'node:util';
import { pathToFileURL } from 'node:url';
import { parseAst } from '../frontend/node_modules/rollup/dist/es/parseAst.js';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const defaults = { files: 256, depth: 32, fileBytes: 64 * 1024 * 1024, totalBytes: 256 * 1024 * 1024 };
export function assetPath(specifier, importer = 'index.html') {
  if (typeof specifier !== 'string' || !specifier || /[\\\x00-\x20]/.test(specifier) ||
      specifier.startsWith('//') || /^[a-z][\w+.-]*:/i.test(specifier))
    throw Error('Unsafe/nonlocal asset reference: ' + specifier);
  const raw = specifier.split(/[?#]/, 1)[0];
  let decoded;
  try { decoded = decodeURIComponent(raw); } catch { throw Error('Invalid asset URL encoding'); }
  if (!decoded || /[\\\x00-\x1f:]/.test(decoded) || /%2f|%5c/i.test(raw)) throw Error('Unsafe asset path: ' + specifier);
  const name = decoded.startsWith('/') ? posix.normalize(decoded.slice(1)) : posix.normalize(posix.join(posix.dirname(importer), decoded));
  if (!name || name === '.' || name.startsWith('../') || name === '..' || posix.isAbsolute(name))
    throw Error('Asset escapes bundle root: ' + specifier);
  return name;
}
function localFile(directory, name) {
  const file = resolve(directory, name), local = relative(directory, file);
  if (local.startsWith('..') || isAbsolute(local)) throw Error('Asset escapes bundle root: ' + name);
  const physical = realpathSync(file), realLocal = relative(realpathSync(directory), physical);
  if (realLocal.startsWith('..') || isAbsolute(realLocal) || !statSync(physical).isFile())
    throw Error('Asset is not a regular bundle file: ' + name);
  return file;
}
function htmlDependencies(source) {
  const dependencies = [];
  for (const match of source.matchAll(/<(script|link)\b([^>]*?)>/gi)) {
    const attributes = {};
    for (const attribute of match[2].matchAll(/([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g))
      attributes[attribute[1].toLowerCase()] = attribute[2] ?? attribute[3] ?? attribute[4];
    if (match[1].toLowerCase() === 'script' && attributes.src) dependencies.push(attributes.src);
    if (match[1].toLowerCase() === 'link' && /^(modulepreload|stylesheet|preload)$/i.test(attributes.rel || '') && attributes.href)
      dependencies.push(attributes.href);
  }
  return dependencies;
}
function moduleDependencies(source) {
  const dependencies = [], stack = [parseAst(source)];
  while (stack.length) {
    const node = stack.pop();
    if (!node || typeof node !== 'object') continue;
    if (['ImportDeclaration', 'ExportNamedDeclaration', 'ExportAllDeclaration', 'ImportExpression'].includes(node.type) &&
        typeof node.source?.value === 'string') dependencies.push(node.source.value);
    // Vite preload tables contain CSS dependencies that are not ESM imports.
    if (node.type === 'ArrayExpression') for (const item of node.elements || [])
      if (typeof item?.value === 'string' && /^assets\/[^?#]+\.(?:m?js|css)$/.test(item.value)) dependencies.push('/' + item.value);
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) stack.push(...value.filter(v => v && typeof v === 'object'));
      else if (value && typeof value === 'object') stack.push(value);
    }
  }
  return dependencies;
}
export function collectServedFiles(directory, { built = false, limits = {} } = {}) {
  directory = resolve(directory);
  const bounds = { ...defaults, ...limits }, info = JSON.parse(readFileSync(resolve(directory, 'rhwp/build-info.json'), 'utf8'));
  const queue = [['rhwp/build-info.json', 0, false], ...Object.keys(info.bundle).map(name => [assetPath('/rhwp/' + name), 0, false])];
  if (built) queue.push(['index.html', 0, true], ['compare-renderer.html', 0, true], ['comparison-core.js', 0, true], ['comparison-engine.json', 0, true]);
  const seen = new Set(), files = [];
  let total = 0;
  while (queue.length) {
    const [name, depth, parse] = queue.shift();
    if (seen.has(name)) continue;
    if (depth > bounds.depth || seen.size >= bounds.files) throw Error('Asset graph count/depth budget exceeded');
    seen.add(name);
    const file = localFile(directory, name), size = statSync(file).size;
    total += size;
    if (size > bounds.fileBytes || total > bounds.totalBytes) throw Error('Asset graph byte budget exceeded: ' + name);
    const bytes = readFileSync(file);
    files.push({ path: name, sha256: sha(bytes), bytes: size });
    if (!parse) continue;
    const source = bytes.toString('utf8');
    let dependencies = [];
    if (name.endsWith('.html')) dependencies = htmlDependencies(source);
    else if (/\.m?js$/.test(name)) dependencies = moduleDependencies(source);
    else if (name.endsWith('.css')) {
      for (const match of source.matchAll(/(?:@import\s+(?=["'])|url\(\s*)(?:"([^"]+)"|'([^']+)'|([^\s)"';]+))/g)) {
        const dependency = match[1] ?? match[2] ?? match[3];
        if (!/^(data:|#)/i.test(dependency)) dependencies.push(dependency);
      }
    } else if (name === 'comparison-engine.json') {
      const manifest = JSON.parse(source), wasm = assetPath(manifest.wasm);
      if (!/^rhwp\/assets\/rhwp_bg-[\w-]+\.wasm$/.test(wasm) ||
          manifest.wasm_sha256 !== info.coreBuild.wasm_sha256 ||
          manifest.binding_sha256 !== info.coreBuild.binding_sha256 ||
          manifest.binding_sha256 !== sha(readFileSync(localFile(directory, 'comparison-core.js'))))
        throw Error('Comparison engine/binding receipt differs from verified Studio core');
      dependencies.push('/' + wasm);
    }
    for (const dependency of dependencies) queue.push([assetPath(dependency, name), depth + 1, true]);
  }
  if (built && !files.some(file => file.path.startsWith('assets/') && file.path.endsWith('.js')))
    throw Error('Built application JS was not found');
  return files;
}
export async function verifyServedBuild({ url, directory, built = false, limits }) {
  url = new URL(url);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password)
    throw Error('Only isolated loopback HTTP is allowed');
  const files = collectServedFiles(directory, { built, limits });
  for (const file of files) {
    const response = await fetch(new URL(file.path, url), { signal: AbortSignal.timeout(10000), redirect: 'error' });
    if (!response.ok) throw Error(`HTTP ${response.status}: ${file.path}`);
    const hash = createHash('sha256'); let length = 0;
    for await (const chunk of response.body) {
      length += chunk.length;
      if (length > file.bytes) throw Error('Served byte length differs: ' + file.path);
      hash.update(chunk);
    }
    if (length !== file.bytes || hash.digest('hex') !== file.sha256) throw Error('Served bytes differ: ' + file.path);
  }
  return { mode: built ? 'fresh diagnostic app bundle' : 'source app, copied engine', url: url.origin,
    asset_graph: built ? ['index.html', 'compare-renderer.html', 'comparison-core.js', 'comparison-engine.json'] : [], files };
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const { values } = parseArgs({ options: {
    url: { type: 'string' }, root: { type: 'string' }, out: { type: 'string' }, built: { type: 'boolean', default: false },
  } });
  const evidence = await verifyServedBuild({ url: values.url, directory: values.root, built: values.built });
  writeFileSync(values.out, JSON.stringify(evidence, null, 2));
  console.log(`Served-byte verification: ${evidence.files.length} files match (${values.built ? 'built entry graphs' : 'source'})`);
}
