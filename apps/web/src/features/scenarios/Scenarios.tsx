import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { QueryError, QueryLoading } from "@/components/QueryState";
import {
  useCommitScenario,
  useDeleteScenario,
  useSaveScenario,
  useScenarios,
  useSolveScenario,
} from "@/lib/api/useScenario";
import type { ScenarioParams } from "@/lib/api/types";
import { ScenarioControls } from "@/features/scenarios/ScenarioControls";
import { ScenarioOutcomePanel } from "@/features/scenarios/ScenarioOutcomePanel";
import { ScenarioFrontierChart } from "@/features/scenarios/ScenarioFrontierChart";
import { SavedScenarios } from "@/features/scenarios/SavedScenarios";

const DEFAULT_PARAMS: ScenarioParams = {
  service_level_target: 0.95,
  lead_time_delta_pct: 0,
  procurement_lead_time_delta_pct: 0,
  repair_tat_delta_pct: 0,
  budget_cap: null,
  scope: "all",
  scope_value: null,
};

const DEBOUNCE_MS = 350;

function normalizedScenarioParams(params: ScenarioParams) {
  return {
    service_level_target: params.service_level_target ?? null,
    service_level_by_tier: Object.fromEntries(
      Object.entries(params.service_level_by_tier ?? {}).sort(([a], [b]) =>
        a.localeCompare(b),
      ),
    ),
    budget_cap: params.budget_cap ?? null,
    procurement_lead_time_delta_pct:
      params.procurement_lead_time_delta_pct ??
      params.lead_time_delta_pct ??
      0,
    repair_tat_delta_pct: params.repair_tat_delta_pct ?? 0,
    scope: params.scope ?? "all",
    scope_value: params.scope_value ?? null,
  };
}

function scenarioParamsMatch(left: ScenarioParams, right: ScenarioParams) {
  return (
    JSON.stringify(normalizedScenarioParams(left)) ===
    JSON.stringify(normalizedScenarioParams(right))
  );
}

/**
 * What-If Scenarios (PRD §6.5) — Slice S6. Sliders (SL target, budget cap, TAT delta,
 * scope) drive a debounced live solve (POST …/scenarios/solve) against the real key
 * universe via the BFF's `ScenarioSolver`. Projected outcome vs. current plan, a
 * cost–service frontier, and save/name/compare/commit — commit is audited but does
 * NOT write policies back to eMRO (see ScenarioOutcomePanel / SavedScenarios).
 */
