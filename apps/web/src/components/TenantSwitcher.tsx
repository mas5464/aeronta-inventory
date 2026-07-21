import { useState } from "react";
import { activateTenant } from "@/lib/api/members";
import { supabase, tenantSlugByUuid } from "@/lib/auth/supabase";
import { useAuth } from "@/lib/auth/useAuth";

/**
 * Tenant switcher (C2 Task 7). Renders ONLY when authenticated AND there's
 * more than one tenant baked into `VITE_TENANT_SLUGS` — C2's deploy is
 * single-tenant, so this is invisible in production today; shipping it dark
 * now keeps C4's multi-tenant signup flow trivial to light up later (see the
 * task-7 brief).
 *
 * On change: `activateTenant(uuid)` persists the switch (a
 * `tenant_preferences` row the next-mint claims hook reads — see
 * pg/members.py's `set_preference`), `supabase.auth.refreshSession()`
 * re-mints the JWT against that new preference, then a full reload picks up
 * the new tenant end-to-end. A full reload (not an in-place query-cache
 * swap) is deliberate — every TanStack Query key in this app is
 * tenant-scoped off the OLD token's claims, so anything short of a reload
 * would need to invalidate the entire cache anyway.
 *
 * Error handling (C2 Task 7 review): activateTenant, refreshSession, or
 * reload failures are caught and surfaced inline; the select re-enables
 * on error so the user can retry.
 */
export function TenantSwitcher() {
  const { session, tenantSlug } = useAuth();
  const entries = Object.entries(tenantSlugByUuid);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!session || entries.length <= 1) return null;

  const activeUuid = entries.find(([, slug]) => slug === tenantSlug)?.[0] ?? "";

  async function handleChange(uuid: string) {
    setError(null);
    setBusy(true);
    try {
      await activateTenant(uuid);
      if (supabase) await supabase.auth.refreshSession();
      window.location.reload();
    } catch {
      setBusy(false);
      setError("Could not switch tenant — please try again.");
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <select
        aria-label="Switch tenant"
        value={activeUuid}
        disabled={busy}
        onChange={(e) => void handleChange(e.target.value)}
        className="h-8 rounded-control border border-line bg-panel px-2 text-sm text-ink disabled:opacity-50"
      >
        {entries.map(([uuid, slug]) => (
          <option key={uuid} value={uuid}>
            {slug}
          </option>
        ))}
      </select>
      {error && (
        <p role="alert" className="text-xs text-bad">
          {error}
        </p>
      )}
    </div>
  );
}
