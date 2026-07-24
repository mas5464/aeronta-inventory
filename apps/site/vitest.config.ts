/// <reference types="vitest" />
// apps/site/vitest.config.ts — mirrors apps/web's vite.config.ts test block.
// Astro's own build/dev pipeline is driven by astro.config.mjs; this file is
// Vitest-only, for the React island unit tests (ContactForm.test.tsx). It
// never touches Astro's compiler — .astro files aren't imported by these tests.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
  },
});
