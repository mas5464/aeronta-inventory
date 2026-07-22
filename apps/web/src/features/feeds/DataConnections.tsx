import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Metric } from "@/components/Metric";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { useFeeds } from "@/lib/api/useFeeds";
import { feedsProvenance } from "@/lib/feedsProvenance";
import { withProvenance } from "@/lib/provenance";
import { FeedTable } from "@/features/feeds/FeedTable";
import { RecommendedFeeds } from "@/features/feeds/RecommendedFeeds";
import { PartStatSheetLookup } from "@/features/feeds/PartStatSheetLookup";
import { UploadPanel } from "@/features/feeds/UploadPanel";
import { IngestHistory } from "@/features/feeds/IngestHistory";
import type { FeedStatusFilter } from "@/features/feeds/feedTableView";

const integerFormatter = new Intl.NumberFormat("en-US");

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
  const { data, isPending, isError, error, refetch, dataUpdatedAt } = useFeeds();
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
        <h1 className="text-xl font-semibold text-ink">Data & Connections</h1>
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

      {/* C3 Task 6 — upload the six canonical files (parts, stock, demand_history,
          locations, open_orders, vendors) straight to Supabase Storage, then run an
          ingest job. Role-gated inside UploadPanel itself; IngestHistory below is
          visible to every role. */}
      <Card>
        <CardHeader>
          <CardTitle>Upload data</CardTitle>
        </CardHeader>
        <CardContent>
          <UploadPanel />
        </CardContent>
      </Card>

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
