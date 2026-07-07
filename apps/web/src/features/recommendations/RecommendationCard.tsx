import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Metric } from "@/components/Metric";
import { ConfidenceBar } from "@/features/workbench/ConfidenceBar";
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
      <CardHeader className="flex flex-row items-start justify-between gap-4 pb-3">
        <div className="flex-1">
          <CardTitle className="text-base text-text">
            {RECOMMENDATION_TYPE_LABEL[detail.type]} — {detail.pn} @ {detail.location}
          </CardTitle>
          <p className="text-sm text-text-muted mt-1">{detail.description}</p>
        </div>
        <Badge className={`${tierBadgeClass} flex-shrink-0 whitespace-nowrap`}>
          Priority {detail.criticality_tier}
        </Badge>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-4">
        {/* Reason Section */}
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-text-muted">
            Why this recommendation?
          </h4>
          <p className="text-sm text-text mt-2">{detail.reason}</p>
          {detail.supporting_evidence.length > 0 && (
            <ul className="mt-3 flex flex-col gap-2 text-xs">
              {detail.supporting_evidence.map((ev) => (
                <li key={ev.ref_id} className="flex gap-2">
                  <span className="font-semibold text-info flex-shrink-0">✓</span>
                  <span>
                    <span className="font-medium text-text">{ev.kind}</span>
                    <span className="text-text-muted">: {ev.detail}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        <hr className="border-t border-border" />

        {/* Impact & Confidence */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <Metric
            label="Projected Impact"
            metric={withProvenance(detail.estimated_cost_impact, provenance)}
            format={currencyFormatter.format}
          />
          <Metric
            label="Recommended Qty"
            metric={withProvenance(detail.recommended_quantity, provenance)}
            format={integerFormatter.format}
          />
          <div className="flex flex-col gap-1">
            <span className="text-xs text-text-muted font-medium">Confidence</span>
            <ConfidenceBar score={detail.confidence_score} />
          </div>
        </div>

        <hr className="border-t border-border" />

        {/* Current vs Proposed Policy */}
        {(detail.current_policy || detail.proposed_policy) && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 bg-bg-secondary p-3 rounded-md">
            <div>
              <span className="text-xs font-semibold text-text-muted uppercase">Current Levels</span>
              <p className="text-sm text-text font-mono mt-1">
                {detail.current_policy ? formatPolicy(detail.current_policy) : "—"}
              </p>
            </div>
            <div>
              <span className="text-xs font-semibold text-text-muted uppercase">Proposed Levels</span>
              <p className="text-sm text-text font-mono mt-1">
                {detail.proposed_policy ? formatPolicy(detail.proposed_policy) : "—"}
              </p>
            </div>
          </div>
        )}

        {/* Guardrail Flags */}
        {detail.guardrail_flags.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {detail.guardrail_flags.map((flag) => (
              <Badge key={flag} className="bg-warning text-text text-xs">
                {flag}
              </Badge>
            ))}
          </div>
        )}

        <hr className="border-t border-border" />

        {/* Actions */}
        <div className="flex flex-wrap gap-2 justify-end">
          <Button
            onClick={onAccept}
            disabled={killSwitchEngaged || isAccepting}
            className="bg-success text-text hover:opacity-90"
          >
            {isAccepting ? "Approving…" : "Approve"}
          </Button>
          <Button
            onClick={onDismiss}
            variant="outline"
            className="border-border text-text hover:bg-bg-secondary"
          >
            Reject
          </Button>
          <Button
            disabled
            variant="outline"
            className="border-border text-text-muted hover:bg-bg-secondary"
            title="Editing proposed values before approval is coming soon"
          >
            Adjust (coming soon)
          </Button>
        </div>
        </div>
    </CardContent>
    </Card>
  );
}
