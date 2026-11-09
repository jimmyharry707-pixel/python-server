import { defineConfig, loadEnv } from "vite";
import { resolve } from "node:path";
import react from "@vitejs/plugin-react";

const rootDir = import.meta.dirname;

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, rootDir, "");
  const proxyTarget =
    env.VITE_PROXY_TARGET ||
    (mode === "production-backend" ? "https://app.example.com" : "http://127.0.0.1:8000");
  const proxy = {
    "/api": { target: proxyTarget, changeOrigin: true, secure: true },
    "/meta": { target: proxyTarget, changeOrigin: true, secure: true },
    "/uploads": { target: proxyTarget, changeOrigin: true, secure: true },
  };

  return {
    plugins: [react()],
    base: "/",
    css: {
      // Keep unprefixed backdrop-filter (LightningCSS otherwise drops it)
      lightningcss: {
        targets: {
          chrome: 111 << 16,
          firefox: 111 << 16,
          safari: 16 << 16,
        },
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
      rollupOptions: {
        input: {
          main: resolve(rootDir, "index.html"),
        },
      },
    },
    server: {
      port: 5173,
      proxy,
    },
    preview: {
      port: 4173,
      proxy,
    },
  };
});
