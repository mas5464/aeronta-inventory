import { ProvChip } from "@/components/ProvChip";
import type { MetricValue } from "@/lib/provenance";
import { cn } from "@/lib/utils";

export interface MetricProps<T> {
  /**
   * The metric to render. This is intentionally typed as `MetricValue<T>`,
   * never a bare `T` — a metric literally cannot be constructed or rendered
   * in this UI without its provenance travelling with it. See
   * docs/DESIGN-SYSTEM.md §4 and src/lib/provenance.ts.
   */
  metric: MetricValue<T>;
  label?: string;
  format?: (value: T) => string;
  className?: string;
}

/** Labeled metric — value + its ProvChip. The provenance invariant made visible. */
export function Metric<T>({ metric, label, format, className }: MetricProps<T>) {
  const display = format ? format(metric.value) : String(metric.value);

  return (
    <span className={cn("inline-flex flex-col gap-1", className)} data-testid="metric">
      {label && <span className="text-xs text-ink-2">{label}</span>}
      <b className="text-2xl font-semibold text-ink">{display}</b>
      <ProvChip provenance={metric.provenance} />
    </span>
  );
}
