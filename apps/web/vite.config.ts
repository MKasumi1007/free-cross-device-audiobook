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
      includeAssets: [
        "app-icon-32.png",
        "app-icon-192.png",
        "app-icon-512.png",
        "app-icon-maskable-512.png",
        "apple-touch-icon.png",
      ],
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
            src: "app-icon-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "app-icon-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "app-icon-maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
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
