import { useState } from "react";
import { activateTenant } from "@/lib/api/members";
import { supabase } from "@/lib/auth/supabase";
import { useAuth } from "@/lib/auth/useAuth";

/**
 * Tenant switcher (C2 Task 7). Renders ONLY when authenticated AND the
 * caller belongs to more than one tenant — sourced from `useAuth().tenants`
 * (C5 Task 8's `GET /v1/auth/whoami`, which replaced the C2-era build-time
 * tenant-slug env map; a tenant created after the last frontend deploy is
 * now switchable immediately, with no rebuild required).
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
  const { session, tenantSlug, tenants } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!session || tenants.length <= 1) return null;

  const activeUuid = tenants.find((t) => t.slug === tenantSlug)?.tenant_uuid ?? "";

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
        {tenants.map((t) => (
          <option key={t.tenant_uuid} value={t.tenant_uuid}>
            {t.slug}
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
