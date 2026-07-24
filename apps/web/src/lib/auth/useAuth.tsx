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
import { setAccessToken, setActiveTenant } from "@/lib/api/client";
import { getWhoami, type TenantRef } from "@/lib/api/whoami";
import { authEnabled, supabase } from "@/lib/auth/supabase";

interface AuthState {
  session: Session | null;
  authEnabled: boolean;
  tenantSlug: string | null;
  tenants: TenantRef[];
  role: string | null;
  email: string | null;
  signIn: (email: string, password: string) => Promise<{ error: string | null }>;
  signOut: () => Promise<void>;
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

  useEffect(() => {
    if (!supabase) return;
    // Captured as a local const so its non-null narrowing survives into the
    // nested `onUnauthorized` closure — TypeScript doesn't carry a guard on
    // an imported module binding across a function-expression boundary.
    const client = supabase;
    void client.auth.getSession().then(({ data }) => setSession(data.session));
    const { data: sub } = client.auth.onAuthStateChange((_evt, s) => setSession(s));
    const onUnauthorized = () => void client.auth.signOut();
    window.addEventListener("aeronta:unauthorized", onUnauthorized);
    return () => {
      sub.subscription.unsubscribe();
      window.removeEventListener("aeronta:unauthorized", onUnauthorized);
    };
  }, []);

  useEffect(() => {
    // setAccessToken is the module-level state client.ts's request<T>()
    // reads in the hot path (no async session lookup there) — set
    // synchronously, before the whoami fetch below, so that fetch itself
    // carries the bearer token.
    setAccessToken(session?.access_token ?? null);

    if (!session) {
      setTenantSlug(null);
      setTenants([]);
      setActiveTenant(null);
      return;
    }

    let cancelled = false;
    getWhoami()
      .then((whoami) => {
        if (cancelled) return;
        setTenantSlug(whoami.active?.slug ?? null);
        setTenants(whoami.tenants);
        setActiveTenant(whoami.active?.slug ?? null);
      })
      .catch(() => {
        // Degrade to "no tenant" rather than throwing out of the provider.
        // This covers both a network failure AND the 401 a signed-in user
        // with ZERO tenant memberships gets (the BFF's AuthMiddleware
        // rejects any authed request whose JWT lacks a tenant_id claim,
        // whoami included — see apps/web/src/lib/api/whoami.ts). Either way
        // the existing `session && !tenantSlug` gate (App.tsx/Login.tsx)
        // already renders a sane "no tenant access" screen for this state;
        // this effect runs once per session change with no retry loop, so
        // there's no risk of hammering the endpoint or bouncing routes.
        if (cancelled) return;
        setTenantSlug(null);
        setTenants([]);
        setActiveTenant(null);
      });
    return () => {
      cancelled = true;
    };
  }, [session]);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!supabase) return { error: "auth disabled" };
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    return { error: error?.message ?? null };
  }, []);

  const signOut = useCallback(async () => {
    if (supabase) await supabase.auth.signOut();
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      session,
      authEnabled,
      tenantSlug,
      tenants,
      role: roleOf(session) ?? null,
      email: session?.user?.email ?? null,
      signIn,
      signOut,
    }),
    [session, tenantSlug, tenants, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
