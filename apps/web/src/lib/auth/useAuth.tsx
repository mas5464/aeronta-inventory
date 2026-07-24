import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
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

/**
 * The identity a session represents, for telling a genuine identity
 * transition (a different user, a sign-in, a sign-out) apart from a
 * same-identity event like a background `TOKEN_REFRESHED` (the Supabase
 * client is created with default options — see supabase.ts — so
 * `autoRefreshToken` is on and this fires roughly once per token lifetime
 * for any actively-open session; see the C5 Task 8 round-2 regression this
 * distinction fixes).
 *
 * `null` means "no session". A session ALWAYS maps to a defined string —
 * `session.user.id` normally, or `""` if a session object happens to omit
 * it (only seen in minimal test fixtures; every real Supabase session has a
 * `user.id`) — so a real sign-out is never mistaken for "same identity"
 * just because two sessions both happen to lack an id. Every comparison
 * against this return value MUST use `=== null` / `!== null`, never a bare
 * truthiness check: `""` is a valid, non-null identity, but it's falsy.
 */
function identityOf(session: Session | null): string | null {
  return session ? (session.user?.id ?? "") : null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [identity, setIdentity] = useState<string | null>(null);
  const [tenantSlug, setTenantSlug] = useState<string | null>(null);
  const [tenants, setTenants] = useState<TenantRef[]>([]);
  const [tenantStatus, setTenantStatus] = useState<TenantStatus>("idle");
  const [retryNonce, setRetryNonce] = useState(0);

  /**
   * Mirrors `identity` state for reading inside `onAuthStateChange`'s
   * callback below without staleness. That callback is registered ONCE
   * (the subscribing effect's only dependency is the stable `applySession`,
   * so the effect itself never re-runs) — a plain closure over the
   * `identity` STATE variable there would be frozen at whatever it was when
   * the effect first ran (always its initial `null`), never seeing later
   * updates. Written only where `identity` state is (inside `applySession`
   * below), never anywhere else.
   */
  const identityRef = useRef<string | null>(null);

  /**
   * The ONLY place `session`/`identity` are set for a genuine identity
   * TRANSITION — a different user, a sign-in, or a sign-out. Bundling the
   * tenant-resolution reset into this SAME synchronous callback — rather
   * than leaving it to the whoami effect further below, keyed on `identity`
   * — matters: React 18 batches state updates issued together in one
   * callback into a single render, so there is never an intermediate frame
   * where `session` reflects the new value while `tenantStatus` still
   * reflects the old one. That gap (session truthy a render before a
   * *different* effect moved tenantStatus off its stale value) was the
   * round-1 bug.
   *
   * A SAME-identity event (a background `TOKEN_REFRESHED`) must NOT go
   * through here — see the `onAuthStateChange` callback right below, which
   * routes that case to a plain `setSession` instead. Resetting
   * tenant-resolution state for a same-identity event was the round-2
   * regression this fix closes: it forced `tenantStatus` back to "loading"
   * for an already-"ready" session roughly once per token lifetime,
   * tripping AppShell's `tenantStatus !== "ready"` gate and unmounting the
   * whole app behind "Loading your workspace" for no reason.
   */
  const applySession = useCallback((next: Session | null) => {
    const nextIdentity = identityOf(next);
    identityRef.current = nextIdentity;
    setSession(next);
    setIdentity(nextIdentity);
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
    const { data: sub } = client.auth.onAuthStateChange((_evt, s) => {
      if (identityOf(s) === identityRef.current) {
        // Same identity as before — typically a background TOKEN_REFRESHED
        // (see identityOf's doc comment). The new access token still has to
        // reach client.ts (the access-token effect below, keyed on
        // `session`, handles that), so `session` itself IS updated here —
        // but tenantSlug/tenants/tenantStatus are deliberately left
        // untouched: see applySession's doc comment for why.
        setSession(s);
        return;
      }
      // A genuine identity transition — reset tenant-resolution state in
      // the same render `session` changes in.
      applySession(s);
    });
    const onUnauthorized = () => void client.auth.signOut();
    window.addEventListener("aeronta:unauthorized", onUnauthorized);
    return () => {
      sub.subscription.unsubscribe();
      window.removeEventListener("aeronta:unauthorized", onUnauthorized);
    };
  }, [applySession]);

  // Keeps client.ts's module-level access token in sync with `session` on
  // EVERY change, including a same-identity TOKEN_REFRESHED (whose whole
  // point is a new access token) — deliberately its OWN effect, separate
  // from the whoami-fetch effect below, which must NOT re-run for one.
  useEffect(() => {
    setAccessToken(session?.access_token ?? null);
    if (!session) setActiveTenant(null);
  }, [session]);

  useEffect(() => {
    // Keyed on `identity`, NOT `session`: a same-identity TOKEN_REFRESHED
    // changes `session` (see the access-token effect above) but leaves
    // `identity` untouched (see the onAuthStateChange callback above), so
    // this effect correctly does NOT re-fetch whoami for one — re-fetching
    // on every background token refresh was part of the round-2 regression
    // too (a wasted request even once the "loading" flash was masked).
    //
    // `identity === null` — never a bare truthiness check — is deliberate:
    // identityOf() maps a session lacking a `user.id` to the valid,
    // non-null, but FALSY string `""`; `if (!identity)` would wrongly treat
    // that as "no session" and skip resolving it.
    if (identity === null) return;

    // NOTE: tenantStatus is deliberately NOT set to "loading" here.
    // `applySession` already did that (for an identity change) in the same
    // render as `identity` itself changing; `retryTenantResolution` below
    // does it for a retry. Setting it a second time here would only be
    // reachable a render late, reopening the exact gap the round-1 fix
    // closed.
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
        // identity/retry change with no retry loop of its own — there's no
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
  }, [identity, retryNonce]);

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
