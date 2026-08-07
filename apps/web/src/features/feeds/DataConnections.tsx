import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Metric } from "@/components/Metric";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { useAuth } from "@/lib/auth/useAuth";
import { activeTenant } from "@/lib/api/client";
import {
  ingestHistoryQueryKey,
  isSupersededResult,
  isValidationFailedResult,
  listIngests,
  type IngestHistoryItem,
  type RepairHistoryIngestResult,
} from "@/lib/api/ingest";
import { useFeeds } from "@/lib/api/useFeeds";
import { feedsProvenance } from "@/lib/feedsProvenance";
import { withProvenance } from "@/lib/provenance";
import { FeedTable } from "@/features/feeds/FeedTable";
import { RecommendedFeeds } from "@/features/feeds/RecommendedFeeds";
import { PartStatSheetLookup } from "@/features/feeds/PartStatSheetLookup";
import { UploadPanel } from "@/features/feeds/UploadPanel";
import { IngestHistory } from "@/features/feeds/IngestHistory";
import {
  FEED_STATUS_LABEL,
  type FeedStatusFilter,
} from "@/features/feeds/feedTableView";
import type { FeedConnectionStatus, FeedHealthRow } from "@/lib/api/types";

const integerFormatter = new Intl.NumberFormat("en-US");

/** Roles that may drive an upload/ingest run — mirrors UploadPanel's gate. */
function canUpload(role: string | null): boolean {
  return role === "planner" || role === "admin" || role === "owner";
}

function connectionVariant(
  status: FeedConnectionStatus,
): "good" | "warn" | "bad" {
  if (status === "connected") return "good";
  if (status === "partial") return "warn";
  return "bad";
}

function formatWhen(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? iso
    : date.toISOString().slice(0, 16).replace("T", " ");
}

function latestReportedRepairHistory(
  jobs: IngestHistoryItem[] | undefined,
): {
  job: IngestHistoryItem;
  result: RepairHistoryIngestResult | null;
  validationErrorCount: number | null;
} | null {
  for (const job of jobs ?? []) {
    if (job.result === null || isSupersededResult(job.result)) {
      continue;
    }
    if (isValidationFailedResult(job.result)) {
      return {
        job,
        result: job.result.validation_summary.repair_history ?? null,
        validationErrorCount:
          job.result.validation_summary.validation_error_count,
      };
    }
    if (job.status === "done" && job.result.repair_history !== undefined) {
      return {
        job,
        result: job.result.repair_history,
        validationErrorCount: null,
      };
    }
  }
  return null;
}

function repairCoverageJobLabel(job: IngestHistoryItem): string {
  return job.kind === "recompute" ? "scheduled recompute" : "upload";
}

function RepairCoverageCounts({
  result,
}: {
  result: RepairHistoryIngestResult | null;
}) {
  const validationCounts = [
    ["Accepted", result?.accepted, "repair-coverage-accepted"],
    ["Excluded", result?.excluded, "repair-coverage-excluded"],
    ["Quarantined", result?.quarantined, "repair-coverage-quarantined"],
  ] as const;
  const coverageCounts = [
    ["Parts covered", result?.parts_covered, "repair-coverage-parts"],
    ["Shops covered", result?.shops_covered, "repair-coverage-shops"],
    ["Observed", result?.observed, "repair-coverage-observed"],
    ["Pooled fallback", result?.pooled, "repair-coverage-pooled"],
    ["Proxy", result?.proxy, "repair-coverage-proxy"],
    ["Unavailable", result?.unavailable, "repair-coverage-unavailable"],
  ] as const;

  const renderCounts = (
    counts: ReadonlyArray<readonly [string, number | undefined, string]>,
  ) => (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {counts.map(([label, value, testId]) => (
        <div key={label} className="rounded-md border border-line bg-panel px-3 py-2">
          <dt className="text-xs text-ink-3">{label}</dt>
          <dd
            data-testid={testId}
            className="mt-0.5 tabular-nums font-medium text-ink"
          >
            {value === undefined ? "—" : integerFormatter.format(value)}
          </dd>
        </div>
      ))}
    </dl>
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <p className="text-xs font-medium uppercase tracking-wide text-ink-3">
          Validation outcome
        </p>
        {renderCounts(validationCounts)}
      </div>
      <div className="flex flex-col gap-1">
        <p className="text-xs font-medium uppercase tracking-wide text-ink-3">
          Coverage and fallback
        </p>
        {renderCounts(coverageCounts)}
      </div>
    </div>
  );
}

