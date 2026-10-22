import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import ts from "typescript";

// Load the actual TypeScript implementation without keeping a second JS copy.
// Dependencies may be replaced explicitly; globals remain the test's globals.
export function loadSource(relativePath, overrides = {}) {
  const relative = relativePath.startsWith(".") ? relativePath : relativePath.startsWith("src/") ? `../${relativePath}` : `../src/${relativePath}`;
  const source = new URL(relative, import.meta.url);
  const require = createRequire(source);
  const { outputText } = ts.transpileModule(readFileSync(source, "utf8"), {
    fileName: fileURLToPath(source),
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  });
  const module = { exports: {} };
  new Function("require", "module", "exports", `${outputText}\n//# sourceURL=${source.href}`)(
    name => Object.hasOwn(overrides, name) ? overrides[name] : require(name), module, module.exports,
  );
  return module.exports;
}
