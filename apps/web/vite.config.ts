/// <reference types="vitest" />
import path from "node:path";
import { defineConfig } from "vite";
import { defaultExclude } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
    css: true,
    // Playwright e2e specs (Slice S8) live under e2e/ and run via
    // `npm run e2e`, not Vitest — Vitest's default include glob would
    // otherwise pick up *.spec.ts there and try to execute Playwright's
    // test() inside its own runner (incompatible; verified empirically).
    // Spread Vitest's own defaultExclude rather than replacing it — setting
    // `exclude` overrides (not merges with) the built-in list.
    exclude: [...defaultExclude, "e2e/**"],
  },
});
