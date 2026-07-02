import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  formatFreshness,
  provenanceStatus,
  type Provenance,
} from "@/lib/provenance";

const STATUS_LABEL: Record<ReturnType<typeof provenanceStatus>, string> = {
  good: "High confidence",
  warn: "Reduced confidence",
  bad: "Low confidence",
};

export interface ProvChipProps {
  provenance: Provenance;
  className?: string;
}

/**
 * The trust primitive (docs/DESIGN-SYSTEM.md §4). Renders a confidence/
 * coverage dot (never color-only — always paired with a text label per
 * WCAG 2.1 AA §6), the source system, and freshness. Wrapped in a tooltip
 * exposing the full provenance detail.
 */
export function ProvChip({ provenance, className }: ProvChipProps) {
  const status = provenanceStatus(provenance);
  const freshness = formatFreshness(provenance.freshnessAt);
  const coveragePct = Math.round(provenance.coverage * 100);

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant={status}
            className={className}
            data-testid="prov-chip"
            data-status={status}
            aria-label={`${STATUS_LABEL[status]}. Source ${provenance.source}, updated ${freshness}, ${coveragePct}% coverage.`}
          >
            <span
              aria-hidden="true"
              className="h-1.5 w-1.5 rounded-full bg-current"
            />
            <span>{provenance.source}</span>
            <span className="text-ink-3">·</span>
            <span>{freshness}</span>
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          <div className="flex flex-col gap-0.5">
            <span className="font-semibold">{provenance.source}</span>
            <span>System of record: {provenance.systemOfRecord}</span>
            <span>Freshness: {freshness}</span>
            <span>Coverage: {coveragePct}%</span>
            <span>Confidence: {Math.round(provenance.confidence * 100)}%</span>
            {provenance.derived && <span className="italic text-ink-3">Derived value</span>}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
