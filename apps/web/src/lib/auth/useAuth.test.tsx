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

/** A probe component rendering every `useAuth()` field as text + sign-in/out buttons. */
function makeAuthProbe(authMod: AuthModule) {
  return function AuthProbe() {
    const { session, authEnabled, tenantSlug, role, email, signIn, signOut } = authMod.useAuth();
    const [signInError, setSignInError] = useState<string | null>(null);
    return (
      <div>
        <span data-testid="authEnabled">{String(authEnabled)}</span>
        <span data-testid="session">{session ? "yes" : "no"}</span>
        <span data-testid="tenantSlug">{tenantSlug ?? "none"}</span>
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
    "signIn success (mocked supabase client) populates session, decodes tenantSlug " +
      "via VITE_TENANT_SLUGS, and the token is attached to subsequent requests",
    async () => {
      vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
      vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
      vi.stubEnv(
        "VITE_TENANT_SLUGS",
        JSON.stringify({ "753b64bd-9885-4639-b116-8f2c5c497232": "aeronta-demo" }),
      );
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

      render(
        <authMod.AuthProvider>
          <AuthProbe />
        </authMod.AuthProvider>,
      );

      expect(screen.getByTestId("authEnabled")).toHaveTextContent("true");

      await user.click(screen.getByRole("button", { name: "sign in" }));

      await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("yes"));
      expect(screen.getByTestId("tenantSlug")).toHaveTextContent("aeronta-demo");
      expect(screen.getByTestId("role")).toHaveTextContent("planner");
      expect(screen.getByTestId("email")).toHaveTextContent("planner@aeronta.test");
      expect(screen.getByTestId("signInError")).toHaveTextContent("none");
      expect(clientMod.activeTenant()).toBe("aeronta-demo");

      // setAccessToken was called with the decoded token — verify via a real
      // request<T>() call attaching the Authorization header (black-box,
      // matching client.test.ts's fetch-stub style rather than spying on an
      // ES module's internal binding).
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

  it("tenantSlug is null when the claims uuid has no VITE_TENANT_SLUGS mapping", async () => {
    vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
    vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
    vi.stubEnv("VITE_TENANT_SLUGS", JSON.stringify({ "known-uuid": "acme-demo" }));
    vi.resetModules();

    const { authMod } = await loadAuth();
    const AuthProbe = makeAuthProbe(authMod);

    const fakeSession = {
      access_token: buildJwt({ tenant_id: "unmapped-uuid", tenant_role: "planner" }),
      user: { email: "planner@aeronta.test" },
    };
    mockGetSession.mockResolvedValue({ data: { session: fakeSession } });
    mockOnAuthStateChange.mockReturnValue({ data: { subscription: { unsubscribe: vi.fn() } } });

    render(
      <authMod.AuthProvider>
        <AuthProbe />
      </authMod.AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("yes"));
    expect(screen.getByTestId("tenantSlug")).toHaveTextContent("none");
  });

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

    render(
      <authMod.AuthProvider>
        <AuthProbe />
      </authMod.AuthProvider>,
    );

    await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("yes"));

    await user.click(screen.getByRole("button", { name: "sign out" }));

    await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("no"));
    expect(screen.getByTestId("email")).toHaveTextContent("none");
    expect(clientMod.activeTenant()).toBe(clientMod.DEFAULT_TENANT);

    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal("fetch", fetchMock);
    await clientMod.bffClient.getDashboard("acme");
    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });

  it(
    "decodes claims correctly when the JWT payload is base64url-encoded with '-'/'_' " +
      "characters (real-world JWT format, not plain base64)",
    async () => {
      vi.stubEnv("VITE_SUPABASE_URL", "https://project.supabase.co");
      vi.stubEnv("VITE_SUPABASE_ANON_KEY", "anon-key");
      vi.stubEnv(
        "VITE_TENANT_SLUGS",
        JSON.stringify({ "753b64bd-9885-4639-b116-8f2c5c497232": "aeronta-demo" }),
      );
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

      render(
        <authMod.AuthProvider>
          <AuthProbe />
        </authMod.AuthProvider>,
      );

      await waitFor(() => expect(screen.getByTestId("session")).toHaveTextContent("yes"));
      expect(screen.getByTestId("tenantSlug")).toHaveTextContent("aeronta-demo");
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
