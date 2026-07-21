import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { activeTenant, bffClient } from "@/lib/api/client";
import type { HistoryEntry, RollbackRequest, RollbackResult } from "@/lib/api/types";

export function historyQueryKey(tenant: string, pn: string, location: string) {
  return ["history", tenant, pn, location] as const;
}

/** Writeback history for a (pn, location). Disabled until both are present. */
export function useHistory(pn: string, location: string, tenant: string = activeTenant()) {
  return useQuery<HistoryEntry[]>({
    queryKey: historyQueryKey(tenant, pn, location),
    queryFn: () => bffClient.getHistory(pn, location, tenant),
    enabled: Boolean(pn) && Boolean(location),
  });
}

/** Rollback mutation — invalidates the tenant's history queries on success. */
export function useRollback(tenant: string = activeTenant()) {
  const queryClient = useQueryClient();
  return useMutation<RollbackResult, Error, RollbackRequest>({
    mutationFn: (req: RollbackRequest) => bffClient.rollback(req, tenant),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["history", tenant] }),
  });
}
