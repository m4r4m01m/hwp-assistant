import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 120000,
  workers: 1,
  use: {
    actionTimeout: 10000,
    baseURL: process.env.HWP_E2E_URL || "http://127.0.0.1:5173",
    headless: true,
    viewport: { width: 1500, height: 1000 },
    launchOptions: { channel: "chrome" },
    trace: "retain-on-failure",
  },
  reporter: "list",
});
