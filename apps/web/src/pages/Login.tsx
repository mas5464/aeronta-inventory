import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth/useAuth";

/**
 * Minimal branded sign-in gate — rendered by App.tsx whenever auth is
 * enabled (VITE_SUPABASE_* set) and there's no session, or a session exists
 * but tenant resolution (GET /v1/auth/whoami, via `tenantStatus`) hasn't
 * reached "ready" yet. Auth-disabled dev mode never reaches this component
 * (see App.tsx's gate).
 */
export function Login() {
  const { signIn, session, tenantStatus, retryTenantResolution } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    const result = await signIn(email, password);
    setIsSubmitting(false);
    if (result.error) {
      setError(result.error);
    }
  }

  // A session exists but whoami hasn't resolved yet — distinct from BOTH
  // "confirmed no tenant" and the plain sign-in form below. Every
  // successful login/reload passes through this render for at least one
  // frame before whoami settles (review fix, C5 Task 8 round 1) — showing
  // either of those other two states here would be wrong.
  if (session && tenantStatus === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg p-6">
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle>Loading your workspace</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-ink-2" role="status" aria-live="polite">
            Checking your account access…
          </CardContent>
        </Card>
      </div>
    );
  }

  // A valid session but no active tenant — whoami resolved with
  // `active: null` (e.g. zero memberships, a stale claim) OR returned 401
  // (the BFF's AuthMiddleware rejects any authed request whose JWT lacks a
  // tenant_id claim, which is exactly what a zero-membership user's JWT
  // looks like — see useAuth.tsx). Both are a confirmed, terminal answer —
  // distinct from a bad-credentials error, and distinct from the "error"
  // branch below (a FAILURE to resolve, not a confirmed answer).
  if (session && tenantStatus === "no-tenant") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg p-6">
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle>No tenant access</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-ink-2">
            Your account isn&apos;t mapped to a known tenant. Contact your administrator.
          </CardContent>
        </Card>
      </div>
    );
  }

  // whoami failed for a reason OTHER than 401 (network failure, 5xx, ...) —
  // NOT the user's fault and not terminal, so it gets its own copy plus a
  // retry affordance instead of being folded into "no tenant access".
  // Retrying is user-initiated only via this button; there is no
  // automatic retry anywhere in useAuth.
  if (session && tenantStatus === "error") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg p-6">
        <Card className="w-full max-w-sm">
          <CardHeader>
            <CardTitle>Couldn&apos;t load your workspace</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm text-ink-2">
            <p role="alert">
              We weren&apos;t able to check your account access. This is usually temporary.
            </p>
            <Button type="button" onClick={retryTenantResolution} className="w-full">
              Retry
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in to Trax Inventory Optimizer</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={(e) => void handleSubmit(e)}>
            <div className="flex flex-col gap-1">
              <label htmlFor="login-email" className="text-sm font-medium text-ink-2">
                Email
              </label>
              <input
                id="login-email"
                type="email"
                required
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="rounded-control border border-line bg-panel px-3 py-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="login-password" className="text-sm font-medium text-ink-2">
                Password
              </label>
              <input
                id="login-password"
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="rounded-control border border-line bg-panel px-3 py-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
              />
            </div>
            {error && (
              <p role="alert" className="text-sm text-bad">
                {error}
              </p>
            )}
            <Button type="submit" disabled={isSubmitting} className="w-full">
              {isSubmitting ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
