import { useEffect, useState } from "react";
import { supabase, authEnabled } from "@/lib/auth/supabase";
import { createCheckoutSession, getPublicPrices } from "@/lib/api/billing";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Step = "account" | "confirm" | "org" | "plan";

export interface SignupWizardProps {
  /** The plan tier requested via `/signup?plan=<tier>` (App.tsx), e.g.
   * "growth" — matched against `getPublicPrices()`'s `tier` field at the
   * plan step. Defaults to "growth" at the call site, not here. */
  initialPlan: string;
}

/**
 * `/signup` onboarding wizard (C4 Task 11): create account → confirm email
 * → name the organization → pick a billing interval → Stripe Checkout.
 * Public route (mounted outside the authed shell in App.tsx) so it works
 * pre-auth; also handles an already-authenticated visitor (an existing
 * member revisiting the link, or an owner adding a plan) by skipping the
 * account step.
 */
export function SignupWizard({ initialPlan }: SignupWizardProps) {
  const [step, setStep] = useState<Step>("account");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Already-logged-in visitor: skip straight past account creation. Uses
  // the functional updater (`s === "account" ? "org" : s`) so a visitor who
  // has ALREADY progressed past "account" via their own action (e.g. their
  // `createAccount()` click resolved first) never gets stomped by this
  // background check resolving late — the user's own explicit result always
  // wins over this best-effort background skip.
  useEffect(() => {
    if (!supabase) return;
    let cancelled = false;
    void supabase.auth.getSession().then(({ data }) => {
      if (!cancelled && data.session) setStep((s) => (s === "account" ? "org" : s));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!authEnabled || !supabase) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg p-6">
        <p className="text-sm text-ink-2">Signup requires auth to be configured for this deployment.</p>
      </div>
    );
  }

  async function createAccount() {
    setBusy(true);
    setErr(null);
    try {
      const { data, error } = await supabase!.auth.signUp({ email, password });
      if (error) {
        setErr(error.message);
        return;
      }
      // Confirmation required: signUp returns no session until the email
      // link is clicked.
      setStep(data.session ? "org" : "confirm");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function continueAfterConfirm() {
    setBusy(true);
    setErr(null);
    try {
      const { data } = await supabase!.auth.getSession();
      if (data.session) setStep("org");
      else setErr("Please confirm your email, then try again.");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function createOrg() {
    setBusy(true);
    setErr(null);
    try {
      const { error } = await supabase!.rpc("create_tenant_for_current_user", { p_name: orgName });
      if (error) {
        setErr(error.message);
        return;
      }
      // Pick up the new tenant_id/tenant_role claims minted by the RPC.
      const { error: refreshError } = await supabase!.auth.refreshSession();
      if (refreshError) {
        setErr(refreshError.message);
        return;
      }
      setStep("plan");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function goToCheckout(interval: "month" | "year") {
    setBusy(true);
    setErr(null);
    try {
      const prices = await getPublicPrices();
      const price =
        prices.find((p) => p.tier === initialPlan && p.interval === interval) ??
        prices.find((p) => p.tier === initialPlan);
      if (!price) {
        setErr("No price configured for this plan.");
        return;
      }
      // create-checkout-session's Edge Function resolves the tenant from
      // the caller's JWT, not from this argument — createCheckoutSession's
      // first parameter is unused server-side (`_tenant`, see
      // lib/api/billing.ts). "current" documents that this call always
      // targets whoever is signed in, never a literal slug.
      location.href = await createCheckoutSession("current", price.id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="text-xl font-semibold text-ink capitalize">
            {step === "account" && "Start your 14-day free trial"}
            {step === "confirm" && "Confirm your email"}
            {step === "org" && "Name your organization"}
            {step === "plan" && `${initialPlan} plan · 14-day trial`}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {err && (
            <p role="alert" className="text-sm text-bad">
              {err}
            </p>
          )}

          {step === "account" && (
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-1">
                <label htmlFor="signup-email" className="text-sm font-medium text-ink-2">
                  Email
                </label>
                <input
                  id="signup-email"
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="rounded-control border border-line bg-panel px-3 py-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label htmlFor="signup-password" className="text-sm font-medium text-ink-2">
                  Password
                </label>
                <input
                  id="signup-password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="rounded-control border border-line bg-panel px-3 py-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                />
              </div>
              <Button
                disabled={busy || !email || !password}
                onClick={() => void createAccount()}
                className="w-full"
              >
                Create account
              </Button>
            </div>
          )}

          {step === "confirm" && (
            <div className="flex flex-col gap-4">
              <p className="text-sm text-ink-2">
                We sent a confirmation link to <strong className="text-ink">{email}</strong>. Click it,
                then continue below.
              </p>
              <Button disabled={busy} onClick={() => void continueAfterConfirm()} className="w-full">
                I&apos;ve confirmed — continue
              </Button>
            </div>
          )}

          {step === "org" && (
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-1">
                <label htmlFor="signup-org" className="text-sm font-medium text-ink-2">
                  Organization name
                </label>
                <input
                  id="signup-org"
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  className="rounded-control border border-line bg-panel px-3 py-2 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
                />
              </div>
              <Button
                disabled={busy || orgName.trim().length < 2}
                onClick={() => void createOrg()}
                className="w-full"
              >
                Continue
              </Button>
            </div>
          )}

          {step === "plan" && (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-ink-2">
                Choose how you&apos;d like to be billed for the{" "}
                <span className="font-medium capitalize text-ink">{initialPlan}</span> plan.
              </p>
              <div className="flex gap-3">
                <Button disabled={busy} onClick={() => void goToCheckout("month")} className="flex-1">
                  Monthly
                </Button>
                <Button
                  disabled={busy}
                  variant="outline"
                  onClick={() => void goToCheckout("year")}
                  className="flex-1"
                >
                  Annual
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
