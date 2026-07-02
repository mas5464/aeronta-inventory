import { useQuery } from "@tanstack/react-query";
import { bffClient, DEFAULT_TENANT } from "@/lib/api/client";
import type { PartContext } from "@/lib/api/types";

export function partContextQueryKey(pn: string, location: string, tenant: string) {
  return ["part-context", tenant, pn, location] as const;
}

/** Read-heavy per-part aggregate — `staleTime: 60s` (Slice S8 hardening); see useDashboard.ts. */
export function usePartContext(
  pn: string,
  location: string,
  tenant: string = DEFAULT_TENANT,
) {
  return useQuery<PartContext>({
    queryKey: partContextQueryKey(pn, location, tenant),
    queryFn: () => bffClient.getPartContext(pn, location, tenant),
    enabled: Boolean(pn) && Boolean(location),
    staleTime: 60_000,
  });
}
