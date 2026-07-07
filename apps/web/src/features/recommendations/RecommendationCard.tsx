import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Metric } from "@/components/Metric";
import { RECOMMENDATION_TYPE_LABEL } from "@/features/workbench/queueView";
import type { RecommendationDetail } from "@/lib/api/types";
import { recommendationProvenance } from "@/lib/recommendationsProvenance";
import { withProvenance } from "@/lib/provenance";

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
const integerFormatter = new Intl.NumberFormat("en-US");

function formatPolicy(p: { rop: number; eoq: number; safety_stock: number; max_stock: number }) {
  return `ROP ${p.rop} · EOQ ${p.eoq} · SS ${p.safety_stock} · Max ${p.max_stock}`;
}

export interface RecommendationCardProps {
  detail: RecommendationDetail;
  onAccept: () => void;
  onDismiss: () => void;
  isAccepting?: boolean;
  killSwitchEngaged?: boolean;
}

/**
 * "Explainable card": rec → reason → action (PRD §6.3). Built from
 * `RecommendationDetail` — reason, supporting evidence, proposed vs current
 * policy. Accept = approve; Dismiss = reject; Adjust/override is disabled
 * (BFF has no edit-before-accept endpoint — approve writes the computed
 * policy as-is).
 */
export function RecommendationCard({
  detail,
  onAccept,
  onDismiss,
  isAccepting,
  killSwitchEngaged,
}: RecommendationCardProps) {
  const provenance = recommendationProvenance();

  // Determine tier badge color based on criticality (1=danger, 2=warning, 3+=info)
  const tierBadgeClass =
    detail.criticality_tier === 1 ? "bg-error text-text" :
    detail.criticality_tier === 2 ? "bg-warning text-text" :
    "bg-info text-text";

  return (
    <Card
      data-testid="recommendation-card"
      className="border border-border bg-surface shadow-md rounded-lg"
    >
      <CardHeader className="border-b border-border pb-4">
        <div className="flex items-start justify-between gap-4 mb-2">
          <div className="flex-1">
            <CardTitle className="text-lg font-semibold text-text">
              {RECOMMENDATION_TYPE_LABEL[detail.type]} — {detail.pn} @ {detail.location}
            </CardTitle>
          </div>
          <Badge className={`${tierBadgeClass} flex-shrink-0 whitespace-nowrap text-xs font-semibold px-2 py-1`}>
            Priority {detail.criticality_tier}
          </Badge>
        </div>
        <p className="text-sm text-text-muted">{detail.description}</p>
      </CardHeader>
      <CardContent className="pt-6">
        {/* Confidence + Reason Section (2-column) */}
        <div className="flex gap-6 mb-6">
          {/* Left: Confidence Score */}
          <div className="flex flex-col items-center justify-start gap-2 py-2 flex-shrink-0">
            <div className="text-5xl font-bold bg-gradient-to-r from-info via-success to-brand bg-clip-text text-transparent">
              {Math.round(detail.confidence_score * 100)}%
            </div>
            <span className="text-xs text-text-muted font-medium uppercase tracking-widest text-center">Confidence score</span>
          </div>

          {/* Right: Reason Section */}
          <div className="flex-1">
            <h4 className="text-xs font-semibold uppercase tracking-widest text-text-muted mb-2">
              Why this recommendation?
            </h4>
            <p className="text-sm text-text leading-relaxed mb-3">{detail.reason}</p>
            {detail.supporting_evidence.length > 0 && (
              <ul className="flex flex-col gap-2">
                {detail.supporting_evidence.map((ev) => (
                  <li key={ev.ref_id} className="flex gap-2 text-sm">
                    <span className="font-semibold text-success flex-shrink-0">✓</span>
                    <span className="flex-1">
                      <span className="font-medium text-text">{ev.kind}</span>
                      <span className="text-text-muted">: {ev.detail}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        {/* Impact & Qty Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
          <div>
            <Metric
              label="Projected Impact"
              metric={withProvenance(detail.estimated_cost_impact, provenance)}
              format={currencyFormatter.format}
            />
          </div>
          <div>
            <Metric
              label="Recommended Qty"
              metric={withProvenance(detail.recommended_quantity, provenance)}
              format={integerFormatter.format}
            />
          </div>
        </div>

        {/* Guardrail Flags */}
        {detail.guardrail_flags.length > 0 && (
          <>
            <div className="flex flex-wrap gap-2 mb-6">
              {detail.guardrail_flags.map((flag) => (
                <Badge key={flag} className="bg-warning text-text text-xs px-2 py-1">
                  {flag}
                </Badge>
              ))}
            </div>
          </>
        )}

        {/* Current vs Proposed Policy (collapsed) */}
        {(detail.current_policy || detail.proposed_policy) && (
          <details className="mb-6 text-sm">
            <summary className="cursor-pointer text-text-muted hover:text-text transition-colors font-medium">
              Inventory Levels (Current vs Proposed)
            </summary>
            <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
              <div>
                <p className="text-text-muted uppercase font-semibold mb-1">Current</p>
                <p className="text-text font-mono">
                  {detail.current_policy ? formatPolicy(detail.current_policy) : "—"}
                </p>
              </div>
              <div>
                <p className="text-text-muted uppercase font-semibold mb-1">Proposed</p>
                <p className="text-text font-mono">
                  {detail.proposed_policy ? formatPolicy(detail.proposed_policy) : "—"}
                </p>
              </div>
            </div>
          </details>
        )}

        {/* Actions */}
        <div className="flex flex-wrap gap-3 justify-end pt-4 border-t border-border">
          <Button
            onClick={onAccept}
            disabled={killSwitchEngaged || isAccepting}
            className="bg-success hover:bg-success/90 text-text font-semibold px-4 py-2"
          >
            {isAccepting ? "Approving…" : "Approve"}
          </Button>
          <Button
            onClick={onDismiss}
            variant="outline"
            className="border border-border text-text hover:bg-bg-secondary font-medium px-4 py-2"
          >
            Reject
          </Button>
          <Button
            disabled
            variant="outline"
            className="border border-border text-text-muted hover:bg-bg-secondary font-medium px-4 py-2 opacity-60"
            title="Editing proposed values before approval is coming soon"
          >
            Adjust (coming soon)
          </Button>
        </div>
    </CardContent>
    </Card>
  );
}
