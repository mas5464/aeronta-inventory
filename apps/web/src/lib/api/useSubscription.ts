import { useQuery } from "@tanstack/react-query";
import { getBilling, type BillingSummary } from "@/lib/api/billing";

export function subscriptionQueryKey(tenant: string) {
  return ["billing", tenant] as const;
}

/** Same 60s `staleTime` policy as the other read-heavy portfolio queries
 * (see `useDashboard`) — plan/quota data doesn't need to be second-fresh. */
export function useSubscription(tenant: string) {
  return useQuery<BillingSummary>({
    queryKey: subscriptionQueryKey(tenant),
    queryFn: () => getBilling(tenant),
    staleTime: 60_000,
  });
}
