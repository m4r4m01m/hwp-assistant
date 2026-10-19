import { defineConfig } from "vite";
import { resolve } from "node:path";
import { comparisonCorePlugin } from "../scripts/comparison-core.mjs";
const publicDir = resolve(process.env.HWP_PUBLIC_DIR || "public");
export default defineConfig({
  publicDir,
  plugins: [
    comparisonCorePlugin({
      publicDir,
      diagnostic: process.env.HWP_DIAGNOSTIC_ENGINE === "1",
    }),
  ],
  build: {
    rollupOptions: {
      input: {
        app: resolve("index.html"),
        comparison: resolve("compare-renderer.html"),
      },
    },
  },
  server: {
    proxy: {
      "/api": {
        target: process.env.HWP_API_PROXY || "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
});
