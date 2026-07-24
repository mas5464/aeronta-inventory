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

/**
 * Like `stubPendingFetch`, but resolves `GET /v1/auth/whoami` immediately
 * with the given body/status — `useAuth`'s effect now resolves `tenantSlug`
 * from that route (C5 Task 8) rather than a build-time env map, so the
 * "auth enabled" tests below need it served to reach a settled tenant
 * state. Every OTHER request still hangs forever, preserving this file's
 * existing "every mounted view stays in its isPending state" strategy for
 * anything besides the auth gate itself. `status` defaults to 200 — the
 * 401 case (round-1 review fix: a signed-in user with zero tenant
 * memberships) passes 401 explicitly. Returns the `fetch` mock so callers
 * can assert on call counts (e.g. "no retry loop").
 */
function stubPendingFetchWithWhoami(whoamiBody: unknown, status = 200) {
  const fetchMock = vi.fn((url: unknown) =>
    typeof url === "string" && url.includes("/v1/auth/whoami")
      ? Promise.resolve(new Response(JSON.stringify(whoamiBody), { status }))
      : new Promise(() => {}),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/**
 * Like `stubPendingFetchWithWhoami`, but `GET /v1/auth/whoami` itself
 * REJECTS (a genuine network failure — `fetch()` throwing, not an HTTP
 * response with a non-2xx status) rather than resolving. Distinct from the
 * 401 case above: this must surface as `tenantStatus === "error"`, not
 * "no-tenant" (round-1 review fix — see useAuth.tsx's `TenantStatus`).
 */
function stubPendingFetchWithWhoamiError() {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: unknown) =>
      typeof url === "string" && url.includes("/v1/auth/whoami")
        ? Promise.reject(new TypeError("Failed to fetch"))
        : new Promise(() => {}),
    ),
  );
}

