import { BreakdownTable } from "@/features/overview/BreakdownTable";
import { TopShortagesTable } from "@/features/overview/TopShortagesTable";
import type { DrillSpec } from "@/features/overview/drillSpecs";
import type { Provenance } from "@/lib/provenance";
import type { DashboardSummary } from "@/lib/api/types";

export interface DrillContentProps {
  spec: DrillSpec;
  data: DashboardSummary;
  provenance: Provenance;
}

/** Renders `spec.description` as a muted line above the label used in captions/empty states. */
function rowNounFor(spec: DrillSpec): string {
  switch (spec.breakdownKey) {
    case "by_criticality":
      return "criticality tier";
    case "by_ata":
      return "ATA chapter";
    case "by_part_class":
      return "part class";
    case "by_tier":
      return "autonomy tier";
    default:
      return "row";
  }
}

/**
 * Dispatches a `DrillSpec` to its content renderer: `breakdown` specs pull
 * their `Breakdown[]` off `data[spec.breakdownKey]` into a `BreakdownTable`;
 * the `shortages` spec renders `data.top_shortages` via `TopShortagesTable`.
 * `spec.description` always renders first, as the honest one-line context
 * for what the table below expands (the "not just the top N" callouts).
 */
export function DrillContent({ spec, data, provenance }: DrillContentProps) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-ink-2">{spec.description}</p>
      {spec.kind === "breakdown" && spec.breakdownKey ? (
        <BreakdownTable
          rows={data[spec.breakdownKey]}
          rowNoun={rowNounFor(spec)}
          labelFor={spec.labelFor}
          provenance={provenance}
        />
      ) : (
        <TopShortagesTable rows={data.top_shortages} provenance={provenance} />
      )}
    </div>
  );
}
