import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth/useAuth";

/**
 * Minimal branded sign-in gate — rendered by App.tsx whenever auth is
 * enabled (VITE_SUPABASE_* set) and there's no session yet. Auth-disabled
 * dev mode never reaches this component (see App.tsx's gate).
 */
export function Login() {
  const { signIn, tenantSlug, session } = useAuth();
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

  // A valid session but no active tenant (GET /v1/auth/whoami reported
  // `active: null` — e.g. zero memberships, or a stale claim) — distinct
  // from a bad-credentials error.
  if (session && !tenantSlug) {
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
