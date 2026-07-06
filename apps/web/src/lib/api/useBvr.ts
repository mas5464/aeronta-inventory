import { useQuery } from "@tanstack/react-query";
import { bffClient, DEFAULT_TENANT } from "@/lib/api/client";
import type { BvrReport } from "@/lib/api/types";

export function bvrQueryKey(tenant: string) {
  return ["bvr", tenant] as const;
}

/** The tenant's Business Value Report. Read-heavy snapshot — staleTime 60s. */
export function useBvr(tenant: string = DEFAULT_TENANT) {
  return useQuery<BvrReport>({
    queryKey: bvrQueryKey(tenant),
    queryFn: () => bffClient.getBvr(tenant),
    staleTime: 60_000,
  });
}
