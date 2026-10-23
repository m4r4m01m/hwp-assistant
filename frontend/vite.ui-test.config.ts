import { defineConfig } from "vite";

// Only mocked UI lifecycle tests use this server. It neither serves a document
// engine nor bypasses the production build's engine provenance checks.
export default defineConfig({
  publicDir: false,
  // A concurrent native-engine server must keep its optimized dependency cache.
  cacheDir: "node_modules/.vite-ui-tests",
});
