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

  return (
    <Card data-testid="recommendation-card">
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle className="text-base text-ink">
            {RECOMMENDATION_TYPE_LABEL[detail.type]} — {detail.pn} / {detail.location}
          </CardTitle>
          <p className="text-sm text-ink-2">{detail.description}</p>
        </div>
        <Badge variant="brand">Tier {detail.criticality_tier}</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {/* Reason */}
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-ink-3">Why</h4>
          <p className="text-sm text-ink">{detail.reason}</p>
          {detail.supporting_evidence.length > 0 && (
            <ul className="mt-2 flex flex-col gap-1 text-xs text-ink-2">
              {detail.supporting_evidence.map((ev) => (
                <li key={ev.ref_id}>
                  <span className="font-medium text-ink-2">{ev.kind}</span>: {ev.detail}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Impact */}
        <div className="flex flex-wrap gap-6">
          <Metric
            label="Impact"
            metric={withProvenance(detail.estimated_cost_impact, provenance)}
            format={currencyFormatter.format}
          />
          <Metric
            label="Recommended qty"
            metric={withProvenance(detail.recommended_quantity, provenance)}
            format={integerFormatter.format}
          />
          <div className="flex flex-col gap-1">
            <span className="text-xs text-ink-2">Confidence</span>
            <ConfidenceBar score={detail.confidence_score} />
          </div>
        </div>

        {/* Current vs proposed policy */}
        {(detail.current_policy || detail.proposed_policy) && (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div>
              <span className="text-xs text-ink-2">Current policy</span>
              <p className="text-sm text-ink">
                {detail.current_policy ? formatPolicy(detail.current_policy) : "—"}
              </p>
            </div>
            <div>
              <span className="text-xs text-ink-2">Proposed policy</span>
              <p className="text-sm text-ink">
                {detail.proposed_policy ? formatPolicy(detail.proposed_policy) : "—"}
              </p>
            </div>
          </div>
        )}

        {detail.guardrail_flags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {detail.guardrail_flags.map((flag) => (
              <Badge key={flag} variant="warn">
                {flag}
              </Badge>
            ))}
          </div>
        )}

        {/* Action */}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={onAccept} disabled={killSwitchEngaged || isAccepting}>
            Accept
          </Button>
          <Button variant="ghost" size="sm" onClick={onDismiss}>
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
      </CardContent>
    </Card>
  );
}
