import { defineConfig } from "vite";

// Only mocked UI lifecycle tests use this server. It neither serves a document
// engine nor bypasses the production build's engine provenance checks.
export default defineConfig({ publicDir: false });
