import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bffClient, DEFAULT_TENANT } from "@/lib/api/client";
import type {
  ActionResult,
  BulkApproveFilter,
  BulkApproveResult,
  KillSwitchState,
  PagedQueue,
  RecommendationDetail,
  RejectReason,
  TaskStatus,
} from "@/lib/api/types";

export function queueQueryKey(
  tenant: string,
  status: TaskStatus,
  limit: number,
  offset: number,
) {
  return ["queue", tenant, status, limit, offset] as const;
}

export function recommendationQueryKey(tenant: string, recommendationId: string) {
  return ["recommendation", tenant, recommendationId] as const;
}

export function killSwitchQueryKey(tenant: string) {
  return ["killswitch", tenant] as const;
}

/** Server-paged ranked worklist — GET /v1/tenants/{tenant}/recommendations. */
export function useQueue(
  status: TaskStatus = "pending",
  limit: number = 50,
  offset: number = 0,
  tenant: string = DEFAULT_TENANT,
) {
  return useQuery<PagedQueue>({
    queryKey: queueQueryKey(tenant, status, limit, offset),
    queryFn: () => bffClient.getQueue(status, limit, offset, tenant),
  });
}

/** Recommendation detail — GET /v1/tenants/{tenant}/recommendations/{id}. */
export function useRecommendation(recommendationId: string, tenant: string = DEFAULT_TENANT) {
  return useQuery<RecommendationDetail>({
    queryKey: recommendationQueryKey(tenant, recommendationId),
    queryFn: () => bffClient.getRecommendation(recommendationId, tenant),
    enabled: Boolean(recommendationId),
  });
}

/** Kill switch state — GET/POST /v1/tenants/{tenant}/killswitch. */
export function useKillSwitch(tenant: string = DEFAULT_TENANT) {
  return useQuery<KillSwitchState>({
    queryKey: killSwitchQueryKey(tenant),
    queryFn: () => bffClient.getKillSwitch(tenant),
  });
}

function invalidateQueue(queryClient: ReturnType<typeof useQueryClient>, tenant: string) {
  return queryClient.invalidateQueries({ queryKey: ["queue", tenant] });
}

/** Accept = approve — POST …/recommendations/{id}/approve. */
export function useApprove(tenant: string = DEFAULT_TENANT) {
  const queryClient = useQueryClient();
  return useMutation<ActionResult, Error, string>({
    mutationFn: (recommendationId: string) => bffClient.approve(recommendationId, tenant),
    onSuccess: () => invalidateQueue(queryClient, tenant),
  });
}

/** Dismiss = reject (with a required RejectReason) — POST …/reject. */
export function useReject(tenant: string = DEFAULT_TENANT) {
  const queryClient = useQueryClient();
  return useMutation<
    ActionResult,
    Error,
    { recommendationId: string; reason: RejectReason; detail?: string }
  >({
    mutationFn: ({ recommendationId, reason, detail }) =>
      bffClient.reject(recommendationId, reason, detail, tenant),
    onSuccess: () => invalidateQueue(queryClient, tenant),
  });
}

/** Defer — POST …/defer. */
export function useDefer(tenant: string = DEFAULT_TENANT) {
  const queryClient = useQueryClient();
  return useMutation<ActionResult, Error, { recommendationId: string; until?: string | null }>({
    mutationFn: ({ recommendationId, until }) => bffClient.defer(recommendationId, until, tenant),
    onSuccess: () => invalidateQueue(queryClient, tenant),
  });
}

/** Bulk "Accept high-confidence" — POST …/recommendations/bulk-approve. */
export function useBulkApprove(tenant: string = DEFAULT_TENANT) {
  const queryClient = useQueryClient();
  return useMutation<BulkApproveResult, Error, BulkApproveFilter>({
    mutationFn: (filter: BulkApproveFilter) => bffClient.bulkApprove(filter, tenant),
    onSuccess: () => invalidateQueue(queryClient, tenant),
  });
}

/** Kill switch toggle — POST …/killswitch. */
export function useSetKillSwitch(tenant: string = DEFAULT_TENANT) {
  const queryClient = useQueryClient();
  return useMutation<KillSwitchState, Error, boolean>({
    mutationFn: (engaged: boolean) => bffClient.setKillSwitch(engaged, tenant),
    onSuccess: (data) => {
      queryClient.setQueryData(killSwitchQueryKey(tenant), data);
    },
  });
}
