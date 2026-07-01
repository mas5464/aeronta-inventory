import { useQuery } from "@tanstack/react-query";
import { bffClient, DEFAULT_TENANT } from "@/lib/api/client";
import type { FeedsSummary } from "@/lib/api/types";

export function feedsQueryKey(tenant: string) {
  return ["feeds", tenant] as const;
}

export function useFeeds(tenant: string = DEFAULT_TENANT) {
  return useQuery<FeedsSummary>({
    queryKey: feedsQueryKey(tenant),
    queryFn: () => bffClient.getFeeds(tenant),
  });
}
