import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Metric } from "@/components/Metric";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { SortHeader } from "@/components/table/SortHeader";
import { EmptyRow, TableCaption } from "@/components/table/TableChrome";
import { useUrlSyncedState } from "@/lib/table/useUrlSyncedState";
import { downloadWithAuth, recommendationsExportUrl } from "@/lib/api/client";
import {
  useApprove,
  useBulkApprove,
  useDefer,
  useKillSwitch,
  useQueue,
  useReject,
  useSetKillSwitch,
} from "@/lib/api/useRecommendations";
import type {
  AogRiskLevel,
  AutonomyTier,
  QueueRow,
  QueueSortKey,
  RecommendationType,
  RejectReason,
} from "@/lib/api/types";
import { recommendationProvenance } from "@/lib/recommendationsProvenance";
import { withProvenance } from "@/lib/provenance";
import { ConfidenceBar } from "@/features/workbench/ConfidenceBar";
import { RejectDialog } from "@/features/workbench/RejectDialog";
import {
  AOG_RISK_LABEL,
  highConfidenceRows,
  MAX_PAGE_SIZE,
  RECOMMENDATION_TYPE_LABEL,
  TIER_LABEL,
} from "@/features/workbench/queueView";
import {
  decodeWorkbenchQueryState,
  DEFAULT_WORKBENCH_QUERY_STATE,
  encodeWorkbenchQueryState,
  WORKBENCH_QUERY_KEYS,
} from "@/features/workbench/workbenchQueryState";

/** AOG risk floor the "AOG risk only" pill maps to server-side (High/Critical). */
const AOG_ONLY_MIN: AogRiskLevel = 3;

/**
 * Server-paged page size — see `MAX_PAGE_SIZE`'s docstring (queueView.ts) for
 * the full large-table strategy. 25 is the UX default (a comfortable single
 * screenful); the important invariant is `PAGE_SIZE <= MAX_PAGE_SIZE`, which
 * keeps every rendered `<table>` well within "renders smoothly, no
 * virtualization needed" territory even if a future UI affordance lets a
 * planner widen the page.
 */
const PAGE_SIZE = 25;
if (PAGE_SIZE > MAX_PAGE_SIZE) {
  throw new Error(`Workbench PAGE_SIZE (${PAGE_SIZE}) exceeds MAX_PAGE_SIZE (${MAX_PAGE_SIZE})`);
}

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
 * Slice S3 — Planner Workbench (core loop); task F4 upgraded the pill
 * filters + column sort from a client-side narrowing of the loaded page to
 * server-side sort/filter params on `GET /v1/tenants/{tenant}/recommendations`
 * (`sort_by`/`sort_dir`/`tier`/`type`/`aog_min`, BFF commit 0d3c04d),
 * URL-synced via `useUrlSyncedState` so filters/sort survive reload and are
 * shareable/deep-linkable. Confidence bars, cost-impact + priority, row
 * actions (Accept/Defer/Dismiss), a bulk "Accept high-confidence" action, a
 * pager, and a kill-switch toggle round out the loop.
 */
