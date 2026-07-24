import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { Session } from "@supabase/supabase-js";
import { ApiError, setAccessToken, setActiveTenant } from "@/lib/api/client";
import { getWhoami, type TenantRef } from "@/lib/api/whoami";
import { authEnabled, supabase } from "@/lib/auth/supabase";

/**
 * The status of resolving the caller's active tenant via `GET
 * /v1/auth/whoami`. Kept as an explicit enum rather than inferred from
 * `tenantSlug === null` — that collapse was the review-fix bug (C5 Task 8
 * round 1): `null` is both the initial "haven't asked yet" value AND the
 * confirmed answer "this user has no tenant", so every login/reload
 * rendered at least one frame of the "no tenant access" message before
 * whoami actually settled.
 *
 * - "idle" — no session, so there's nothing to resolve (dev mode, or
 *   signed out).
 * - "loading" — a session exists and the whoami request is in flight.
 * - "ready" — whoami resolved with an active tenant (`tenantSlug` is set).
 * - "no-tenant" — whoami resolved with `active: null`, OR returned 401 (the
 *   BFF's AuthMiddleware rejects any authed request whose JWT lacks a
 *   tenant_id claim — a signed-in user with ZERO tenant memberships gets a
 *   401 here, not an empty list; see whoami.ts). Both are the same
 *   CONFIRMED answer to the user: contact your administrator.
 * - "error" — the whoami request failed for any OTHER reason (network
 *   failure, 5xx, ...). Distinct from "no-tenant" so the UI doesn't blame
 *   the user's permissions for what's actually a transient failure.
 */
export type TenantStatus = "idle" | "loading" | "ready" | "no-tenant" | "error";

