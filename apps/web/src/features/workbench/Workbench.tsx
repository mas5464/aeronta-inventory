import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Metric } from "@/components/Metric";
import {
  useApprove,
  useBulkApprove,
  useDefer,
  useKillSwitch,
  useQueue,
  useReject,
  useSetKillSwitch,
} from "@/lib/api/useRecommendations";
import type { AogRiskLevel, AutonomyTier, QueueRow, RecommendationType, RejectReason } from "@/lib/api/types";
import { recommendationProvenance } from "@/lib/recommendationsProvenance";
import { withProvenance } from "@/lib/provenance";
import { ConfidenceBar } from "@/features/workbench/ConfidenceBar";
import { RejectDialog } from "@/features/workbench/RejectDialog";
import {
  AOG_RISK_LABEL,
  applyQueueFilters,
  DEFAULT_QUEUE_FILTERS,
  highConfidenceRows,
  RECOMMENDATION_TYPE_LABEL,
  TIER_LABEL,
  type QueueFilters,
} from "@/features/workbench/queueView";

const PAGE_SIZE = 25;

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const TIER_OPTIONS: AutonomyTier[] = [1, 2, 3];
const TYPE_OPTIONS: RecommendationType[] = [
  "purchase",
  "transfer",
  "reduce_stock",
  "sell",
  "adjust_min_max",
];

function aogVariant(level: AogRiskLevel): "default" | "warn" | "bad" {
  if (level >= 3) return "bad";
  if (level >= 1) return "warn";
  return "default";
}

/**
 * Slice S3 — Planner Workbench (core loop): a server-paged ranked worklist
 * of recommendations (GET /v1/tenants/{tenant}/recommendations) with pill
 * filters (tier/type/AOG, client-side over the loaded page — the BFF's
 * queue route has no server-side filter params yet), confidence bars,
 * cost-impact + priority, row actions (Accept/Defer/Dismiss), a bulk
 * "Accept high-confidence" action, a pager, and a kill-switch toggle.
 */