export function Scenarios() {
  const [params, setParams] = useState<ScenarioParams>(DEFAULT_PARAMS);
  const [debouncedParams, setDebouncedParams] = useState<ScenarioParams>(DEFAULT_PARAMS);
  const [scenarioName, setScenarioName] = useState("");
  const [saveAck, setSaveAck] = useState(false);

  const solveMutation = useSolveScenario();
  const saveMutation = useSaveScenario();
  const deleteMutation = useDeleteScenario();
  const commitMutation = useCommitScenario();
  const scenariosQuery = useScenarios();

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedParams(params), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [params]);

  const { mutate: solve } = solveMutation;
  useEffect(() => {
    solve(debouncedParams);
  }, [debouncedParams, solve]);

  function handleSave() {
    if (
      !scenarioName.trim() ||
      !solveMutation.data ||
      solveMutation.isPending ||
      !scenarioParamsMatch(params, solveMutation.data.params)
    ) {
      return;
    }
    saveMutation.mutate(
      {
        name: scenarioName.trim(),
        params: solveMutation.data.params,
        result: solveMutation.data,
      },
      {
        onSuccess: () => {
          setScenarioName("");
          setSaveAck(true);
          setTimeout(() => setSaveAck(false), 3000);
        },
      },
    );
  }

  const isScopeIncomplete =
    (params.scope === "criticality_tier" || params.scope === "ata_chapter") &&
    !params.scope_value;
  const resultMatchesControls =
    solveMutation.data !== undefined &&
    scenarioParamsMatch(params, solveMutation.data.params);

  return (
    <div className="flex flex-col gap-6 p-6">
      <header>
        <h1 className="text-xl font-semibold text-ink">What-If Scenarios</h1>
        <p className="text-sm text-ink-2">
          Explore service-level, budget, procurement-lead, and repair-TAT
          trade-offs before committing a plan.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[280px_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Levers</CardTitle>
          </CardHeader>
          <CardContent>
            <ScenarioControls params={params} onChange={setParams} />
          </CardContent>
        </Card>

        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle>Projected outcome vs. current plan</CardTitle>
            </CardHeader>
            <CardContent>
              {isScopeIncomplete ? (
                <p role="status" className="text-sm text-ink-2">
                  Select a scope value to solve this scenario.
                </p>
              ) : solveMutation.isPending && !solveMutation.data ? (
                <QueryLoading label="Solving scenario…" className="text-sm text-ink-2" />
              ) : solveMutation.isError ? (
                <QueryError
                  label="Failed to solve scenario"
                  error={solveMutation.error}
                  onRetry={() => solveMutation.mutate(debouncedParams)}
                  className="flex flex-col items-start gap-3 text-sm text-bad"
                />
              ) : solveMutation.data ? (
                <div className="flex flex-col gap-4">
                  <ScenarioOutcomePanel result={solveMutation.data} />
                  <div className="flex flex-wrap items-end gap-2">
                    <label className="flex flex-1 flex-col gap-1 text-sm">
                      <span className="text-xs text-ink-2">Scenario name</span>
                      <input
                        type="text"
                        value={scenarioName}
                        onChange={(e) => setScenarioName(e.target.value)}
                        placeholder="e.g. Tier 1 SL to 99%"
                        aria-label="Scenario name"
                        className="h-9 rounded-control border border-line bg-panel px-2 text-sm text-ink"
                      />
                    </label>
                    <button
                      type="button"
                      onClick={handleSave}
                      disabled={
                        !scenarioName.trim() ||
                        saveMutation.isPending ||
                        solveMutation.isPending ||
                        !resultMatchesControls
                      }
                      className="h-9 rounded-control bg-brand px-4 text-sm font-semibold text-white hover:bg-brand-2 disabled:pointer-events-none disabled:opacity-50"
                    >
                      Save scenario
                    </button>
                  </div>
                  {saveAck && (
                    <p role="status" className="text-xs text-good">
                      Scenario saved.
                    </p>
                  )}
                  {!resultMatchesControls && (
                    <p role="status" className="text-xs text-ink-2">
                      Waiting for a result that matches the current assumptions
                      before saving.
                    </p>
                  )}
                </div>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Cost–service frontier</CardTitle>
            </CardHeader>
            <CardContent>
              {solveMutation.data ? (
                <ScenarioFrontierChart
                  frontier={solveMutation.data.frontier}
                  current={solveMutation.data.current}
                  proposed={solveMutation.data.proposed}
                />
              ) : (
                <p className="text-sm text-ink-2">Solve a scenario to see the frontier.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Saved scenarios</CardTitle>
            </CardHeader>
            <CardContent>
              {scenariosQuery.isPending ? (
                <QueryLoading label="Loading saved scenarios…" className="text-sm text-ink-2" />
              ) : scenariosQuery.isError ? (
                <QueryError
                  label="Failed to load saved scenarios"
                  error={scenariosQuery.error}
                  onRetry={() => scenariosQuery.refetch()}
                  className="flex flex-col items-start gap-3 text-sm text-bad"
                />
              ) : (
                <SavedScenarios
                  scenarios={scenariosQuery.data ?? []}
                  onDelete={(id) => deleteMutation.mutate(id)}
                  onCommit={(id) => commitMutation.mutate(id)}
                  isDeleting={deleteMutation.isPending}
                  isCommitting={commitMutation.isPending}
                />
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
