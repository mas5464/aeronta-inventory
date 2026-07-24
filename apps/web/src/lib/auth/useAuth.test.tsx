import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

/**
 * Follows the fetch/env-stub conventions of the neighboring
 * `src/lib/api/client.test.ts` (`vi.stubGlobal`, `vi.stubEnv`) — but
 * `supabase.ts`/`useAuth.tsx` read `import.meta.env.VITE_SUPABASE_*` at
 * MODULE LOAD time, so every scenario needs a fresh module instance:
 * `vi.stubEnv(...)` then `vi.resetModules()` then a dynamic `import()`,
 * rather than the static top-of-file import client.test.ts uses (its
 * BASE_URL default doesn't vary across cases the way auth-enabled/disabled
 * does here).
 */

const mockGetSession = vi.fn();
const mockOnAuthStateChange = vi.fn();
const mockSignInWithPassword = vi.fn();
const mockSignOut = vi.fn();

vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({
    auth: {
      getSession: mockGetSession,
      onAuthStateChange: mockOnAuthStateChange,
      signInWithPassword: mockSignInWithPassword,
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
 * Regression helper for the base64url decode fix (`claimsOf` in
 * useAuth.tsx): real Supabase-issued JWTs base64url-encode the payload
 * (`-`/`_` in place of `+`/`/`, RFC 7519 §3) — the opposite of `buildJwt`
 * above, which only works as a test fixture because its payloads happen not
 * to trip the +//- distinction.
 */
function buildBase64UrlJwt(payload: Record<string, unknown>): string {
  const toB64Url = (obj: Record<string, unknown>) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_");
  return `${toB64Url({ alg: "none" })}.${toB64Url(payload)}.signature`;
}

type ClientModule = typeof import("@/lib/api/client");
type AuthModule = typeof import("@/lib/auth/useAuth");

async function loadAuth(): Promise<{ clientMod: ClientModule; authMod: AuthModule }> {
  const clientMod = await import("@/lib/api/client");
  const authMod = await import("@/lib/auth/useAuth");
  return { clientMod, authMod };
}

/**
 * Stubs global `fetch` to resolve EVERY request with the given JSON body —
 * used to serve the `GET /v1/auth/whoami` call `useAuth`'s effect fires on
 * every session change (C5 Task 8). Tests that also need to observe a
 * *different* subsequent request (e.g. a dashboard call) install their own
 * `vi.stubGlobal("fetch", ...)` afterward, which simply replaces this one.
 */
function stubFetchResolving(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** A probe component rendering every `useAuth()` field as text + sign-in/out/retry buttons. */
function makeAuthProbe(authMod: AuthModule) {
  return function AuthProbe() {
    const {
      session,
      authEnabled,
      tenantSlug,
      tenants,
      tenantStatus,
      role,
      email,
      signIn,
      signOut,
      retryTenantResolution,
    } = authMod.useAuth();
    const [signInError, setSignInError] = useState<string | null>(null);
    return (
      <div>
        <span data-testid="authEnabled">{String(authEnabled)}</span>
        <span data-testid="session">{session ? "yes" : "no"}</span>
        <span data-testid="tenantSlug">{tenantSlug ?? "none"}</span>
        <span data-testid="tenants">{JSON.stringify(tenants.map((t) => t.slug))}</span>
        <span data-testid="tenantStatus">{tenantStatus}</span>
        <span data-testid="role">{role ?? "none"}</span>
        <span data-testid="email">{email ?? "none"}</span>
        <span data-testid="signInError">{signInError ?? "none"}</span>
        <button
          onClick={() => {
            void signIn("planner@aeronta.test", "hunter2").then((r) => setSignInError(r.error));
          }}
        >
          sign in
        </button>
        <button onClick={() => void signOut()}>sign out</button>
        <button onClick={() => retryTenantResolution()}>retry</button>
      </div>
    );
  };
}

describe("useAuth", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("authEnabled is false and session is null when no VITE_SUPABASE_* env is set (dev-mode invariant)", async () => {
    vi.resetModules();
    const { authMod } = await loadAuth();
    const AuthProbe = makeAuthProbe(authMod);

    render(
      <authMod.AuthProvider>
        <AuthProbe />
      </authMod.AuthProvider>,
    );

    expect(screen.getByTestId("authEnabled")).toHaveTextContent("false");
    expect(screen.getByTestId("session")).toHaveTextContent("no");
    expect(screen.getByTestId("tenantStatus")).toHaveTextContent("idle");
  });

  it("signIn returns 'auth disabled' when auth is disabled", async () => {
    vi.resetModules();
    const { authMod } = await loadAuth();
    const AuthProbe = makeAuthProbe(authMod);
    const user = userEvent.setup();

    render(
      <authMod.AuthProvider>
        <AuthProbe />
      </authMod.AuthProvider>,
    );

    await user.click(screen.getByRole("button", { name: "sign in" }));

    await waitFor(() =>
      expect(screen.getByTestId("signInError")).toHaveTextContent("auth disabled"),
    );
    expect(mockSignInWithPassword).not.toHaveBeenCalled();
  });

  it(
    "signIn success (mocked supabase client) populates session, resolves tenantSlug + tenants " +
      "via GET /v1/auth/whoami, and the token is attached to both that call and subsequent requests",
    async () => {
      vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
      vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
      vi.resetModules();

      const { clientMod, authMod } = await loadAuth();
      const AuthProbe = makeAuthProbe(authMod);
      const user = userEvent.setup();

      const fakeToken = buildJwt({
        sub: "user-1",
        tenant_id: "753b64bd-9885-4639-b116-8f2c5c497232",
        tenant_role: "planner",
      });
      const fakeSession = { access_token: fakeToken, user: { email: "planner@aeronta.test" } };
      const whoamiTenant = {
        tenant_uuid: "753b64bd-9885-4639-b116-8f2c5c497232",
        slug: "aeronta-demo",
        name: "Aeronta Demo",
        role: "planner",
      };

      let authChangeCallback: ((event: string, session: unknown) => void) | undefined;
      mockGetSession.mockResolvedValue({ data: { session: null } });
      mockOnAuthStateChange.mockImplementation(
        (cb: (event: string, session: unknown) => void) => {
          authChangeCallback = cb;
          return { data: { subscription: { unsubscribe: vi.fn() } } };
        },
      );
      mockSignInWithPassword.mockImplementation(async () => {
        authChangeCallback?.("SIGNED_IN", fakeSession);
        return { error: null };
      });
      const whoamiFetchMock = stubFetchResolving({
        user_id: "user-1",
        active: whoamiTenant,
        tenants: [whoamiTenant],
      });

      render(
        <authMod.AuthProvider>
          <AuthProbe />
        </authMod.AuthProvider>,
      );

      expect(screen.getByTestId("authEnabled")).toHaveTextContent("true");

      await user.click(screen.getByRole("button", { name: "sign in" }));

      await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("yes"));
      await waitFor(() => expect(screen.getByTestId("tenantSlug")).toHaveTextContent("aeronta-demo"));
      expect(screen.getByTestId("tenantStatus")).toHaveTextContent("ready");
      expect(screen.getByTestId("tenants")).toHaveTextContent('["aeronta-demo"]');
      expect(screen.getByTestId("role")).toHaveTextContent("planner");
      expect(screen.getByTestId("email")).toHaveTextContent("planner@aeronta.test");
      expect(screen.getByTestId("signInError")).toHaveTextContent("none");
      expect(clientMod.activeTenant()).toBe("aeronta-demo");

      // setAccessToken runs before getWhoami() in the same effect, so the
      // whoami call itself already carried the bearer token.
      const whoamiCall = whoamiFetchMock.mock.calls.find(([url]) =>
        String(url).includes("/v1/auth/whoami"),
      );
      expect(whoamiCall?.[1]).toEqual(
        expect.objectContaining({
          headers: expect.objectContaining({ Authorization: `Bearer ${fakeToken}` }),
        }),
      );

      // ...and so does an unrelated, subsequent request (black-box, matching
      // client.test.ts's fetch-stub style rather than spying on an ES
      // module's internal binding).
      const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
      vi.stubGlobal("fetch", fetchMock);
      await clientMod.bffClient.getDashboard("aeronta-demo");
      expect(fetchMock).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          headers: expect.objectContaining({ Authorization: `Bearer ${fakeToken}` }),
        }),
      );
    },
  );

  it("tenantSlug is null when whoami reports no active tenant (e.g. a stale/unmapped claim)", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.resetModules();

    const { authMod } = await loadAuth();
    const AuthProbe = makeAuthProbe(authMod);

    const fakeSession = {
      access_token: buildJwt({ tenant_id: "unmapped-uuid", tenant_role: "planner" }),
      user: { email: "planner@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    stubFetchResolving({ user_id: "u1", active: null, tenants: [] });

    render(
      <authMod.AuthProvider>
        <AuthProbe />
      </authMod.AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("yes"));
    await waitFor(() => expect(screen.getByTestId("tenantSlug")).toHaveTextContent("none"));
    expect(screen.getByTestId("tenants")).toHaveTextContent("[]");
    expect(screen.getByTestId("tenantStatus")).toHaveTextContent("no-tenant");
  });

  it(
    "tenantStatus is 'loading' (not 'no-tenant') while whoami is in flight, before it settles " +
      "(review fix, C5 Task 8 round 1 — tenantSlug is null in both states, so a status distinct " +
      "from tenantSlug is required to tell them apart)",
    async () => {
      vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
      vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
      vi.resetModules();

      const { authMod } = await loadAuth();
      const AuthProbe = makeAuthProbe(authMod);

      const fakeSession = {
        access_token: buildJwt({ tenant_id: "t1", tenant_role: "planner" }),
        user: { email: "planner@aeronta.test" },
      };
      mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
      mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
      // A promise that never resolves — holds the whoami request in flight
      // for the lifetime of this test, matching this suite's existing
      // convention for "stay pending" (see App.test.tsx's stubPendingFetch).
      vi.stubGlobal(
        "fetch",
        vi.fn().mockReturnValue(new Promise(() => {})),
      );

      render(
        <authMod.AuthProvider>
          <AuthProbe />
        </authMod.AuthProvider>,
      );

      await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("yes"));
      expect(screen.getByTestId("tenantStatus")).toHaveTextContent("loading");
      expect(screen.getByTestId("tenantSlug")).toHaveTextContent("none");
    },
  );

  it(
    "a 401 from whoami (a signed-in user with ZERO tenant memberships — the BFF's " +
      "AuthMiddleware rejects any authed request whose JWT lacks a tenant_id claim, which the " +
      "claims hook omits entirely for such a user) degrades to tenantSlug: null / tenants: [] " +
      "without throwing, retrying, or looping",
    async () => {
      vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
      vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
      vi.resetModules();

      const { authMod } = await loadAuth();
      const AuthProbe = makeAuthProbe(authMod);

      // A perfectly valid, signed-in session — the account just isn't a
      // member of any tenant yet, so its JWT carries no tenant_id claim.
      const fakeSession = {
        access_token: buildJwt({ sub: "orphan-user" }),
        user: { email: "orphan@aeronta.test" },
      };
      mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
      mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
      // A 401 also dispatches client.ts's "aeronta:unauthorized" (existing,
      // unrelated behavior — this provider signs out on ANY 401 anywhere in
      // the app); stub it so that doesn't reject/throw in this test.
      mockSignOut.mockResolvedValue({ error: null });
      const fetchMock = stubFetchResolving({ detail: "missing or invalid token" }, 401);

      render(
        <authMod.AuthProvider>
          <AuthProbe />
        </authMod.AuthProvider>,
      );

      await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("yes"));
      await waitFor(() => expect(screen.getByTestId("tenantSlug")).toHaveTextContent("none"));
      expect(screen.getByTestId("tenants")).toHaveTextContent("[]");
      // A 401 is a CONFIRMED "no tenant" answer, same as a 200 with
      // `active: null` — not the "error" status (see the next test).
      expect(screen.getByTestId("tenantStatus")).toHaveTextContent("no-tenant");

      // No retry loop: whoami was attempted exactly once for this session.
      expect(fetchMock).toHaveBeenCalledTimes(1);
    },
  );

  it(
    "a non-401 whoami failure (e.g. a network rejection) sets tenantStatus to 'error' — " +
      "distinct from 'no-tenant' — and retryTenantResolution() re-fetches exactly once per " +
      "click (no automatic retry loop), reaching 'ready' on a successful retry",
    async () => {
      vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
      vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
      vi.resetModules();

      const { authMod } = await loadAuth();
      const AuthProbe = makeAuthProbe(authMod);
      const user = userEvent.setup();

      const fakeSession = {
        access_token: buildJwt({ tenant_id: "t1", tenant_role: "planner" }),
        user: { email: "planner@aeronta.test" },
      };
      mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
      mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });

      // A genuine network failure — fetch() itself rejects, unlike a 401
      // (an HTTP response with a non-2xx status). This must NOT collapse
      // into "no-tenant": that would blame the user's permissions for a
      // transient failure that has nothing to do with them.
      const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch"));
      vi.stubGlobal("fetch", fetchMock);

      render(
        <authMod.AuthProvider>
          <AuthProbe />
        </authMod.AuthProvider>,
      );

      await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("yes"));
      await waitFor(() => expect(screen.getByTestId("tenantStatus")).toHaveTextContent("error"));
      expect(screen.getByTestId("tenantSlug")).toHaveTextContent("none");
      expect(fetchMock).toHaveBeenCalledTimes(1);

      // A second automatic call never happens on its own — only a
      // user-initiated retry (the "retry" button, wired to
      // retryTenantResolution()) fetches again.
      const whoamiTenant = {
        tenant_uuid: "t1",
        slug: "aeronta-demo",
        name: "Aeronta Demo",
        role: "planner",
      };
      fetchMock.mockResolvedValueOnce(
        new Response(
          JSON.stringify({ user_id: "u1", active: whoamiTenant, tenants: [whoamiTenant] }),
          { status: 200 },
        ),
      );

      await user.click(screen.getByRole("button", { name: "retry" }));

      await waitFor(() => expect(screen.getByTestId("tenantStatus")).toHaveTextContent("ready"));
      expect(screen.getByTestId("tenantSlug")).toHaveTextContent("aeronta-demo");
      expect(fetchMock).toHaveBeenCalledTimes(2);
    },
  );

  it("signOut clears the session and the access token (activeTenant reverts to DEFAULT_TENANT)", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.resetModules();

    const { clientMod, authMod } = await loadAuth();
    const AuthProbe = makeAuthProbe(authMod);
    const user = userEvent.setup();

    const fakeSession = {
      access_token: buildJwt({ tenant_id: "t1" }),
      user: { email: "planner@aeronta.test" },
    };

    let authChangeCallback: ((event: string, session: unknown) => void) | undefined;
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockImplementation((cb: (event: string, session: unknown) => void) => {
      authChangeCallback = cb;
      return { data: { subscription: { unsubscribe: vi.fn() } } };
    });
    mockSignOut.mockImplementation(async () => {
      authChangeCallback?.("SIGNED_OUT", null);
      return { error: null };
    });
    // The session is truthy from the very first render (getSession()
    // resolves with it, no sign-in click needed), so useAuth's whoami effect
    // fires immediately on mount — stub it so that's not a real network call.
    stubFetchResolving({ user_id: "u1", active: null, tenants: [] });

    render(
      <authMod.AuthProvider>
        <AuthProbe />
      </authMod.AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("yes"));

    await user.click(screen.getByRole("button", { name: "sign out" }));

    await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("no"));
    expect(screen.getByTestId("email")).toHaveTextContent("none");
    expect(screen.getByTestId("tenantStatus")).toHaveTextContent("idle");
    expect(clientMod.activeTenant()).toBe(clientMod.DEFAULT_TENANT);

    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal("fetch", fetchMock);
    await clientMod.bffClient.getDashboard("acme");
    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });

  it(
    "decodes the role claim correctly when the JWT payload is base64url-encoded with '-'/'_' " +
      "characters (real-world JWT format, not plain base64)",
    async () => {
      vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
      vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
      vi.resetModules();

      const { authMod } = await loadAuth();
      const AuthProbe = makeAuthProbe(authMod);

      // This payload's plain-base64 encoding is verified to contain a `+`
      // (`btoa(JSON.stringify(payload))` ends "...LCJ4IjoiICA+In0="), so its
      // base64url form below swaps that `+` for `-` — reproducing the exact
      // byte pattern plain `atob` cannot handle.
      const fakeSession = {
        access_token: buildBase64UrlJwt({
          tenant_id: "753b64bd-9885-4639-b116-8f2c5c497232",
          tenant_role: "planner",
          x: "  >",
        }),
        user: { email: "planner@aeronta.test" },
      };
      mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
      mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
      const whoamiTenant = {
        tenant_uuid: "753b64bd-9885-4639-b116-8f2c5c497232",
        slug: "aeronta-demo",
        name: "Aeronta Demo",
        role: "planner",
      };
      stubFetchResolving({ user_id: "u1", active: whoamiTenant, tenants: [whoamiTenant] });

      render(
        <authMod.AuthProvider>
          <AuthProbe />
        </authMod.AuthProvider>,
      );

      await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("yes"));
      await waitFor(() => expect(screen.getByTestId("tenantSlug")).toHaveTextContent("aeronta-demo"));
      expect(screen.getByTestId("role")).toHaveTextContent("planner");
    },
  );

  it("an aeronta:unauthorized event (dispatched by client.ts on a 401) signs the user out via supabase", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.resetModules();

    const { authMod } = await loadAuth();
    const AuthProbe = makeAuthProbe(authMod);

    mockGetSession.mockResolvedValue({ data: { session: null } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });
    mockSignOut.mockResolvedValue({ error: null });

    render(
      <authMod.AuthProvider>
        <AuthProbe />
      </authMod.AuthProvider>,
    );

    await waitFor(() => expect(mockGetSession).toHaveBeenCalled());
    expect(mockSignOut).not.toHaveBeenCalled();

    window.dispatchEvent(new Event("aeronta:unauthorized"));

    await waitFor(() => expect(mockSignOut).toHaveBeenCalled());
  });
});