interface AuthState {
  session: Session | null;
  authEnabled: boolean;
  tenantSlug: string | null;
  tenants: TenantRef[];
  tenantStatus: TenantStatus;
  role: string | null;
  email: string | null;
  signIn: (email: string, password: string) => Promise<{ error: string | null }>;
  signOut: () => Promise<void>;
  /**
   * User-initiated re-attempt of tenant resolution — for the "error" state
   * only. There is deliberately no automatic retry anywhere in this
   * provider: a 401 is a terminal, correct answer ("no-tenant"), not a
   * failure to retry.
   */
  retryTenantResolution: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

/**
 * Only `tenant_role` is read from the client-decoded JWT payload — tenant
 * IDENTITY (which tenant, its slug/name, and the caller's full membership
 * list) comes exclusively from the verified `GET /v1/auth/whoami` response
 * (see the effect below), never from this unverified client-side decode.
 * That keeps the JWT the sole source of truth for identity: the frontend
 * displays what whoami reports rather than deriving/assuming a tenant slug
 * from anything client-controlled (the C2-era build-time tenant-slug env
 * map this replaces was exactly that kind of client-side derivation).
 */
function roleOf(session: Session | null): string | undefined {
  const token = session?.access_token;
  if (!token) return undefined;
  try {
    // JWT payloads are base64url-encoded (RFC 7519 §3), not plain base64 —
    // `-`/`_` (base64url) stand in for `+`/`/` (base64). `atob` only
    // understands the latter, so a claims payload whose base64 form happens
    // to contain `+` or `/` would throw/mis-decode without this normalization.
    const b64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(b64)) as { tenant_role?: string };
    return payload.tenant_role;
  } catch {
    return undefined;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [tenantSlug, setTenantSlug] = useState<string | null>(null);
  const [tenants, setTenants] = useState<TenantRef[]>([]);
  const [tenantStatus, setTenantStatus] = useState<TenantStatus>("idle");
  const [retryNonce, setRetryNonce] = useState(0);

  /**
   * The ONLY place `session` is ever set. Bundling the tenant-resolution
   * reset into this SAME synchronous callback — rather than leaving it to
   * the separate whoami effect below, keyed on `session` — matters: React
   * 18 batches state updates issued together in one callback into a single
   * render, so there is never an intermediate frame where `session`
   * reflects the new value while `tenantStatus` still reflects the old one.
   * That gap (session truthy a render before a *different* effect moved
   * tenantStatus off its stale value) was exactly the round-1 bug.
   */
  const applySession = useCallback((next: Session | null) => {
    setSession(next);
    setTenantSlug(null);
    setTenants([]);
    setTenantStatus(next ? "loading" : "idle");
  }, []);

  useEffect(() => {
    if (!supabase) return;
    // Captured as a local const so its non-null narrowing survives into the
    // nested `onUnauthorized` closure — TypeScript doesn't carry a guard on
    // an imported module binding across a function-expression boundary.
    const client = supabase;
    void client.auth.getSession().then(({ data }) => applySession(data.session));
    const { data: sub } = client.auth.onAuthStateChange((_evt, s) => applySession(s));
    const onUnauthorized = () => void client.auth.signOut();
    window.addEventListener("aeronta:unauthorized", onUnauthorized);
    return () => {
      sub.subscription.unsubscribe();
      window.removeEventListener("aeronta:unauthorized", onUnauthorized);
    };
  }, [applySession]);

  useEffect(() => {
    // setAccessToken is the module-level state client.ts's request<T>()
    // reads in the hot path (no async session lookup there) — set
    // synchronously, before the whoami fetch below, so that fetch itself
    // carries the bearer token.
    setAccessToken(session?.access_token ?? null);

    if (!session) {
      setActiveTenant(null);
      return;
    }

    // NOTE: tenantStatus is deliberately NOT set to "loading" here.
    // `applySession` above already did that (for a session change) in the
    // same render as `session` itself changing; `retryTenantResolution`
    // below does it for a retry. Setting it a second time here would only
    // be reachable a render late, reopening the exact gap this fix closes.
    let cancelled = false;
    getWhoami()
      .then((whoami) => {
        if (cancelled) return;
        setTenantSlug(whoami.active?.slug ?? null);
        setTenants(whoami.tenants);
        setActiveTenant(whoami.active?.slug ?? null);
        setTenantStatus(whoami.active ? "ready" : "no-tenant");
      })
      .catch((err: unknown) => {
        // Degrade rather than throwing out of the provider. A 401 is the
        // BFF's AuthMiddleware rejecting a JWT with no tenant_id claim — a
        // signed-in user with ZERO tenant memberships (see
        // apps/web/src/lib/api/whoami.ts) — which is a CONFIRMED "no
        // tenant" answer, same as an empty `tenants` list. Anything else
        // (network failure, 5xx, ...) is a genuine failure to resolve and
        // must not be presented as if it were the user's fault (see
        // TenantStatus's doc comment). Either way this effect runs once per
        // session/retry change with no retry loop of its own — there's no
        // risk of hammering the endpoint or bouncing routes.
        if (cancelled) return;
        setTenantSlug(null);
        setTenants([]);
        setActiveTenant(null);
        setTenantStatus(err instanceof ApiError && err.status === 401 ? "no-tenant" : "error");
      });
    return () => {
      cancelled = true;
    };
  }, [session, retryNonce]);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!supabase) return { error: "auth disabled" };
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    return { error: error?.message ?? null };
  }, []);

  const signOut = useCallback(async () => {
    if (supabase) await supabase.auth.signOut();
  }, []);

  const retryTenantResolution = useCallback(() => {
    // Guard against a stray call outside the "error" state (e.g. no session
    // at all) leaving tenantStatus stuck on "loading" forever with nothing
    // to ever move it off that value.
    if (!session) return;
    setTenantStatus("loading");
    setRetryNonce((n) => n + 1);
  }, [session]);

  const value = useMemo<AuthState>(
    () => ({
      session,
      authEnabled,
      tenantSlug,
      tenants,
      tenantStatus,
      role: roleOf(session) ?? null,
      email: session?.user?.email ?? null,
      signIn,
      signOut,
      retryTenantResolution,
    }),
    [session, tenantSlug, tenants, tenantStatus, signIn, signOut, retryTenantResolution],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
