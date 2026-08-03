import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: "/dashboard/",
  plugins: [react()],
  css: {
    transformer: "lightningcss",
  },
  build: {
    outDir: "../src/gate_bridge/static/dashboard",
    emptyOutDir: true,
    sourcemap: false,
    cssMinify: "lightningcss",
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      "/dashboard/api": "http://127.0.0.1:8080",
    },
  },
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: {
        url: "http://localhost/dashboard/",
      },
    },
    setupFiles: "./src/test/setup.ts",
    css: false,
  },
});
