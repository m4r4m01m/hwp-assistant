import { defineConfig } from "vite";
export default defineConfig({
  server: {
    proxy: {
      "/api": {
        target: process.env.HWP_API_PROXY || "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
});
