import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Vitest config (takes precedence over vite.config.ts): jsdom + the socket tripwire setup.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
