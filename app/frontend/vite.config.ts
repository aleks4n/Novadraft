import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The web app talks to the Python backend (app/backend/main.py) on :8787.
// In dev we proxy /api there so the frontend can use same-origin relative URLs;
// the Tauri build does the same against a locally-running backend.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
