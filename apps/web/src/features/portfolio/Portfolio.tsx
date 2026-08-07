import { useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { activeTenant } from "@/lib/api/client";
import { useAuth } from "@/lib/auth/useAuth";
import {
  useCreatePlanningRun,
  usePlanningCapability,
  usePlanningRun,
  usePlanningRuns,
} from "@/lib/api/usePlanningRuns";
import type { CreatePlanningRunBody } from "@/lib/api/planningRuns";
import { PlanningRunDetail } from "@/features/portfolio/PlanningRunDetail";
import { PlanningRunForm } from "@/features/portfolio/PlanningRunForm";
import { PlanningRunHistory } from "@/features/portfolio/PlanningRunHistory";
import { ShadowValidationPanel } from "@/features/replay/ShadowValidationPanel";

const PLANNING_ROLES = new Set(["planner", "admin", "owner"]);

export function Portfolio() {
  const { authEnabled, role, tenantSlug } = useAuth();
  const tenant = tenantSlug ?? activeTenant();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const capabilityQuery = usePlanningCapability(tenant);
  const planningEnabled = capabilityQuery.data?.enabled === true;
  const historyQuery = usePlanningRuns(tenant, planningEnabled);
  const createMutation = useCreatePlanningRun(tenant);
  const currentTenant = useRef(tenant);
  currentTenant.current = tenant;

  useEffect(() => {
    setSelectedRunId(null);
    createMutation.reset();
  }, [tenant]);

  const effectiveRunId =
    selectedRunId ?? historyQuery.data?.[0]?.run_id ?? null;
  const detailQuery = usePlanningRun(effectiveRunId, tenant);
  const terminalRuns = useMemo(
    () =>
      (historyQuery.data ?? []).filter((run) =>
        ["completed", "infeasible", "failed"].includes(run.status),
      ),
    [historyQuery.data],
  );
  const canSubmit =
    capabilityQuery.data?.can_submit === true &&
    (!authEnabled || (role !== null && PLANNING_ROLES.has(role)));

  function submit(body: CreatePlanningRunBody) {
    const submittedTenant = tenant;
    createMutation.mutate(body, {
      onSuccess: (submission) => {
        if (currentTenant.current === submittedTenant) {
          setSelectedRunId(submission.run.run_id);
        }
      },
    });
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line pb-4">
        <div>
          <h1 className="text-xl font-semibold text-ink">
            Portfolio optimization
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-ink-2">
            Select one reconciled candidate per inventory key under a hard
            acquisition budget and mandatory service or risk floors.
          </p>
        </div>
        <Badge variant="warn">Advisory only · no writeback</Badge>
      </header>

      {capabilityQuery.isPending ? (
        <QueryLoading label="Checking portfolio availability…" />
      ) : capabilityQuery.isError ? (
        <QueryError
          label="Failed to check portfolio availability"
          error={capabilityQuery.error}
          onRetry={() => capabilityQuery.refetch()}
        />
      ) : !planningEnabled ? (
        <Card>
          <CardHeader>
            <CardTitle>Portfolio optimization is not enabled</CardTitle>
          </CardHeader>
          <CardContent>
            <p role="status" className="text-sm text-ink-2">
              This tenant remains on the default-off launch gate. Enabling the
              advisory workflow does not grant a role or any inventory
              writeback authority.
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[22rem_minmax(0,1fr)]">
        <aside className="flex flex-col gap-4">
          {canSubmit ? (
            <PlanningRunForm
              key={tenant}
              tenant={tenant}
              terminalRuns={terminalRuns}
              isPending={createMutation.isPending}
              error={createMutation.error}
              onSubmit={submit}
            />
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>Read-only portfolio access</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-ink-2">
                  Viewers may inspect run evidence. A planner, admin, or owner
                  role is required to submit a new optimization run.
                </p>
              </CardContent>
            </Card>
          )}

          {historyQuery.isPending ? (
            <QueryLoading
              label="Loading planning history…"
              className="p-4 text-sm text-ink-2"
            />
          ) : historyQuery.isError ? (
            <QueryError
              label="Failed to load planning history"
              error={historyQuery.error}
              onRetry={() => historyQuery.refetch()}
              className="flex flex-col items-start gap-3 p-4 text-sm text-bad"
            />
          ) : (
            <PlanningRunHistory
              runs={historyQuery.data ?? []}
              selectedRunId={effectiveRunId}
              onSelect={setSelectedRunId}
            />
          )}
        </aside>

        <section aria-label="Planning run detail">
          {!effectiveRunId ? (
            <Card>
              <CardHeader>
                <CardTitle>No run selected</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-ink-2">
                  Submit an advisory plan or choose a historical run to inspect
                  its progress, evidence, and selections.
                </p>
              </CardContent>
            </Card>
          ) : detailQuery.isPending ? (
            <QueryLoading label="Loading planning run…" />
          ) : detailQuery.isError ? (
            <QueryError
              label="Failed to load planning run"
              error={detailQuery.error}
              onRetry={() => detailQuery.refetch()}
            />
          ) : (
            <PlanningRunDetail run={detailQuery.data} tenant={tenant} />
          )}
        </section>
      </div>

      <ShadowValidationPanel tenant={tenant} canSubmit={canSubmit} />
        </>
      )}
    </div>
  );
}
