import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "@/App";

// Mocked unconditionally at file scope — harmless for every existing
// (auth-disabled) test below, since supabase.ts only calls `createClient`
// when VITE_SUPABASE_URL/ANON_KEY are set, which none of those tests stub.
// Only the "App — auth enabled" describe block below (dynamic `import()`
// after `vi.stubEnv` + `vi.resetModules()`, matching useAuth.test.tsx's
// convention — App.tsx's static `import` of useAuth.tsx means env must be
// stubbed *before* a fresh module instance is created) actually exercises it.
const mockGetSession = vi.fn();
const mockOnAuthStateChange = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({
    auth: {
      getSession: mockGetSession,
      onAuthStateChange: mockOnAuthStateChange,
      signInWithPassword: vi.fn(),
      signOut: vi.fn(),
    },
  })),
}));

function buildJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: "none" }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.signature`;
}

/**
 * `App` self-wraps `HashRouter` (see App.tsx) — so unlike every other view's
 * test file (which wraps its subject in its own `MemoryRouter`), this file
 * drives navigation via `location.hash` directly and resets theme + storage
 * in `afterEach` so tests don't leak.
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
    localStorage.clear();
    document.documentElement.className = "";
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
      "Reports",
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

  it("deep-links to the Reports route via the URL hash", async () => {
    window.location.hash = "#/reports";
    stubPendingFetch();

    renderApp();

    await waitFor(() =>
      expect(screen.getByRole("link", { name: "Reports" })).toHaveAttribute("aria-current", "page"),
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

  it("renders a theme toggle that flips the .light class and its aria-label", async () => {
    stubPendingFetch();
    const user = userEvent.setup();

    renderApp();

    // Dark default: the button offers switching TO light.
    const toDark = () => screen.queryByRole("button", { name: /switch to dark theme/i });
    const toLight = () => screen.queryByRole("button", { name: /switch to light theme/i });

    expect(toLight()).toBeInTheDocument();
    expect(document.documentElement.classList.contains("light")).toBe(false);

    await user.click(toLight()!);

    expect(document.documentElement.classList.contains("light")).toBe(true);
    // aria-label now offers switching back to dark.
    expect(toDark()).toBeInTheDocument();
  });
});

describe("App — auth enabled", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
    vi.clearAllMocks();
    window.location.hash = "";
    localStorage.clear();
    document.documentElement.className = "";
  });

  it("renders Login (not the nav) when auth is enabled and there's no session", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.resetModules();
    mockGetSession.mockResolvedValue({ data: { session: null } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    stubPendingFetch();

    const { default: AuthedApp } = await import("@/App");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AuthedApp />
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /sign in/i })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("navigation", { name: "Primary" })).not.toBeInTheDocument();
  });

  it("renders the nav plus the user's email and Sign out when a session exists", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    // A mapped tenant is required for the nav to render — App.tsx gates to
    // Login whenever `tenantSlug` is null, even with a valid session (see
    // the "no tenant access" test below).
    vi.stubEnv("VITE_TENANT_SLUGS", JSON.stringify({ t1: "acme" }));
    vi.resetModules();
    const fakeSession = {
      access_token: buildJwt({ tenant_id: "t1" }),
      user: { email: "planner@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    stubPendingFetch();

    const { default: AuthedApp } = await import("@/App");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AuthedApp />
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument(),
    );
    expect(screen.getByText("planner@aeronta.test")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("renders the no-tenant-access screen (not the nav) when the session's tenant has no VITE_TENANT_SLUGS mapping", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    // No VITE_TENANT_SLUGS stubbed — any claims tenant_id resolves to `null`.
    vi.resetModules();
    const fakeSession = {
      access_token: buildJwt({ tenant_id: "unmapped-uuid" }),
      user: { email: "planner@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    stubPendingFetch();

    const { default: AuthedApp } = await import("@/App");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AuthedApp />
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /no tenant access/i })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("navigation", { name: "Primary" })).not.toBeInTheDocument();
  });
});
