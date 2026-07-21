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
 */
export function TenantSwitcher() {
  const { session, tenantSlug } = useAuth();
  const entries = Object.entries(tenantSlugByUuid);

  if (!session || entries.length <= 1) return null;

  const activeUuid = entries.find(([, slug]) => slug === tenantSlug)?.[0] ?? "";

  async function handleChange(uuid: string) {
    await activateTenant(uuid);
    if (supabase) await supabase.auth.refreshSession();
    window.location.reload();
  }

  return (
    <select
      aria-label="Switch tenant"
      value={activeUuid}
      onChange={(e) => void handleChange(e.target.value)}
      className="h-8 rounded-control border border-line bg-panel px-2 text-sm text-ink"
    >
      {entries.map(([uuid, slug]) => (
        <option key={uuid} value={uuid}>
          {slug}
        </option>
      ))}
    </select>
  );
}
