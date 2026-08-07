import { useQuery } from "@tanstack/react-query";
import { activeTenant, bffClient } from "@/lib/api/client";
import type { PartContext } from "@/lib/api/types";

export function partContextQueryKey(
  pn: string,
  location: string,
  tenant: string,
  recommendationId?: string | null,
) {
  return [
    "part-context",
    tenant,
    pn,
    location,
    recommendationId ?? null,
  ] as const;
}

/** Read-heavy per-part aggregate — `staleTime: 60s` (Slice S8 hardening); see useDashboard.ts. */
export function usePartContext(
  pn: string,
  location: string,
  tenant: string = activeTenant(),
  recommendationId?: string | null,
) {
  return useQuery<PartContext>({
    queryKey: partContextQueryKey(pn, location, tenant, recommendationId),
    queryFn: () =>
      bffClient.getPartContext(pn, location, tenant, recommendationId),
    enabled: Boolean(pn) && Boolean(location),
    staleTime: 60_000,
  });
}
