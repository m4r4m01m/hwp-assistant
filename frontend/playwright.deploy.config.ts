import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./deployment-tests",
  timeout: 120000,
  workers: 1,
  use: {
    headless: true,
    // Only the CI localhost gateway uses a self-signed certificate.
    ignoreHTTPSErrors: process.env.HWP_CI_TLS === "1",
    viewport: { width: 1500, height: 1000 },
    trace: "off", // Do not persist login tokens in browser traces.
  },
  reporter: "list",
});
