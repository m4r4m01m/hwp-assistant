import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig({
  ...base,
  testMatch: ["document-usage.spec.ts", "organization-manager.spec.ts", "document-workcopy-switcher.spec.ts", "coedit-live.spec.ts", "coedit-focus.spec.ts", "mattermost-entry.spec.ts", "document-edit-entry.spec.ts", "document-explorer.spec.ts", "team-workspaces.spec.ts", "document-branches.spec.ts", "account-password-change.spec.ts", "history-lifecycle.spec.ts", "document-trash.spec.ts", "account-entry.spec.ts", "account-management.spec.ts", "account-entry-regression.spec.ts", "responsiveness.spec.ts", "document-assistant-lifecycle.spec.ts", "assistant-design-proposals.spec.ts", "standard-designer-compare-regression.spec.ts", "rewrite-lifecycle.spec.ts", "ai-connection.spec.ts", "catalog-lifecycle.spec.ts", "design-library-mutations.spec.ts", "report-failure.spec.ts", "report-research.spec.ts", "codex-chat-review.spec.ts", "report-structure.spec.ts", "report-save-transition.spec.ts"],
  // Experimental collaboration workflows use synthetic API replies in this suite.
  use: {
    ...base.use,
    baseURL: "http://127.0.0.1:5184",
    launchOptions: { channel: process.env.HWP_E2E_BROWSER_CHANNEL || "chromium" },
  },
  webServer: {
    command: "npm exec vite -- --config vite.ui-test.config.ts --host 127.0.0.1 --port 5184 --strictPort",
    url: "http://127.0.0.1:5184",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
