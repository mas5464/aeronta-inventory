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
import { authEnabled, supabase, tenantSlugByUuid } from "@/lib/auth/supabase";

interface AuthState {
  session: Session | null;
  authEnabled: boolean;
  tenantSlug: string | null;
  role: string | null;
  email: string | null;
  signIn: (email: string, password: string) => Promise<{ error: string | null }>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

function claimsOf(session: Session | null): { tenant?: string; role?: string } {
  const token = session?.access_token;
  if (!token) return {};
  try {
    const payload = JSON.parse(atob(token.split(".")[1])) as {
      tenant_id?: string;
      tenant_role?: string;
    };
    return { tenant: payload.tenant_id, role: payload.tenant_role };
  } catch {
    return {};
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);

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
    // setAccessToken/setActiveTenant are the module-level state client.ts's
    // request<T>() reads in the hot path (no async session lookup there) —
    // both updated together on every session change.
    const { tenant } = claimsOf(session);
    setAccessToken(session?.access_token ?? null);
    setActiveTenant(tenant ? (tenantSlugByUuid[tenant] ?? null) : null);
  }, [session]);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!supabase) return { error: "auth disabled" };
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    return { error: error?.message ?? null };
  }, []);

  const signOut = useCallback(async () => {
    if (supabase) await supabase.auth.signOut();
  }, []);

  const value = useMemo<AuthState>(() => {
    const { tenant, role } = claimsOf(session);
    return {
      session,
      authEnabled,
      tenantSlug: tenant ? (tenantSlugByUuid[tenant] ?? null) : null,
      role: role ?? null,
      email: session?.user?.email ?? null,
      signIn,
      signOut,
    };
  }, [session, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
