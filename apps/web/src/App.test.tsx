import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
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
const mockSignOut = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({
    auth: {
      getSession: mockGetSession,
      onAuthStateChange: mockOnAuthStateChange,
      signInWithPassword: vi.fn(),
      signOut: mockSignOut,
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

    expect(screen.getByRole("heading", { name: "Aeronta Inventory" })).toBeInTheDocument();
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

  it("renders a theme toggle that flips the .dark class and its aria-label", async () => {
    stubPendingFetch();
    const user = userEvent.setup();

    renderApp();

    // Light default (Aeronta parent brand): the button offers switching TO dark.
    const toDark = () => screen.queryByRole("button", { name: /switch to dark theme/i });
    const toLight = () => screen.queryByRole("button", { name: /switch to light theme/i });

    expect(toDark()).toBeInTheDocument();
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    await user.click(toDark()!);

    expect(document.documentElement.classList.contains("dark")).toBe(true);
    // aria-label now offers switching back to light.
    expect(toLight()).toBeInTheDocument();
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
    "renders the no-tenant-access screen when whoami returns 401, calls whoami exactly once " +
      "(no retry loop), and — Group A review fix — does NOT sign the user out, so the card " +
      "STAYS rendered instead of being replaced by the sign-in screen a beat later (force-" +
      "signing-out a brand-new signup mid-onboarding defeated C5's whole purpose and made this " +
      "card unreachable in production)",
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
      let authChangeCallback: ((event: string, session: unknown) => void) | undefined;
      mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
      mockOnAuthStateChange.mockImplementation(
        (cb: (event: string, session: unknown) => void) => {
          authChangeCallback = cb;
          return { data: { subscription: { unsubscribe: vi.fn() } } };
        },
      );
      // Wired all the way through — unlike the bare `vi.fn()` this suite
      // used to leave in the mocked supabase client — so that IF the code
      // under test wrongly signed out for this whoami 401, the test would
      // observe the real consequence (Login replacing this screen) instead
      // of silently passing regardless. This is the exact masking the
      // Group A review fix corrects.
      mockSignOut.mockImplementation(async () => {
        authChangeCallback?.("SIGNED_OUT", null);
        return { error: null };
      });
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
      expect(mockSignOut).not.toHaveBeenCalled();

      // Flush a macrotask so any wrongly-scheduled async signOut chain
      // would have landed before this assertion — the card must STAY, not
      // get clobbered a beat later by a sign-out that should never fire.
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(screen.getByRole("heading", { name: /no tenant access/i })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: /sign in/i })).not.toBeInTheDocument();
      expect(mockSignOut).not.toHaveBeenCalled();
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

  // --- Round-2 review fix: a same-identity auth event must not reset ---
  //
  // The defect: round 1's `applySession()` correctly batched the
  // tenant-resolution reset with `session` itself, but `onAuthStateChange`'s
  // callback called it UNCONDITIONALLY for every event — including the
  // Supabase client's own background `TOKEN_REFRESHED` (autoRefreshToken is
  // on by default with no options overriding it — see supabase.ts). For any
  // actively-open session, that forced tenantSlug/tenantStatus back to
  // null/"loading" roughly once per token lifetime, tripping AppShell's gate
  // and unmounting the whole app behind "Loading your workspace" for no
  // reason. This test proves the app shell survives a same-identity event
  // once it has already reached "ready".

  it(
    "a same-identity onAuthStateChange event (e.g. a background TOKEN_REFRESHED) leaves the app " +
      "shell mounted (nav still visible, no Login screen) and does not re-fetch whoami",
    async () => {
      vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
      vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
      vi.resetModules();
      const fakeSession = {
        access_token: buildJwt({ tenant_id: "t1", tenant_role: "owner" }),
        user: { id: "user-1", email: "owner@aeronta.test" },
      };
      // A real TOKEN_REFRESHED swaps the access_token but keeps the SAME
      // user — the fix's identity check is `session.user.id`, not the token.
      const refreshedSession = {
        access_token: buildJwt({ tenant_id: "t1", tenant_role: "owner", refreshed: true }),
        user: { id: "user-1", email: "owner@aeronta.test" },
      };

      let authChangeCallback: ((event: string, session: unknown) => void) | undefined;
      mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
      mockOnAuthStateChange.mockImplementation(
        (cb: (event: string, session: unknown) => void) => {
          authChangeCallback = cb;
          return { data: { subscription: { unsubscribe: vi.fn() } } };
        },
      );
      const fetchMock = stubPendingFetchWithWhoami(acmeWhoami);
      // Overview (the default route, rendered once the nav mounts) fires
      // its OWN dashboard/feed queries against the same `fetchMock` — they
      // hang forever (stubPendingFetchWithWhoami's convention) but still
      // COUNT as calls, so a raw `toHaveBeenCalledTimes` would conflate
      // "whoami re-fetched" with "Overview's other queries fired". Filter
      // to whoami's own URL specifically, matching the `whoamiCall`
      // filtering pattern already used in useAuth.test.tsx.
      const whoamiCallCount = () =>
        fetchMock.mock.calls.filter(
          ([url]) => typeof url === "string" && url.includes("/v1/auth/whoami"),
        ).length;

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
      expect(whoamiCallCount()).toBe(1);

      await act(async () => {
        authChangeCallback?.("TOKEN_REFRESHED", refreshedSession);
      });

      // Still mounted — the nav never disappeared behind Login's loading
      // card, and whoami was NOT re-fetched for a same-identity event.
      expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: /loading your workspace/i }),
      ).not.toBeInTheDocument();
      expect(screen.getByText("owner@aeronta.test")).toBeInTheDocument();
      expect(whoamiCallCount()).toBe(1);
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
