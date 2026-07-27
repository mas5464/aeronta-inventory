import { useQuery } from "@tanstack/react-query";
import { bffClient } from "@/lib/api/client";
import type { DashboardSummary } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/useAuth";

export function dashboardQueryKey(tenant: string) {
  return ["dashboard", tenant] as const;
}

/**
 * Read-heavy, portfolio-wide aggregate — `staleTime: 60s` (Slice S8
 * hardening) avoids a refetch storm on every Overview remount/refocus while
 * still surfacing data at most a minute old. The query's real
 * `dataUpdatedAt` (not "now") is what the view should stamp into the
 * KPI cards' `ProvChip` freshness, so a stale card visibly ages in the
 * tooltip instead of always reading "just now".
 *
 * MUST NOT be called until whoami resolves (tenantStatus === "ready") —
 * no longer defaults to a tenant. Use the active tenant from useAuth() hook
 * to guard the call, or pass an explicit tenant. Calling before tenant
 * resolves enables the query immediately with an invalid tenant.
 */
export function useDashboard(tenant?: string) {
  const { tenantSlug } = useAuth();
  const activeTenant = tenant ?? tenantSlug;

  return useQuery<DashboardSummary>({
    queryKey: dashboardQueryKey(activeTenant!),
    queryFn: activeTenant ? () => bffClient.getDashboard(activeTenant) : async () => { throw new Error("Tenant not resolved"); },
    staleTime: 60_000,
    enabled: !!activeTenant,
  });
}
