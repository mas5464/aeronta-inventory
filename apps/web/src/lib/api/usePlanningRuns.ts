import {
  useMutation,
  useQuery,
  useQueryClient,
  type Query,
} from "@tanstack/react-query";
import { activeTenant } from "@/lib/api/client";
import {
  createPlanningRun,
  getPlanningCapability,
  getPlanningRun,
  getPlanningRunRerunConfig,
  getPlanningRunSelections,
  getPlanningRuns,
  type CreatePlanningRunBody,
  type PlanningCapability,
  type PlanningRerunConfig,
  type PlanningRunSelectionParams,
  type PlanningRunSubmission,
  type PlanningRunView,
} from "@/lib/api/planningRuns";

const ACTIVE_RUN_STATUSES = new Set(["queued", "running"]);
export const PLANNING_RUN_POLL_MS = 2_000;
export const PLANNING_HISTORY_POLL_MS = 4_000;

export const planningRunsQueryKey = (tenant: string) =>
  ["planning-runs", tenant] as const;

export const planningCapabilityQueryKey = (tenant: string) =>
  ["planning-capability", tenant] as const;

export const planningRunQueryKey = (tenant: string, runId: string) =>
  ["planning-runs", tenant, runId] as const;

export const planningRunRerunConfigQueryKey = (
  tenant: string,
  runId: string,
) => ["planning-runs", tenant, runId, "rerun-config"] as const;

export const planningRunSelectionsQueryKey = (
  tenant: string,
  runId: string,
  params: PlanningRunSelectionParams = {},
) =>
  [
    "planning-runs",
    tenant,
    runId,
    "selections",
    {
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
      decisionKey: params.decisionKey ?? null,
      selectedIsNoChange: params.selectedIsNoChange ?? null,
    },
  ] as const;

export function planningRunPollInterval(
  run: PlanningRunView | undefined,
): number | false {
  return run && ACTIVE_RUN_STATUSES.has(run.status)
    ? PLANNING_RUN_POLL_MS
    : false;
}

function detailPollInterval(
  query: Query<PlanningRunView, Error>,
): number | false {
  return planningRunPollInterval(query.state.data);
}

function historyPollInterval(
  query: Query<PlanningRunView[], Error>,
): number | false {
  return query.state.data?.some((run) => ACTIVE_RUN_STATUSES.has(run.status))
    ? PLANNING_HISTORY_POLL_MS
    : false;
}

export function usePlanningCapability(tenant: string = activeTenant()) {
  return useQuery<PlanningCapability, Error>({
    queryKey: planningCapabilityQueryKey(tenant),
    queryFn: () => getPlanningCapability(tenant),
    enabled: Boolean(tenant),
    staleTime: 30_000,
  });
}

export function usePlanningRuns(
  tenant: string = activeTenant(),
  enabled = true,
) {
  return useQuery<PlanningRunView[], Error>({
    queryKey: planningRunsQueryKey(tenant),
    queryFn: () => getPlanningRuns(tenant),
    enabled: Boolean(tenant) && enabled,
    staleTime: 5_000,
    refetchInterval: historyPollInterval,
  });
}

export function usePlanningRun(
  runId: string | null,
  tenant: string = activeTenant(),
) {
  return useQuery<PlanningRunView, Error>({
    queryKey: planningRunQueryKey(tenant, runId ?? ""),
    queryFn: () => getPlanningRun(runId!, tenant),
    enabled: Boolean(tenant) && Boolean(runId),
    staleTime: 1_000,
    refetchInterval: detailPollInterval,
  });
}

export function usePlanningRunRerunConfig(
  runId: string | null,
  tenant: string = activeTenant(),
) {
  return useQuery<PlanningRerunConfig, Error>({
    queryKey: planningRunRerunConfigQueryKey(tenant, runId ?? ""),
    queryFn: () => getPlanningRunRerunConfig(runId!, tenant),
    enabled: Boolean(tenant) && Boolean(runId),
    staleTime: 5_000,
  });
}

export function usePlanningRunSelections(
  run: PlanningRunView | undefined,
  tenant: string = activeTenant(),
  params: PlanningRunSelectionParams = {},
) {
  const enabled = Boolean(tenant) && run?.status === "completed";
  return useQuery({
    queryKey: planningRunSelectionsQueryKey(
      tenant,
      run?.run_id ?? "",
      params,
    ),
    queryFn: () => getPlanningRunSelections(run!.run_id, tenant, params),
    enabled,
    staleTime: Infinity,
  });
}

export function useCreatePlanningRun(tenant: string = activeTenant()) {
  const queryClient = useQueryClient();
  return useMutation<
    PlanningRunSubmission,
    Error,
    CreatePlanningRunBody,
    { submittedTenant: string }
  >({
    mutationFn: (body) => createPlanningRun(body, tenant),
    onMutate: () => ({ submittedTenant: tenant }),
    onSuccess: (submission, _body, context) => {
      if (!context) return;
      const submittedTenant = context.submittedTenant;
      queryClient.setQueryData(
        planningRunQueryKey(submittedTenant, submission.run.run_id),
        submission.run,
      );
      void queryClient.invalidateQueries({
        queryKey: planningRunsQueryKey(submittedTenant),
      });
    },
  });
}
