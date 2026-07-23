import { useQueries } from "@tanstack/react-query";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { activeTenant, bffClient, downloadWithAuth, recommendationsExportUrl } from "@/lib/api/client";
import type { RecommendationDetail } from "@/lib/api/types";
import { useApprove, useKillSwitch, useQueue, useReject } from "@/lib/api/useRecommendations";
import { recommendationQueryKey } from "@/lib/api/useRecommendations";
import { CycleSummary } from "@/features/recommendations/CycleSummary";
import { DriverWeights } from "@/features/recommendations/DriverWeights";
import { RecommendationCard } from "@/features/recommendations/RecommendationCard";

const CARD_LIMIT = 10;
const PAGE_SIZE = 50;

/**
 * Slice S3 — AI Recommendations view (PRD §6.3): explainable cards
 * (rec → reason → action) built from `RecommendationDetail`, a cycle
 * summary (counts by type/AOG, derived from the queue), and a
 * "how the optimizer decides" driver panel derived from supporting
 * evidence (driver weights aren't on the wire — see DriverWeights.tsx).
 */
export function AiRecommendations() {
  const queueQuery = useQueue("pending", PAGE_SIZE, 0);
  const killSwitchQuery = useKillSwitch();
  const approveMutation = useApprove();
  const rejectMutation = useReject();

  const topRows = (queueQuery.data?.items ?? []).slice(0, CARD_LIMIT);

  const detailQueries = useQueries({
    queries: topRows.map((row) => ({
      queryKey: recommendationQueryKey(activeTenant(), row.recommendation_id),
      queryFn: () => bffClient.getRecommendation(row.recommendation_id, activeTenant()),
      enabled: topRows.length > 0,
    })),
  });

  if (queueQuery.isPending) {
    return <QueryLoading label="Loading recommendations…" />;
  }

  if (queueQuery.isError) {
    return (
      <QueryError
        label="Failed to load recommendations"
        error={queueQuery.error}
        onRetry={() => queueQuery.refetch()}
      />
    );
  }

  const details = detailQueries
    .map((q) => q.data)
    .filter((d): d is RecommendationDetail => Boolean(d));

  const engaged = killSwitchQuery.data?.engaged ?? false;

  return (
    <div className="flex flex-col gap-6 p-6 bg-bg min-h-screen">
      {/* Header */}
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-text">AI Recommendations</h1>
          <p className="text-sm text-text-muted mt-1">
            Recommendation → reason → action. Every suggestion is explained.
          </p>
        </div>
        <button
          className="px-3 py-2 text-sm font-medium border border-border bg-surface text-text rounded-md hover:bg-bg-secondary transition-colors"
          onClick={() =>
            void downloadWithAuth(
              recommendationsExportUrl({ status: "pending" }),
              "recommendations.csv",
            )
          }
        >
          Export CSV
        </button>
      </header>

      {/* Kill Switch Alert */}
      {engaged && (
        <div className="bg-warning bg-opacity-10 border border-warning text-warning px-4 py-3 rounded-md text-sm">
          Approvals are paused — the tenant kill switch is engaged.
        </div>
      )}

      {/* Cycle Summary & Driver Weights */}
      <CycleSummary rows={queueQuery.data.items} />
      <DriverWeights details={details} />

      {/* Recommendation Cards */}
      <div className="flex flex-col gap-4">
        {topRows.length === 0 ? (
          <div className="bg-info bg-opacity-10 border border-info text-info px-4 py-3 rounded-md text-sm">
            No pending recommendations. You're all caught up! 🎉
          </div>
        ) : details.length === 0 ? (
          <div className="bg-info bg-opacity-10 border border-info text-info px-4 py-3 rounded-md text-sm">
            Loading recommendation details…
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            {details.map((detail) => (
              <RecommendationCard
                key={detail.recommendation_id}
                detail={detail}
                onAccept={() => approveMutation.mutate(detail.recommendation_id)}
                onDismiss={() =>
                  rejectMutation.mutate({
                    recommendationId: detail.recommendation_id,
                    reason: "other",
                  })
                }
                isAccepting={approveMutation.isPending}
                killSwitchEngaged={engaged}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
