import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  base: "/free-cross-device-audiobook/",
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["seal.svg"],
      manifest: {
        name: "听见书页",
        short_name: "听见书页",
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
