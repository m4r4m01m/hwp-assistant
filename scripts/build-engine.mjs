// Pinned Studio + the verified downstream core; never fall back to the npm WASM.
import { execFileSync } from "node:child_process";
import {
  mkdirSync,
  existsSync,
  cpSync,
  lstatSync,
  unlinkSync,
  readFileSync,
  writeFileSync,
  readdirSync,
} from "node:fs";
import { resolve } from "node:path";
import { parseArgs } from "node:util";
import { root, lock, ensureSource, validateCore, normalizeNewlines, hashFile } from "./engine-build-lib.mjs";
const { values } = parseArgs({ options: {
  "core-dir": { type: "string" },
  out: { type: "string", default: "frontend/public/rhwp" },
  diagnostic: { type: "boolean", default: false },
} });
const out = resolve(root, values.out);
if (values.diagnostic && out === resolve(root, "frontend/public/rhwp"))
  throw Error("Diagnostic builds require a separate --out; preserving the default engine.");
const coreDir = resolve(root, values["core-dir"] || ".cache/rhwp-core-release/pkg");
const coreBuild = validateCore(coreDir, { diagnostic: values.diagnostic });
// Run the real engine before touching the vendor tree or publishing output.
execFileSync(process.execPath, [resolve(root, "scripts/test-engine-fill.mjs"), coreDir], { stdio: "inherit" });
const repo = ensureSource();
const commit = lock.commit;
const run = (cmd, args, cwd = repo, env = {}) =>
  execFileSync(cmd, args, {
    cwd,
    stdio: "inherit",
    env: { ...process.env, ...env },
    shell: process.platform === "win32" && cmd === "npm",
  });
const originalMain = execFileSync(
  "git",
  ["show", `${commit}:rhwp-studio/src/main.ts`],
  { cwd: repo, encoding: "utf8" },
);
const mainPath = resolve(repo, "rhwp-studio/src/main.ts");
const bridgedMain = originalMain + readFileSync(resolve(root, "scripts/assistant-bridge.ts.txt"), "utf8");
const currentMain = normalizeNewlines(readFileSync(mainPath, "utf8"));
if (currentMain !== normalizeNewlines(originalMain) && currentMain !== normalizeNewlines(bridgedMain))
  throw new Error("Preserving local Studio main.ts changes; expected only the pinned assistant bridge.");
writeFileSync(
  mainPath,
  bridgedMain,
);
cpSync(
  coreDir,
  resolve(repo, "pkg"),
  { recursive: true },
);
// Git symlinks are plain text on some Windows hosts. Materialize the licensed assets.
const studio = resolve(repo, "rhwp-studio");
const fonts = resolve(studio, "public/fonts");
if (existsSync(fonts) && !lstatSync(fonts).isDirectory()) unlinkSync(fonts);
mkdirSync(resolve(studio, "public/fonts"), { recursive: true });
cpSync(resolve(repo, "assets/fonts"), resolve(studio, "public/fonts"), {
  recursive: true,
});
run("npm", ["ci"], studio);
run("npm", ["exec", "vite", "build", "--", "--base=/rhwp/"], studio, {
  RHWP_DISABLE_EXTERNAL_WEBFONTS: "1",
  RHWP_SUBSECOND: "0",
});
const dist = resolve(studio, "dist");
const wasmAssets = readdirSync(resolve(dist, "assets")).filter(name => /^rhwp_bg-.*\.wasm$/.test(name));
if (wasmAssets.length !== 1 || hashFile(resolve(dist, "assets", wasmAssets[0])) !== coreBuild.wasm_sha256)
  throw Error("Studio did not bundle the verified core; refusing to publish.");
const bundleFiles = ["index.html", "rhwp.js", `assets/${wasmAssets[0]}`,
  ...readdirSync(resolve(dist, "assets")).filter(name => name.endsWith(".js")).map(name => `assets/${name}`)];
const bundle = Object.fromEntries(bundleFiles.map(name => [name, hashFile(resolve(dist, name))]));
mkdirSync(out, { recursive: true });
cpSync(dist, out, { recursive: true });
for (const name of ["LICENSE", "THIRD_PARTY_LICENSES.md"])
  if (existsSync(resolve(repo, name)))
    cpSync(resolve(repo, name), resolve(out, name));
writeFileSync(
  resolve(out, "build-info.json"),
  JSON.stringify(
    { version: lock.version, commit, externalWebFonts: false, diagnostic: values.diagnostic,
      bridge_sha256: hashFile(resolve(root, "scripts/assistant-bridge.ts.txt")), bundle, coreBuild },
    null,
    2,
  ),
);
console.log("Self-hosted rhwp ready:", out);
