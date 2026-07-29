import { Moon, Sun } from "lucide-react";
import { HashRouter, NavLink, Route, Routes, useSearchParams } from "react-router-dom";
import { cn } from "@/lib/utils";
import { activeTenant } from "@/lib/api/client";
import { useTheme } from "@/lib/useTheme";
import { AuthProvider, useAuth } from "@/lib/auth/useAuth";
import { TenantSwitcher } from "@/components/TenantSwitcher";
import { AiRecommendations } from "@/features/recommendations/AiRecommendations";
import { BillingPage } from "@/features/billing/BillingPage";
import { SignupWizard } from "@/features/billing/SignupWizard";
import { SubscriptionBanner } from "@/features/billing/SubscriptionBanner";
import { DataConnections } from "@/features/feeds/DataConnections";
import { ForecastServiceLevels } from "@/features/forecast/ForecastServiceLevels";
import { PartDrillDown } from "@/features/part/PartDrillDown";
import { Portfolio } from "@/features/portfolio/Portfolio";
import { Reports } from "@/features/reports/Reports";
import { Scenarios } from "@/features/scenarios/Scenarios";
import { Workbench } from "@/features/workbench/Workbench";
import { Login } from "@/pages/Login";
import { Members } from "@/pages/Members";
import { Overview } from "@/pages/Overview";

const NAV_ITEMS = [
  { to: "/", label: "Overview", end: true },
  { to: "/workbench", label: "Workbench" },
  { to: "/recommendations", label: "AI Recommendations" },
  { to: "/forecast", label: "Forecast & Service Levels" },
  { to: "/scenarios", label: "What-If Scenarios" },
  { to: "/portfolio", label: "Portfolio" },
  { to: "/data", label: "Data & Connections" },
  { to: "/reports", label: "Reports" },
];

// Appended to NAV_ITEMS only for an admin/owner `role` claim (C2 Task 7) —
// dev-mode (auth disabled, `role` always null) and a planner/viewer session
// never see it, matching the BFF's own admin-or-owner gate on
// /v1/tenants/{tenant}/members* (members_routes.py's `_require_admin_or_owner`).
const MEMBERS_NAV_ITEM = { to: "/members", label: "Members" };

// Appended to NAV_ITEMS only for an `owner` `role` claim (C4 Task 10) —
// stricter than Members' admin-or-owner gate, since billing actions
// (Stripe Checkout/Portal) are owner-only. Dev-mode (auth disabled, `role`
// always null) never sees it.
const BILLING_NAV_ITEM = { to: "/billing", label: "Billing" };

function AppNav({ items }: { items: { to: string; label: string; end?: boolean }[] }) {
  return (
    // `NavLink` sets `aria-current="page"` on the active item automatically
    // (react-router-dom default) — WCAG 2.1 AA §4.1.2, satisfied for free.
    <nav className="flex gap-1 px-6" aria-label="Primary">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) =>
            cn(
              "rounded-t-control px-3 py-2 text-sm font-medium text-ink-2 hover:text-ink",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
              isActive && "bg-panel text-ink",
            )
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

/**
 * `/signup` route wrapper (C4 Task 11) — reads the `?plan` query param off
 * the HashRouter location (e.g. `#/signup?plan=growth`; `useSearchParams`
 * parses the hash's own search string) and defaults to "growth" when
 * absent. Rendered as a **sibling** of `AppShell`'s catch-all route in
 * `App()` below, not nested inside it — so it's reachable pre-auth,
 * bypassing `AppShell`'s `authEnabled && (!session || tenantStatus !==
 * "ready")` gate entirely (that gate only runs for the paths AppShell
 * actually renders).
 */
function SignupRoute() {
  const [searchParams] = useSearchParams();
  return <SignupWizard initialPlan={searchParams.get("plan") ?? "growth"} />;
}

/**
 * The app shell — gated by auth state (via `useAuth`, so it must render
 * under `AuthProvider`). Auth-disabled dev mode (no VITE_SUPABASE_* env,
 * `authEnabled === false`) never gates: this renders exactly the pre-auth
 * header/nav/routes with `session` always null, byte-identical to before
 * this task.
 */
function AppShell() {
  const { theme, toggleTheme } = useTheme();
  const { authEnabled, session, tenantSlug, tenantStatus, role, email, signOut } = useAuth();
  const navItems = [
    ...NAV_ITEMS,
    ...(role === "admin" || role === "owner" ? [MEMBERS_NAV_ITEM] : []),
    ...(role === "owner" ? [BILLING_NAV_ITEM] : []),
  ];

  // Any session whose tenant resolution (GET /v1/auth/whoami) hasn't
  // reached "ready" also gates to `Login` — it renders the matching
  // loading/no-tenant/error branch rather than the sign-in form. This is
  // NOT the same thing as `!tenantSlug`: `tenantStatus` distinguishes
  // "still resolving" from "confirmed no tenant" from "failed to resolve",
  // where `tenantSlug === null` alone conflates all three (the round-1 bug
  // — see useAuth.tsx's TenantStatus doc comment). `!session` stays
  // explicit (not just folded into `tenantStatus !== "ready"`) because on
  // sign-out there's a render where `session` is already null but
  // `tenantStatus` hasn't caught up yet (React re-renders before the
  // effect that resets it runs) — without this, that one frame would fall
  // through to the full app shell with a null session.
  if (authEnabled && (!session || tenantStatus !== "ready")) {
    return <Login />;
  }

  return (
    <div className="min-h-screen bg-bg text-ink">
      <header className="border-b border-line">
        <div className="flex items-center justify-between px-6 py-4">
          <h1 className="text-lg font-semibold">Trax Inventory Optimizer</h1>
          <div className="flex items-center gap-3">
            {session && (
              <>
                <TenantSwitcher />
                <span className="text-sm text-ink-2">{email}</span>
                <button
                  type="button"
                  onClick={() => void signOut()}
                  className="rounded-control px-3 py-1.5 text-sm font-medium text-ink-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
                >
                  Sign out
                </button>
              </>
            )}
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              className="rounded-control p-2 text-ink-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
          </div>
        </div>
        <AppNav items={navItems} />
      </header>
      {authEnabled && session && tenantSlug && <SubscriptionBanner tenant={tenantSlug} />}
      <main>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/workbench" element={<Workbench />} />
          <Route path="/recommendations" element={<AiRecommendations />} />
          <Route path="/forecast" element={<ForecastServiceLevels />} />
          <Route path="/scenarios" element={<Scenarios />} />
          <Route path="/portfolio" element={<Portfolio />} />
          <Route path="/data" element={<DataConnections />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/members" element={<Members />} />
          <Route path="/billing" element={<BillingPage tenant={tenantSlug ?? activeTenant()} role={role} />} />
          <Route path="/parts/:pn/:location" element={<PartDrillDown />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <HashRouter>
      <AuthProvider>
        <Routes>
          <Route path="/signup" element={<SignupRoute />} />
          <Route path="/*" element={<AppShell />} />
        </Routes>
      </AuthProvider>
    </HashRouter>
  );
}
