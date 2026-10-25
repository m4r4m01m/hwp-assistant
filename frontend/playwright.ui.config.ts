import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig({
  ...base,
  testMatch: ["account-entry.spec.ts", "responsiveness.spec.ts", "document-assistant-lifecycle.spec.ts", "rewrite-lifecycle.spec.ts"],
  use: {
    ...base.use,
    baseURL: "http://127.0.0.1:5184",
    launchOptions: { channel: "chromium" },
  },
  webServer: {
    command: "npm exec vite -- --config vite.ui-test.config.ts --host 127.0.0.1 --port 5184 --strictPort",
    url: "http://127.0.0.1:5184",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
