import { useQuery } from "@tanstack/react-query";
import { activeTenant, bffClient } from "@/lib/api/client";
import type { DashboardSummary } from "@/lib/api/types";

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
 */
export function useDashboard(tenant: string = activeTenant()) {
  return useQuery<DashboardSummary>({
    queryKey: dashboardQueryKey(tenant),
    queryFn: () => bffClient.getDashboard(tenant),
    staleTime: 60_000,
  });
}
