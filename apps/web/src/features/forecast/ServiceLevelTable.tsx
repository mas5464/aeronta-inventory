import type { ServiceLevelBand } from "@/lib/api/types";

export interface ServiceLevelTableProps {
  bands: ServiceLevelBand[];
}

const CRITICALITY_LABEL: Record<number, string> = {
  1: "No-Go (MEL)",
  2: "Go-If",
  3: "Routine",
  4: "Non-critical",
  5: "Excess-prone",
};

const pctFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});

const integerFormatter = new Intl.NumberFormat("en-US");

/**
 * PRD §6.6 — "Differentiated SL policy by criticality band ... target vs actual vs
 * SKU count." Target is the real onboarding policy
 * (`TenantPolicyConfig.service_level_by_tier`); SKU count is the real per-tier count
 * of (PN, Location) keys; "actual" is the honest on-hand-vs-shortage coverage proxy
 * (same technique as the Overview's SlInvestmentPanel) — not a true fill-rate metric.
 */
export function ServiceLevelTable({ bands }: ServiceLevelTableProps) {
  if (bands.length === 0) {
    return <p className="text-sm text-ink-2">No service-level policy data available.</p>;
  }

  return (
    <table className="w-full text-sm">
      <caption className="sr-only">Differentiated service-level policy by criticality tier</caption>
      <thead>
        <tr className="border-b border-line text-left text-xs text-ink-2">
          <th scope="col" className="pb-2 pr-3 font-medium">
            Criticality tier
          </th>
          <th scope="col" className="pb-2 pr-3 font-medium">
            Target SL
          </th>
          <th scope="col" className="pb-2 pr-3 font-medium">
            Actual coverage (proxy)
          </th>
          <th scope="col" className="pb-2 font-medium">
            SKUs
          </th>
        </tr>
      </thead>
      <tbody>
        {bands.map((band) => (
          <tr key={band.criticality_tier} className="border-b border-line/60 last:border-0">
            <td className="py-2 pr-3 font-medium text-ink">
              Tier {band.criticality_tier} — {CRITICALITY_LABEL[band.criticality_tier] ?? "—"}
            </td>
            <td className="py-2 pr-3 tabular-nums text-ink">
              {pctFormatter.format(band.target_service_level)}
            </td>
            <td className="py-2 pr-3 tabular-nums text-ink-2">
              {band.actual_coverage === null ? "—" : pctFormatter.format(band.actual_coverage)}
            </td>
            <td className="py-2 tabular-nums text-ink-2">
              {integerFormatter.format(band.sku_count)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
