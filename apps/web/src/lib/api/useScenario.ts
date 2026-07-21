import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { activeTenant, bffClient } from "@/lib/api/client";
import type {
  SaveScenarioRequest,
  Scenario,
  ScenarioAuditEvent,
  ScenarioParams,
  ScenarioSolveResult,
} from "@/lib/api/types";

/**
 * Slice S6 — What-If Scenarios (PRD §6.5).
 * Mirrors services/agent-spine/src/trax_io_spine/bff/app.py's
 * `/v1/tenants/{tenant}/scenarios*` routes.
 */

export function scenariosQueryKey(tenant: string) {
  return ["scenarios", tenant] as const;
}

export function scenarioQueryKey(tenant: string, scenarioId: string) {
  return ["scenario", tenant, scenarioId] as const;
}

/** Live solve — POST …/scenarios/solve. Not persisted; the caller debounces. */
export function useSolveScenario(tenant: string = activeTenant()) {
  return useMutation<ScenarioSolveResult, Error, ScenarioParams>({
    mutationFn: (params: ScenarioParams) => bffClient.solveScenario(params, tenant),
  });
}

/** Saved scenarios list — GET …/scenarios. */
export function useScenarios(tenant: string = activeTenant()) {
  return useQuery<Scenario[]>({
    queryKey: scenariosQueryKey(tenant),
    queryFn: () => bffClient.listScenarios(tenant),
  });
}

/** One saved scenario — GET …/scenarios/{id}. */
export function useScenarioDetail(scenarioId: string, tenant: string = activeTenant()) {
  return useQuery<Scenario>({
    queryKey: scenarioQueryKey(tenant, scenarioId),
    queryFn: () => bffClient.getScenario(scenarioId, tenant),
    enabled: Boolean(scenarioId),
  });
}

/** Save/name a scenario — POST …/scenarios. */
export function useSaveScenario(tenant: string = activeTenant()) {
  const queryClient = useQueryClient();
  return useMutation<Scenario, Error, SaveScenarioRequest>({
    mutationFn: (body: SaveScenarioRequest) => bffClient.saveScenario(body, tenant),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: scenariosQueryKey(tenant) }),
  });
}

/** Delete a saved scenario — DELETE …/scenarios/{id}. */
export function useDeleteScenario(tenant: string = activeTenant()) {
  const queryClient = useQueryClient();
  return useMutation<{ deleted: string }, Error, string>({
    mutationFn: (scenarioId: string) => bffClient.deleteScenario(scenarioId, tenant),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: scenariosQueryKey(tenant) }),
  });
}

/** Commit = promote to the tenant's plan + audited marker — POST …/scenarios/{id}/commit. */
export function useCommitScenario(tenant: string = activeTenant()) {
  const queryClient = useQueryClient();
  return useMutation<ScenarioAuditEvent, Error, string>({
    mutationFn: (scenarioId: string) => bffClient.commitScenario(scenarioId, tenant),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: scenariosQueryKey(tenant) }),
  });
}
