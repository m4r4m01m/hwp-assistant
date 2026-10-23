import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./deployment-tests",
  timeout: 120000,
  workers: 1,
  use: {
    headless: true,
    // run-browser-checks.sh trusts the disposable CI certificate in NSS/Node.
    // Do not bypass TLS: Service Worker script loading validates it separately.
    ignoreHTTPSErrors: false,
    serviceWorkers: "allow",
    viewport: { width: 1500, height: 1000 },
    trace: "off", // Do not persist login tokens in browser traces.
  },
  reporter: "list",
});
