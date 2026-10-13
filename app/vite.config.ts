import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server: port 5173, /api proxied to the stdlib mock service (loopback only).
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1", // loopback only — never expose the scanner UI
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:18741",
        changeOrigin: true,
      },
    },
  },
});
