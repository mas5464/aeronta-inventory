import { useQuery } from "@tanstack/react-query";
import { activeTenant, bffClient } from "@/lib/api/client";
import type { FeedsSummary } from "@/lib/api/types";

export function feedsQueryKey(tenant: string) {
  return ["feeds", tenant] as const;
}

/** Read-heavy portfolio aggregate — `staleTime: 60s` (Slice S8 hardening); see useDashboard.ts. */
export function useFeeds(tenant: string = activeTenant()) {
  return useQuery<FeedsSummary>({
    queryKey: feedsQueryKey(tenant),
    queryFn: () => bffClient.getFeeds(tenant),
    staleTime: 60_000,
  });
}
