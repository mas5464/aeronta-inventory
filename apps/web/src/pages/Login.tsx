import { useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth/useAuth";

/**
 * Branded sign-in gate (Aeronta parent-app composition: forest brand panel +
 * paper form side) — rendered by App.tsx whenever auth is enabled
 * (VITE_SUPABASE_* set) and there's no session, or a session exists but
 * tenant resolution (GET /v1/auth/whoami, via `tenantStatus`) hasn't reached
 * "ready" yet. Auth-disabled dev mode never reaches this component (see
 * App.tsx's gate).
 *
 * The forest panel is a `.dark` island: globals.css scopes the dark token
 * block to `.dark` (not just `:root.dark`), so ink/badge/status tokens inside
 * resolve to their dark-mode values and stay readable on deep green.
 */

function BrandMark() {
  return (
    <span
      aria-hidden="true"
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-control bg-ink text-base font-bold leading-none text-bg"
    >
      A<span className="text-brand">°</span>
    </span>
  );
}

/** Two-pane page frame: forest brand panel (left / top) + content side. */
function LoginFrame({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-ink lg:flex-row">
      <div className="dark relative flex flex-col gap-10 bg-forest p-8 text-ink lg:w-[44%] lg:justify-between lg:p-12">
        <div className="flex items-center gap-2.5">
          <BrandMark />
          <span className="text-base font-semibold tracking-tight">Aeronta</span>
        </div>
        <div className="hidden max-w-md lg:block">
          <p className="eyebrow text-peach">Secure airline workspace</p>
          <h2 className="mt-4 text-3xl font-semibold leading-tight tracking-tight">
            Your inventory.
            <br />
            Your data boundary.
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-ink-2">
            Every Aeronta Inventory session is bound to one airline workspace, one permission
            model, and one auditable operating context.
          </p>
        </div>
        <div className="hidden grid-cols-3 gap-4 border-t border-line pt-4 text-xs text-ink-2 lg:grid">
          <span>Tenant isolated</span>
          <span>Role governed</span>
          <span>Evidence linked</span>
        </div>
      </div>
      <div className="flex flex-1 items-center justify-center p-6 lg:p-12">
        <div className="w-full max-w-sm">{children}</div>
      </div>
    </div>
  );
}

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
      <LoginFrame>
        <h3 className="text-2xl font-semibold tracking-tight text-ink">Loading your workspace</h3>
        <p className="mt-2 text-sm text-ink-2" role="status" aria-live="polite">
          Checking your account access…
        </p>
      </LoginFrame>
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
      <LoginFrame>
        <h3 className="text-2xl font-semibold tracking-tight text-ink">No tenant access</h3>
        <p className="mt-2 text-sm text-ink-2">
          Your account isn&apos;t mapped to a known tenant. Contact your administrator.
        </p>
      </LoginFrame>
    );
  }

  // whoami failed for a reason OTHER than 401 (network failure, 5xx, ...) —
  // NOT the user's fault and not terminal, so it gets its own copy plus a
  // retry affordance instead of being folded into "no tenant access".
  // Retrying is user-initiated only via this button; there is no
  // automatic retry anywhere in useAuth.
  if (session && tenantStatus === "error") {
    return (
      <LoginFrame>
        <h3 className="text-2xl font-semibold tracking-tight text-ink">
          Couldn&apos;t load your workspace
        </h3>
        <div className="mt-2 flex flex-col gap-4 text-sm text-ink-2">
          <p role="alert">
            We weren&apos;t able to check your account access. This is usually temporary.
          </p>
          <Button type="button" onClick={retryTenantResolution} className="w-full">
            Retry
          </Button>
        </div>
      </LoginFrame>
    );
  }

  return (
    <LoginFrame>
      <p className="eyebrow">Platform access</p>
      <h3 className="mt-2 text-2xl font-semibold tracking-tight text-ink">
        Sign in to Aeronta Inventory
      </h3>
      <p className="mt-1 text-sm text-ink-2">
        Use the workspace assigned by your airline administrator.
      </p>
      <form className="mt-6 flex flex-col gap-4" onSubmit={(e) => void handleSubmit(e)}>
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
            className="rounded-control border border-line bg-bg px-3 py-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
            className="rounded-control border border-line bg-bg px-3 py-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>
        {error && (
          <p role="alert" className="text-sm text-bad">
            {error}
          </p>
        )}
        <Button type="submit" disabled={isSubmitting} className="h-11 w-full">
          {isSubmitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </LoginFrame>
  );
}
