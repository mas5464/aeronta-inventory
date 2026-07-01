import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "@/App";

/**
 * `App` self-wraps `HashRouter` (see App.tsx) — so unlike every other view's
 * test file (which wraps its subject in its own `MemoryRouter`), this file
 * drives navigation via `location.hash` directly and resets it in
 * `afterEach` (same convention as apps/planner-ui's `App.test.tsx`).
 *
 * This file only exercises the app shell (nav + routing), not any one
 * view's content — every view's own test file already covers its
 * loading/error/data rendering in isolation. So the `fetch` stub here
 * deliberately never resolves, keeping every mounted view in its
 * `isPending` state throughout (each view's loading render is already
 * proven safe by its own test file) rather than risking a shape-mismatched
 * fake payload crashing some view's post-load render.
 */
function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

function stubPendingFetch() {
  vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));
}

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.location.hash = "";
  });

  it("renders the header and every nav item", () => {
    stubPendingFetch();

    renderApp();

    expect(screen.getByRole("heading", { name: "Trax Inventory Optimizer" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const label of [
      "Overview",
      "Workbench",
      "AI Recommendations",
      "Forecast & Service Levels",
      "What-If Scenarios",
      "Data & Connections",
    ]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
    expect(nav).toBeInTheDocument();
  });

  it("marks the active nav item with aria-current=page (WCAG 2.1 AA §4.1.2), and only the active one", () => {
    stubPendingFetch();

    renderApp();

    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Workbench" })).not.toHaveAttribute("aria-current");
    expect(screen.getByRole("link", { name: "AI Recommendations" })).not.toHaveAttribute("aria-current");
  });

  it("clicking a nav item navigates (updates the URL hash) and moves aria-current to it", async () => {
    stubPendingFetch();
    const user = userEvent.setup();

    renderApp();

    await user.click(screen.getByRole("link", { name: "Workbench" }));

    await waitFor(() => expect(window.location.hash).toBe("#/workbench"));
    expect(screen.getByRole("link", { name: "Workbench" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("deep-links directly to a non-root route via the URL hash", async () => {
    window.location.hash = "#/data";
    stubPendingFetch();

    renderApp();

    await waitFor(() =>
      expect(screen.getByRole("link", { name: "Data & Connections" })).toHaveAttribute(
        "aria-current",
        "page",
      ),
    );
  });

  it("every nav item is keyboard-focusable with a visible focus-visible ring class", () => {
    stubPendingFetch();

    renderApp();

    for (const label of ["Overview", "Workbench", "AI Recommendations"]) {
      const link = screen.getByRole("link", { name: label });
      expect(link.className).toMatch(/focus-visible:ring-2/);
      expect(link.tabIndex).not.toBe(-1);
    }
  });
});
