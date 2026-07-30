import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

import packageInfo from "./package.json";

const buildId = process.env.VITE_GIT_SHA || process.env.GITHUB_SHA || "development";
const builtAt = new Date().toISOString();

export default defineConfig({
  base: "/free-cross-device-audiobook/",
  define: {
    __APP_VERSION__: JSON.stringify(packageInfo.version),
    __BUILD_ID__: JSON.stringify(buildId),
    __BUILD_TIME__: JSON.stringify(builtAt),
  },
  plugins: [
    react(),
    {
      name: "build-version",
      generateBundle() {
        this.emitFile({
          type: "asset",
          fileName: "version.json",
          source: JSON.stringify({ version: packageInfo.version, build_id: buildId, built_at: builtAt }),
        });
      },
    },
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["seal.svg"],
      manifest: {
        name: "米兰读书",
        short_name: "米兰读书",
        description: "在 Mac 添加书，在手机和电脑边听边看。",
        lang: "zh-CN",
        theme_color: "#a43b2b",
        background_color: "#f2eadb",
        display: "standalone",
        start_url: "/free-cross-device-audiobook/",
        icons: [
          {
            src: "seal.svg",
            sizes: "any",
            type: "image/svg+xml",
            purpose: "any maskable",
          },
        ],
      },
    }),
  ],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: true,
  },
});