interface RepairHistoryCoverageCardProps {
  repairFeed: FeedHealthRow | undefined;
  history: IngestHistoryItem[] | undefined;
  isPending: boolean;
  isError: boolean;
}

function RepairHistoryCoverageCard({
  repairFeed,
  history,
  isPending,
  isError,
}: RepairHistoryCoverageCardProps) {
  const reported = latestReportedRepairHistory(history);
  const coverageLabel = isPending
    ? "Loading"
    : isError
      ? "Unavailable"
      : reported
        ? reported.job.status === "failed" || reported.job.status === "dead"
          ? "Validation failed"
          : "Reported"
        : "Not reported";
  const coverageVariant = isPending
    ? "warn"
    : isError
      ? "bad"
      : reported
        ? reported.job.status === "failed" || reported.job.status === "dead"
          ? "bad"
          : "good"
        : "default";

  return (
    <Card data-testid="repair-history-coverage">
      <CardHeader>
        <CardTitle>Repair history &amp; coverage</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4 lg:grid-cols-2">
        <section
          aria-label="Native repair feed status"
          className="flex flex-col gap-3 rounded-md border border-line bg-panel-2 p-4"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 className="text-sm font-medium text-ink">Native source feed</h4>
              <p className="text-xs text-ink-3">REPAIR_ORDERS</p>
            </div>
            {repairFeed ? (
              <Badge variant={connectionVariant(repairFeed.status)}>
                {FEED_STATUS_LABEL[repairFeed.status]}
              </Badge>
            ) : (
              <Badge variant="bad">Unavailable</Badge>
            )}
          </div>
          {repairFeed ? (
            <>
              <p className="text-xs leading-relaxed text-ink-2">
                {repairFeed.notes}
              </p>
              <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 text-xs">
                <dt className="text-ink-3">Backing domains</dt>
                <dd className="min-w-0 text-ink">
                  {repairFeed.domains.length > 0
                    ? repairFeed.domains.join(", ")
                    : "none"}
                </dd>
                <dt className="text-ink-3">Rows</dt>
                <dd className="tabular-nums text-ink">
                  {repairFeed.rows === null
                    ? "—"
                    : integerFormatter.format(repairFeed.rows)}
                </dd>
                <dt className="text-ink-3">Last sync</dt>
                <dd className="tabular-nums text-ink">
                  {repairFeed.last_sync ?? "—"}
                </dd>
              </dl>
            </>
          ) : (
            <p className="text-xs text-ink-2">
              The feed-health response did not include REPAIR_ORDERS, so its
              connection state is unavailable.
            </p>
          )}
        </section>

        <section
          aria-label="Self-serve repair history coverage"
          className="flex flex-col gap-3 rounded-md border border-line bg-panel-2 p-4"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h4 className="text-sm font-medium text-ink">
                Latest self-serve coverage
              </h4>
              <p className="text-xs text-ink-3">
                Upload and recompute validation evidence
              </p>
            </div>
            <Badge variant={coverageVariant}>{coverageLabel}</Badge>
          </div>

          {isPending ? (
            <p role="status" className="text-xs text-ink-2">
              Loading repair-history coverage…
            </p>
          ) : isError ? (
            <>
              <p role="alert" className="text-xs text-bad">
                Repair-history coverage could not be loaded. Counts remain
                unavailable.
              </p>
              <RepairCoverageCounts result={null} />
            </>
          ) : reported ? (
            <>
              <p className="text-xs text-ink-2">
                {`Latest reported coverage: ${repairCoverageJobLabel(reported.job)} #${reported.job.id} · `}
                <span className="tabular-nums">
                  {formatWhen(reported.job.created_at)}
                </span>
              </p>
              {reported.validationErrorCount !== null && (
                <p role="status" className="text-xs text-bad">
                  Validation failed with{" "}
                  {integerFormatter.format(reported.validationErrorCount)}{" "}
                  rejected row/error finding
                  {reported.validationErrorCount === 1 ? "" : "s"}. Fixed-shape
                  counts below remain evidence only; no failed batch was seeded.
                </p>
              )}
              <RepairCoverageCounts result={reported.result} />
              <dl className="grid gap-1 text-xs sm:grid-cols-[9rem_1fr]">
                <dt className="text-ink-3">Proxy definition</dt>
                <dd className="min-w-0 break-words text-ink">
                  <code>{reported.result?.proxy_definition ?? "—"}</code>
                </dd>
              </dl>
            </>
          ) : (
            <>
              <p className="text-xs leading-relaxed text-ink-2">
                No completed ingest has reported the optional repair-history
                coverage payload. Legacy results remain unavailable rather than
                being treated as zero.
              </p>
              <RepairCoverageCounts result={null} />
              <dl className="grid gap-1 text-xs sm:grid-cols-[9rem_1fr]">
                <dt className="text-ink-3">Proxy definition</dt>
                <dd className="text-ink">—</dd>
              </dl>
            </>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

/**
 * Data & Connections — Slice S7 (PRD §6.7, the last net-new vertical slice), sourced
 * from GET /v1/tenants/{tenant}/feeds via useFeeds(). Every displayed number flows
 * through Metric/ProvChip (docs/DESIGN-SYSTEM.md §4).
 *
 * This view tells the truth about what's actually connected: status/domains/notes
 * for all 13 spec feeds are derived from the real 21-domain nightly-extract registry
 * and what the recommendation-engine's extract_loader actually consumes — not a
 * spec-shaped fiction (see services/agent-spine/src/trax_io_spine/bff/feeds.py). Of
 * the 13 feeds, 4 are CONNECTED (extracted and consumed), 3 are PARTIAL (extracted
 * but not consumed, or structurally thin), and 6 are NOT_CONNECTED (no eMRO domain
 * wired at all in v1) — those honest counts are exactly what the health strip and
 * table below render, with no rounding up.
 *
 * The "part-statistics reference browser" (PRD §6.7) is NOT rebuilt here as a second
 * parallel browser — it's a search box that navigates straight to the existing Part
 * Drill-Down view (Slice S2), which already renders every derived metric with its
 * source/confidence via the provenance invariant. See PartStatSheetLookup.tsx.
 */
export function DataConnections() {
  const { role } = useAuth();
  const { data, isPending, isError, error, refetch, dataUpdatedAt } = useFeeds();
  const tenant = activeTenant();
  const repairHistoryQuery = useQuery<IngestHistoryItem[]>({
    queryKey: ingestHistoryQueryKey(tenant),
    queryFn: () => listIngests(tenant),
  });
  const [statusFilter, setStatusFilter] = useState<FeedStatusFilter>("all");

  if (isPending) {
    return <QueryLoading label="Loading data & connections…" />;
  }

  if (isError) {
    return <QueryError label="Failed to load feed health" error={error} onRetry={() => refetch()} />;
  }

  // Real fetch time, not render-time "now" — see Overview.tsx for why.
  const provenance = feedsProvenance(new Date(dataUpdatedAt));

  return (
    <div className="flex flex-col gap-6 p-6">
      <header>
        <h1 className="text-2xl font-semibold text-ink">Data & Connections</h1>
        <p className="text-sm text-ink-2">
          What's actually connected to eMRO today, feed by feed — coverage, freshness,
          and honest gaps.
        </p>
      </header>

      {/* Health strip */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Connected</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.health.connected, provenance)}
              format={integerFormatter.format}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Partial</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.health.partial, provenance)}
              format={integerFormatter.format}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Not connected</CardTitle>
          </CardHeader>
          <CardContent>
            <Metric
              metric={withProvenance(data.health.not_connected, provenance)}
              format={integerFormatter.format}
            />
          </CardContent>
        </Card>
      </div>

      <p className="text-xs text-ink-2">
        Extract date:{" "}
        <span className="tabular-nums text-ink">{data.health.extract_date ?? "—"}</span>
      </p>

      {/* 13-feed table */}
      <Card>
        <CardHeader>
          <CardTitle>Source feeds (13)</CardTitle>
        </CardHeader>
        <CardContent>
          <FeedTable rows={data.feeds} filter={statusFilter} onFilterChange={setStatusFilter} />
        </CardContent>
      </Card>

      <RepairHistoryCoverageCard
        repairFeed={data.feeds.find((feed) => feed.feed_id === "REPAIR_ORDERS")}
        history={repairHistoryQuery.data}
        isPending={repairHistoryQuery.isPending}
        isError={repairHistoryQuery.isError}
      />

      {/* C3 Task 6 + Phase 4 — upload all canonical files, including repair
          history. Role-gated: only planner/admin/owner see the upload card.
          Repair status/coverage above and IngestHistory below are visible to
          every authorized role. */}
      {canUpload(role) && (
        <Card>
          <CardHeader>
            <CardTitle>Upload data</CardTitle>
          </CardHeader>
          <CardContent>
            <UploadPanel />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Upload history</CardTitle>
        </CardHeader>
        <CardContent>
          <IngestHistory />
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recommended feeds to add</CardTitle>
          </CardHeader>
          <CardContent>
            <RecommendedFeeds rows={data.feeds} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Part statistics reference browser</CardTitle>
          </CardHeader>
          <CardContent>
            <PartStatSheetLookup />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