export function Workbench() {
  const [offset, setOffset] = useState(0);
  const [filters, setFilters] = useState<QueueFilters>(DEFAULT_QUEUE_FILTERS);
  const [rejectingId, setRejectingId] = useState<string | null>(null);

  const queueQuery = useQueue("pending", PAGE_SIZE, offset);
  const killSwitchQuery = useKillSwitch();

  const approveMutation = useApprove();
  const rejectMutation = useReject();
  const deferMutation = useDefer();
  const bulkApproveMutation = useBulkApprove();
  const setKillSwitchMutation = useSetKillSwitch();

  const engaged = killSwitchQuery.data?.engaged ?? false;

  if (queueQuery.isPending) {
    return (
      <div role="status" aria-live="polite" className="p-6 text-ink-2">
        Loading workbench…
      </div>
    );
  }

  if (queueQuery.isError) {
    return (
      <div role="alert" className="p-6 text-bad">
        Failed to load workbench:{" "}
        {queueQuery.error instanceof Error ? queueQuery.error.message : "unknown error"}
      </div>
    );
  }

  const { items, total } = queueQuery.data;
  const filteredRows = applyQueueFilters(items, filters);
  const candidates = highConfidenceRows(filteredRows);
  const provenance = recommendationProvenance();

  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + PAGE_SIZE, total);

  function handleBulkAccept() {
    if (candidates.length === 0) return;
    const tiers = Array.from(new Set(candidates.map((row) => row.tier))) as AutonomyTier[];
    const criticalityMin = Math.min(...candidates.map((row) => row.criticality_tier));
    bulkApproveMutation.mutate({ tiers, criticality_min: criticalityMin });
  }

  function handleReject(recommendationId: string, reason: RejectReason, detail: string) {
    rejectMutation.mutate(
      { recommendationId, reason, detail },
      { onSuccess: () => setRejectingId(null) },
    );
  }

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-ink">Workbench</h1>
          <p className="text-sm text-ink-2">Ranked worklist of recommended actions.</p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-ink-2" data-testid="killswitch-status">
            Kill switch: {engaged ? "Engaged" : "Off"}
          </span>
          <Button
            variant={engaged ? "default" : "outline"}
            size="sm"
            onClick={() => setKillSwitchMutation.mutate(!engaged)}
            disabled={setKillSwitchMutation.isPending}
            aria-pressed={engaged}
          >
            {engaged ? "Resume approvals" : "Engage kill switch"}
          </Button>
        </div>
      </header>

      {engaged && (
        <div role="alert" className="rounded-md border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          Approvals are paused — the tenant kill switch is engaged. Accept and bulk actions are
          disabled until it is turned off.
        </div>
      )}

      {/* Pill filters */}
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Worklist filters">
        <button
          type="button"
          onClick={() => setFilters((f) => ({ ...f, tier: "all" }))}
          aria-pressed={filters.tier === "all"}
          className="rounded-full border border-line px-3 py-1 text-xs font-medium data-[active=true]:bg-brand data-[active=true]:text-white"
          data-active={filters.tier === "all"}
        >
          All tiers
        </button>
        {TIER_OPTIONS.map((tier) => (
          <button
            key={tier}
            type="button"
            onClick={() => setFilters((f) => ({ ...f, tier }))}
            aria-pressed={filters.tier === tier}
            className="rounded-full border border-line px-3 py-1 text-xs font-medium data-[active=true]:bg-brand data-[active=true]:text-white"
            data-active={filters.tier === tier}
          >
            {TIER_LABEL[tier]}
          </button>
        ))}
        <span className="mx-1 text-ink-3">·</span>
        <button
          type="button"
          onClick={() => setFilters((f) => ({ ...f, type: "all" }))}
          aria-pressed={filters.type === "all"}
          className="rounded-full border border-line px-3 py-1 text-xs font-medium data-[active=true]:bg-brand data-[active=true]:text-white"
          data-active={filters.type === "all"}
        >
          All types
        </button>
        {TYPE_OPTIONS.map((type) => (
          <button
            key={type}
            type="button"
            onClick={() => setFilters((f) => ({ ...f, type }))}
            aria-pressed={filters.type === type}
            className="rounded-full border border-line px-3 py-1 text-xs font-medium data-[active=true]:bg-brand data-[active=true]:text-white"
            data-active={filters.type === type}
          >
            {RECOMMENDATION_TYPE_LABEL[type]}
          </button>
        ))}
        <span className="mx-1 text-ink-3">·</span>
        <button
          type="button"
          onClick={() => setFilters((f) => ({ ...f, aogOnly: !f.aogOnly }))}
          aria-pressed={filters.aogOnly}
          className="rounded-full border border-line px-3 py-1 text-xs font-medium data-[active=true]:bg-bad data-[active=true]:text-white"
          data-active={filters.aogOnly}
        >
          AOG risk only
        </button>
      </div>

      {/* Bulk accept-high-confidence */}
      <div className="flex items-center gap-3">
        <Button
          variant="outline"
          size="sm"
          onClick={handleBulkAccept}
          disabled={engaged || candidates.length === 0 || bulkApproveMutation.isPending}
        >
          Accept high-confidence ({candidates.length})
        </Button>
        {bulkApproveMutation.isSuccess && (
          <span className="text-xs text-good">
            Approved {bulkApproveMutation.data.approved_count} recommendations.
          </span>
        )}
        <span className="text-xs text-ink-3">
          Confidence ≥ 80%, client-filtered on this page — the BFF's bulk-approve filter has no
          confidence field, so this bulk-approves by tier/criticality among the matching rows.
        </span>
      </div>

      {/* Worklist */}
      <Card>
        <CardHeader>
          <CardTitle>Ranked worklist ({filteredRows.length} of {total})</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {filteredRows.length === 0 ? (
            <p className="p-4 text-sm text-ink-2">No recommendations match the current filters.</p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="text-ink-2">
                  <th className="p-3 font-medium">Part / Location</th>
                  <th className="p-3 font-medium">Type</th>
                  <th className="p-3 font-medium">AOG</th>
                  <th className="p-3 font-medium">Confidence</th>
                  <th className="p-3 font-medium">Cost impact</th>
                  <th className="p-3 font-medium">Priority</th>
                  <th className="p-3 font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((row: QueueRow) => (
                    <tr key={row.recommendation_id} className="border-t border-line align-top">
                      <td className="p-3">
                        <Link
                          to={`/parts/${encodeURIComponent(row.pn)}/${encodeURIComponent(row.location)}`}
                          className="font-medium text-brand hover:underline"
                        >
                          {row.pn}
                        </Link>
                        <div className="text-xs text-ink-2">{row.location}</div>
                        <div className="text-xs text-ink-3">{row.reason}</div>
                      </td>
                      <td className="p-3">
                        <Badge>{RECOMMENDATION_TYPE_LABEL[row.type]}</Badge>
                      </td>
                      <td className="p-3">
                        <Badge variant={aogVariant(row.aog_risk_level)}>
                          {AOG_RISK_LABEL[row.aog_risk_level]}
                        </Badge>
                      </td>
                      <td className="p-3">
                        <ConfidenceBar score={row.confidence_score} />
                      </td>
                      <td className="p-3">
                        <Metric
                          metric={withProvenance(row.estimated_cost_impact, provenance)}
                          format={currencyFormatter.format}
                        />
                      </td>
                      <td className="p-3 tabular-nums text-ink-2">{row.priority_score.toFixed(1)}</td>
                      <td className="p-3">
                        <div className="flex flex-wrap gap-2">
                          <Button
                            size="sm"
                            onClick={() => approveMutation.mutate(row.recommendation_id)}
                            disabled={engaged || !row.approvable || approveMutation.isPending}
                          >
                            Accept
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => deferMutation.mutate({ recommendationId: row.recommendation_id })}
                            disabled={deferMutation.isPending}
                          >
                            Defer
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setRejectingId(row.recommendation_id)}
                          >
                            Dismiss
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled
                            title="Adjust/override (editing proposed values before accepting) is not yet supported by the BFF — coming soon."
                          >
                            Adjust (coming soon)
                          </Button>
                        </div>
                        {rejectingId === row.recommendation_id && (
                          <div className="mt-2 max-w-xs">
                            <RejectDialog
                              recommendationId={row.recommendation_id}
                              onCancel={() => setRejectingId(null)}
                              onConfirm={(reason, detail) =>
                                handleReject(row.recommendation_id, reason, detail)
                              }
                              isSubmitting={rejectMutation.isPending}
                            />
                          </div>
                        )}
                      </td>
                    </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>

      {/* Pager */}
      <div className="flex items-center justify-between text-sm text-ink-2">
        <span>
          {rangeStart}–{rangeEnd} of {total}
        </span>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            disabled={offset === 0}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            disabled={offset + PAGE_SIZE >= total}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
