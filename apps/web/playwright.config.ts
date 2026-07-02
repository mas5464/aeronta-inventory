import { defineConfig, devices } from "@playwright/test";

/**
 * Slice S8 hardening — best-effort e2e (per the slice scope, item 5).
 * Runs against the real Vite dev server (`npm run dev`); the BFF is NOT
 * started — every spec mocks `/v1/**` at the browser level via
 * `page.route()` (see `e2e/workbench-accept.spec.ts`), so this suite never
 * depends on a running BFF or Docker.
 */
// A dedicated, unlikely-to-collide port — this repo's dev convention (`npm
// run dev`) defaults to Vite's own 5173, which may already be in use by an
// unrelated project's dev server on a shared machine (verified: it was,
// during this slice's own build — do NOT default to 5173 here).
const PORT = 5190;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },
  webServer: {
    command: `npm run dev -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    // Never reuse an already-running server on this port — a stray server
    // (this repo's own `npm run dev`, or an unrelated project) must not be
    // mistaken for this suite's dedicated instance.
    reuseExistingServer: false,
    timeout: 30_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
