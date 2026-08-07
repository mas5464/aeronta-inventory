import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  PlanningRunStatus,
  PlanningRunView,
} from "@/lib/api/planningRuns";
import {
  formatPlanningDate,
  formatPlanningMoney,
  planningStatusLabel,
} from "@/features/portfolio/portfolioView";

const STATUS_VARIANT: Record<
  PlanningRunStatus,
  NonNullable<BadgeProps["variant"]>
> = {
  queued: "default",
  running: "brand",
  completed: "good",
  infeasible: "warn",
  failed: "bad",
};

interface PlanningRunHistoryProps {
  runs: PlanningRunView[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}

export function PlanningRunHistory({
  runs,
  selectedRunId,
  onSelect,
}: PlanningRunHistoryProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Run history</CardTitle>
        <p className="text-xs text-ink-2">
          Immutable submissions, newest first.
        </p>
      </CardHeader>
      <CardContent>
        {runs.length === 0 ? (
          <p className="text-sm text-ink-2">No planning runs yet.</p>
        ) : (
          <ol className="flex max-h-[32rem] flex-col gap-2 overflow-y-auto">
            {runs.map((run) => {
              const selected = run.run_id === selectedRunId;
              return (
                <li key={run.run_id}>
                  <button
                    type="button"
                    aria-pressed={selected}
                    onClick={() => onSelect(run.run_id)}
                    className={[
                      "w-full rounded-control border p-3 text-left transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
                      selected
                        ? "border-brand bg-brand/10"
                        : "border-line hover:bg-panel-2",
                    ].join(" ")}
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs text-ink">
                        {run.run_id.slice(0, 8)}
                      </span>
                      <span className="flex items-center gap-1">
                        {run.stale && <Badge variant="warn">Stale inputs</Badge>}
                        <Badge variant={STATUS_VARIANT[run.status]}>
                          {planningStatusLabel(run.status)}
                        </Badge>
                      </span>
                    </span>
                    <span className="mt-2 block text-xs text-ink-2">
                      {run.key_count.toLocaleString("en-US")} keys ·{" "}
                      {formatPlanningMoney(run.budget, run.currency)}
                    </span>
                    <span className="mt-1 block text-xs text-ink-3">
                      {formatPlanningDate(run.created_at)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