export function Workbench() {
  const [offset, setOffset] = useState(0);
  const [queryState, setQueryState] = useUrlSyncedState({
    defaultValue: DEFAULT_WORKBENCH_QUERY_STATE,
    serialize: encodeWorkbenchQueryState,
    deserialize: decodeWorkbenchQueryState,
    ownedKeys: WORKBENCH_QUERY_KEYS,
  });
  const [rejectingId, setRejectingId] = useState<string | null>(null);

  // Any sort/filter change re-scopes the server-side query, so the current
  // offset is no longer meaningful — reset to page 1 whenever queryState
  // changes (encoded to a stable string so this doesn't fire on every
  // render, only on actual sort/filter changes; this project has no
  // react-hooks/exhaustive-deps lint rule installed, so the dep array is
  // intentionally scoped to just the encoded state, not `setOffset`, whose
  // identity is stable per React's guarantee for state setters).
  const encodedQueryState = encodeWorkbenchQueryState(queryState).toString();
  useEffect(() => {
    setOffset(0);
  }, [encodedQueryState]);

  const queueQuery = useQueue(
    "pending",
    PAGE_SIZE,
    offset,
    undefined,
    queryState.sort,
    queryState.dir,
    queryState.tier === "all" ? undefined : queryState.tier,
    queryState.type === "all" ? undefined : queryState.type,
    queryState.aogOnly ? AOG_ONLY_MIN : undefined,
  );
  const killSwitchQuery = useKillSwitch();

  const approveMutation = useApprove();
  const rejectMutation = useReject();
  const deferMutation = useDefer();
  const bulkApproveMutation = useBulkApprove();
  const setKillSwitchMutation = useSetKillSwitch();

  const engaged = killSwitchQuery.data?.engaged ?? false;

  if (queueQuery.isPending) {
    return <QueryLoading label="Loading workbench…" />;
  }

  if (queueQuery.isError) {
    return (
      <QueryError
        label="Failed to load workbench"
        error={queueQuery.error}
        onRetry={() => queueQuery.refetch()}
      />
    );
  }

  const { items, total } = queueQuery.data;
  // Tier/type/AOG filtering and sort now happen server-side (task F4) — the
  // BFF already returns exactly the matching, sorted page, so no client-side
  // narrowing is applied to `items` here. `highConfidenceRows` remains a
  // client-only bulk-accept preview: confidence isn't a BFF filter param, so
  // it stays a narrowing of the loaded page (see queueView.ts docstring).
  const candidates = highConfidenceRows(items);
  const provenance = recommendationProvenance();
  const exportUrl = recommendationsExportUrl({
    status: "pending",
    sortBy: queryState.sort,
    sortDir: queryState.dir,
    tier: queryState.tier === "all" ? undefined : queryState.tier,
    type: queryState.type === "all" ? undefined : queryState.type,
    aogMin: queryState.aogOnly ? AOG_ONLY_MIN : undefined,
  });

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

  /** Toggle direction when re-clicking the active column; new column defaults to desc. */
  function handleSort(column: QueueSortKey) {
    if (column === queryState.sort) {
      setQueryState({ ...queryState, dir: queryState.dir === "asc" ? "desc" : "asc" });
    } else {
      setQueryState({ ...queryState, sort: column, dir: "desc" });
    }
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

      {/* Pill filters — now drive server-side tier/type/aog_min params (task F4) */}
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Worklist filters">
        <button
          type="button"
          onClick={() => setQueryState({ ...queryState, tier: "all" })}
          aria-pressed={queryState.tier === "all"}
          className="rounded-full border border-line px-3 py-1 text-xs font-medium data-[active=true]:bg-brand data-[active=true]:text-white"
          data-active={queryState.tier === "all"}
        >
          All tiers
        </button>
        {TIER_OPTIONS.map((tier) => (
          <button
            key={tier}
            type="button"
            onClick={() => setQueryState({ ...queryState, tier })}
            aria-pressed={queryState.tier === tier}
            className="rounded-full border border-line px-3 py-1 text-xs font-medium data-[active=true]:bg-brand data-[active=true]:text-white"
            data-active={queryState.tier === tier}
          >
            {TIER_LABEL[tier]}
          </button>
        ))}
        <span className="mx-1 text-ink-3">·</span>
        <button
          type="button"
          onClick={() => setQueryState({ ...queryState, type: "all" })}
          aria-pressed={queryState.type === "all"}
          className="rounded-full border border-line px-3 py-1 text-xs font-medium data-[active=true]:bg-brand data-[active=true]:text-white"
          data-active={queryState.type === "all"}
        >
          All types
        </button>
        {TYPE_OPTIONS.map((type) => (
          <button
            key={type}
            type="button"
            onClick={() => setQueryState({ ...queryState, type })}
            aria-pressed={queryState.type === type}
            className="rounded-full border border-line px-3 py-1 text-xs font-medium data-[active=true]:bg-brand data-[active=true]:text-white"
            data-active={queryState.type === type}
          >
            {RECOMMENDATION_TYPE_LABEL[type]}
          </button>
        ))}
        <span className="mx-1 text-ink-3">·</span>
        <button
          type="button"
          onClick={() => setQueryState({ ...queryState, aogOnly: !queryState.aogOnly })}
          aria-pressed={queryState.aogOnly}
          className="rounded-full border border-line px-3 py-1 text-xs font-medium data-[active=true]:bg-bad data-[active=true]:text-white"
          data-active={queryState.aogOnly}
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
        <Button
          variant="outline"
          size="sm"
          onClick={() => void downloadWithAuth(exportUrl, "recommendations.csv")}
        >
          Export CSV
        </Button>
      </div>

      {/* Worklist */}
      <Card>
        <CardHeader>
          <CardTitle>Ranked worklist ({items.length} of {total})</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <table className="w-full text-left text-sm">
            <TableCaption>
              {`Ranked worklist of recommended actions, page ${rangeStart}–${rangeEnd} of ${total}`}
            </TableCaption>
            <thead>
              <tr className="text-ink-2">
                <th scope="col" className="p-3 font-medium">Part / Location</th>
                <th scope="col" className="p-3 font-medium">Type</th>
                <th scope="col" className="p-3 font-medium">AOG</th>
                <SortHeader
                  column="confidence_score"
                  label="Confidence"
                  activeSort={queryState.sort}
                  dir={queryState.dir}
                  onSort={handleSort}
                />
                <SortHeader
                  column="estimated_cost_impact"
                  label="Cost impact"
                  activeSort={queryState.sort}
                  dir={queryState.dir}
                  onSort={handleSort}
                />
                <SortHeader
                  column="priority_score"
                  label="Priority"
                  activeSort={queryState.sort}
                  dir={queryState.dir}
                  onSort={handleSort}
                />
                <th scope="col" className="p-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <EmptyRow colSpan={7}>No recommendations match the current filters.</EmptyRow>
              ) : (
                items.map((row: QueueRow) => (
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
                        <Link
                          to={`/parts/${encodeURIComponent(row.pn)}/${encodeURIComponent(row.location)}#history`}
                          className="text-xs text-brand hover:underline"
                        >
                          History
                        </Link>
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
                ))
              )}
            </tbody>
          </table>
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
