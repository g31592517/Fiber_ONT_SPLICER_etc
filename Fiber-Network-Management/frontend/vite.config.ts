import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the backend so the browser talks to one origin.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: { "/api": { target: process.env.FNMS_API_URL || "http://127.0.0.1:8000", changeOrigin: true } },
  },
  preview: { port: 4173, proxy: { "/api": { target: process.env.FNMS_API_URL || "http://127.0.0.1:8000", changeOrigin: true } } },
  build: { chunkSizeWarningLimit: 1500 },
});
