import { useQuery } from "@tanstack/react-query";
import { bffClient, DEFAULT_TENANT } from "@/lib/api/client";
import type { DashboardSummary } from "@/lib/api/types";

export function dashboardQueryKey(tenant: string) {
  return ["dashboard", tenant] as const;
}

export function useDashboard(tenant: string = DEFAULT_TENANT) {
  return useQuery<DashboardSummary>({
    queryKey: dashboardQueryKey(tenant),
    queryFn: () => bffClient.getDashboard(tenant),
  });
}
