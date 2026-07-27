import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { activeTenant, bffClient } from "@/lib/api/client";
import type {
  ActionResult,
  AutonomyTier,
  BulkApproveFilter,
  BulkApproveResult,
  KillSwitchState,
  PagedQueue,
  QueueSortKey,
  RecommendationDetail,
  RecommendationType,
  RejectReason,
  TaskStatus,
} from "@/lib/api/types";

export function queueQueryKey(
  tenant: string,
  status: TaskStatus,
  limit: number,
  offset: number,
  sortBy: QueueSortKey,
  sortDir: "asc" | "desc",
  tier?: AutonomyTier,
  type?: RecommendationType,
  aogMin?: number,
) {
  return [
    "queue",
    tenant,
    status,
    limit,
    offset,
    sortBy,
    sortDir,
    tier,
    type,
    aogMin,
  ] as const;
}

export function recommendationQueryKey(tenant: string, recommendationId: string) {
  return ["recommendation", tenant, recommendationId] as const;
}

export function killSwitchQueryKey(tenant: string) {
  return ["killswitch", tenant] as const;
}

/**
 * Server-paged ranked worklist — GET /v1/tenants/{tenant}/recommendations.
 * Deliberately left at the default `staleTime: 0` (Slice S8 hardening audit)
 * — unlike the portfolio read-heavy views (dashboard/forecast/feeds/part
 * context), the Workbench/AI Recommendations queue is where a planner is
 * actively approving/rejecting/deferring; approve/reject/defer/bulk-approve
 * already `invalidateQueue()` on success, but a refetch-on-remount/refocus
 * staying immediate (not gated behind a stale window) keeps a
 * just-approved row from lingering if the tab regains focus mid-review.
 */
export function useQueue(
  status: TaskStatus = "pending",
  limit: number = 50,
  offset: number = 0,
  tenant?: string | null,
  sortBy: QueueSortKey = "priority_score",
  sortDir: "asc" | "desc" = "desc",
  tier?: AutonomyTier,
  type?: RecommendationType,
  aogMin?: number,
) {
  const activeTenantOrNull = tenant ?? activeTenant();
  return useQuery<PagedQueue>({
    queryKey: queueQueryKey(activeTenantOrNull || "", status, limit, offset, sortBy, sortDir, tier, type, aogMin),
    queryFn: () =>
      bffClient.getQueue(status, limit, offset, activeTenantOrNull!, sortBy, sortDir, tier, type, aogMin),
    enabled: !!activeTenantOrNull,
  });
}

/** Recommendation detail — GET /v1/tenants/{tenant}/recommendations/{id}. Default staleTime — see useQueue. */
export function useRecommendation(recommendationId: string, tenant: string = activeTenant()) {
  return useQuery<RecommendationDetail>({
    queryKey: recommendationQueryKey(tenant, recommendationId),
    queryFn: () => bffClient.getRecommendation(recommendationId, tenant),
    enabled: Boolean(recommendationId),
  });
}

/**
 * Kill switch state — GET/POST /v1/tenants/{tenant}/killswitch. Default
 * staleTime (0) is intentional: this gates whether Approve is even
 * clickable, so it must never read stale — a planner engaging the kill
 * switch from another tab should see Approve disable immediately on
 * refocus, not up to a minute late.
 */
export function useKillSwitch(tenant: string = activeTenant()) {
  return useQuery<KillSwitchState>({
    queryKey: killSwitchQueryKey(tenant),
    queryFn: () => bffClient.getKillSwitch(tenant),
  });
}

function invalidateQueue(queryClient: ReturnType<typeof useQueryClient>, tenant: string) {
  return queryClient.invalidateQueries({ queryKey: ["queue", tenant] });
}

/** Accept = approve — POST …/recommendations/{id}/approve. */
export function useApprove(tenant: string = activeTenant()) {
  const queryClient = useQueryClient();
  return useMutation<ActionResult, Error, string>({
    mutationFn: (recommendationId: string) => bffClient.approve(recommendationId, tenant),
    onSuccess: () => invalidateQueue(queryClient, tenant),
  });
}

/** Dismiss = reject (with a required RejectReason) — POST …/reject. */
export function useReject(tenant: string = activeTenant()) {
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
export function useDefer(tenant: string = activeTenant()) {
  const queryClient = useQueryClient();
  return useMutation<ActionResult, Error, { recommendationId: string; until?: string | null }>({
    mutationFn: ({ recommendationId, until }) => bffClient.defer(recommendationId, until, tenant),
    onSuccess: () => invalidateQueue(queryClient, tenant),
  });
}

/** Bulk "Accept high-confidence" — POST …/recommendations/bulk-approve. */
export function useBulkApprove(tenant: string = activeTenant()) {
  const queryClient = useQueryClient();
  return useMutation<BulkApproveResult, Error, BulkApproveFilter>({
    mutationFn: (filter: BulkApproveFilter) => bffClient.bulkApprove(filter, tenant),
    onSuccess: () => invalidateQueue(queryClient, tenant),
  });
}

/** Kill switch toggle — POST …/killswitch. */
export function useSetKillSwitch(tenant: string = activeTenant()) {
  const queryClient = useQueryClient();
  return useMutation<KillSwitchState, Error, boolean>({
    mutationFn: (engaged: boolean) => bffClient.setKillSwitch(engaged, tenant),
    onSuccess: (data) => {
      queryClient.setQueryData(killSwitchQueryKey(tenant), data);
    },
  });
}