const acmeWhoami = {
  user_id: "u1",
  active: { tenant_uuid: "t1", slug: "acme", name: "Acme", role: "owner" },
  tenants: [{ tenant_uuid: "t1", slug: "acme", name: "Acme", role: "owner" }],
};

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
    // A whoami response with an active tenant is required for the nav to
    // render — App.tsx gates to Login whenever `tenantSlug` is null, even
    // with a valid session (see the "no tenant access" test below).
    vi.resetModules();
    const fakeSession = {
      access_token: buildJwt({ tenant_id: "t1" }),
      user: { email: "planner@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    stubPendingFetchWithWhoami(acmeWhoami);

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

  it("renders the no-tenant-access screen (not the nav) when whoami reports no active tenant", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.resetModules();
    const fakeSession = {
      access_token: buildJwt({ tenant_id: "unmapped-uuid" }),
      user: { email: "planner@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    // whoami reports no active tenant for this claim.
    stubPendingFetchWithWhoami({ user_id: "u1", active: null, tenants: [] });

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

  // --- Round-1 review fix: loading/no-tenant/error must be distinct ---
  //
  // The defect: `tenantSlug` started `null` and only became non-null once
  // `getWhoami()` resolved, but `null` was ALSO the value meaning "confirmed:
  // this user has no tenant" — the two states were indistinguishable, and
  // `session` turned truthy a render before the whoami effect settled. Every
  // login/reload rendered at least one frame of "No tenant access" for users
  // who DO have access. These three tests cover the fix's three distinct
  // states end to end through the real `AuthProvider` + `Login`.

  it(
    "renders a loading state — NOT the no-tenant-access message — while whoami is still in " +
      "flight for a real session",
    async () => {
      vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
      vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
      vi.resetModules();
      const fakeSession = {
        access_token: buildJwt({ tenant_id: "t1", tenant_role: "owner" }),
        user: { email: "owner@aeronta.test" },
      };
      mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
      mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
      // Every request hangs forever, whoami included — session is truthy but
      // tenant resolution never settles, which is exactly the window the
      // original bug rendered "No tenant access" for.
      stubPendingFetch();

      const { default: AuthedApp } = await import("@/App");
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(
        <QueryClientProvider client={client}>
          <AuthedApp />
        </QueryClientProvider>,
      );

      await waitFor(() =>
        expect(screen.getByRole("heading", { name: /loading your workspace/i })).toBeInTheDocument(),
      );
      expect(screen.queryByRole("heading", { name: /no tenant access/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("navigation", { name: "Primary" })).not.toBeInTheDocument();
    },
  );

  it(
    "renders the no-tenant-access screen when whoami returns 401, and calls whoami exactly " +
      "once (no retry loop)",
    async () => {
      vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
      vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
      vi.resetModules();
      // A perfectly valid session — the account just isn't a member of any
      // tenant yet, so its JWT carries no tenant_id claim, and the BFF's
      // AuthMiddleware 401s the whoami request itself.
      const fakeSession = {
        access_token: buildJwt({ sub: "orphan-user" }),
        user: { email: "orphan@aeronta.test" },
      };
      mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
      mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
      const fetchMock = stubPendingFetchWithWhoami({ detail: "missing or invalid token" }, 401);

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
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it(
    "renders an error state with a retry affordance — NOT the no-tenant-access message — when " +
      "whoami's request fails outright (network rejection)",
    async () => {
      vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
      vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
      vi.resetModules();
      const fakeSession = {
        access_token: buildJwt({ tenant_id: "t1", tenant_role: "planner" }),
        user: { email: "planner@aeronta.test" },
      };
      mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
      mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
      stubPendingFetchWithWhoamiError();

      const { default: AuthedApp } = await import("@/App");
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(
        <QueryClientProvider client={client}>
          <AuthedApp />
        </QueryClientProvider>,
      );

      await waitFor(() =>
        expect(screen.getByRole("heading", { name: /couldn.t load your workspace/i })).toBeInTheDocument(),
      );
      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: /no tenant access/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("navigation", { name: "Primary" })).not.toBeInTheDocument();
    },
  );

  it("does not render a Members nav entry for a planner role (C2 Task 7 nav gating)", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.resetModules();
    const fakeSession = {
      access_token: buildJwt({ tenant_id: "t1", tenant_role: "planner" }),
      user: { email: "planner@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    stubPendingFetchWithWhoami(acmeWhoami);

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
    expect(screen.queryByRole("link", { name: "Members" })).not.toBeInTheDocument();
  });

  it("renders a Members nav entry for an owner role (C2 Task 7 nav gating)", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.resetModules();
    const fakeSession = {
      access_token: buildJwt({ tenant_id: "t1", tenant_role: "owner" }),
      user: { email: "owner@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    stubPendingFetchWithWhoami(acmeWhoami);

    const { default: AuthedApp } = await import("@/App");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AuthedApp />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByRole("link", { name: "Members" })).toBeInTheDocument());
  });

  it("an admin role also renders the Members nav entry (admin OR owner gate, not owner-only)", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.resetModules();
    const fakeSession = {
      access_token: buildJwt({ tenant_id: "t1", tenant_role: "admin" }),
      user: { email: "admin@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    stubPendingFetchWithWhoami(acmeWhoami);

    const { default: AuthedApp } = await import("@/App");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AuthedApp />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByRole("link", { name: "Members" })).toBeInTheDocument());
  });

  it("does not render a Billing nav entry for a planner role (C4 Task 10 nav gating)", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.resetModules();
    const fakeSession = {
      access_token: buildJwt({ tenant_id: "t1", tenant_role: "planner" }),
      user: { email: "planner@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    stubPendingFetchWithWhoami(acmeWhoami);

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
    expect(screen.queryByRole("link", { name: "Billing" })).not.toBeInTheDocument();
  });

  it("does not render a Billing nav entry for an admin role (Billing is owner-only, stricter than Members' admin-or-owner gate)", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.resetModules();
    const fakeSession = {
      access_token: buildJwt({ tenant_id: "t1", tenant_role: "admin" }),
      user: { email: "admin@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    stubPendingFetchWithWhoami(acmeWhoami);

    const { default: AuthedApp } = await import("@/App");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AuthedApp />
      </QueryClientProvider>,
    );

    // Admin still gets Members (admin-or-owner gate) — confirms the shell
    // rendered normally — but must NOT get Billing (owner-only gate).
    await waitFor(() => expect(screen.getByRole("link", { name: "Members" })).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: "Billing" })).not.toBeInTheDocument();
  });

  it("renders a Billing nav entry for an owner role (C4 Task 10 nav gating)", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.resetModules();
    const fakeSession = {
      access_token: buildJwt({ tenant_id: "t1", tenant_role: "owner" }),
      user: { email: "owner@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    stubPendingFetchWithWhoami(acmeWhoami);

    const { default: AuthedApp } = await import("@/App");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AuthedApp />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByRole("link", { name: "Billing" })).toBeInTheDocument());
  });
});
