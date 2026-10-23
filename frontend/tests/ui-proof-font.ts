import type { Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

// Optional only for proof images from minimal Linux runners lacking Korean OS
// fonts. Product CSS and the native document renderer are not changed.
export async function prepareScreenshotFont(page: Page) {
  const path = process.env.HWP_UI_PROOF_FONT;
  if (!path) return;
  const font = (await readFile(path)).toString("base64");
  await page.addStyleTag({ content: `@font-face { font-family: "UI Proof Korean"; src: url(data:font/woff2;base64,${font}) format("woff2"); font-style: normal; font-weight: 100 900; } :root { font-family: "UI Proof Korean", system-ui, sans-serif; }` });
  await page.evaluate(async () => { await document.fonts.load('14px "UI Proof Korean"'); await document.fonts.ready; });
}
