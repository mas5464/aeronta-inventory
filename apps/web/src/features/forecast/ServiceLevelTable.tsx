import { SortHeader } from "@/components/table/SortHeader";
import { TableCaption, EmptyRow } from "@/components/table/TableChrome";
import { useTableState } from "@/lib/table/useTableState";
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

type ServiceLevelSort = "criticality_tier" | "target_service_level" | "actual_coverage" | "sku_count";

/**
 * PRD §6.6 — "Differentiated SL policy by criticality band ... target vs actual vs
 * SKU count." Target is the real onboarding policy
 * (`TenantPolicyConfig.service_level_by_tier`); SKU count is the real per-tier count
 * of (PN, Location) keys; "actual" is the honest on-hand-vs-shortage coverage proxy
 * (same technique as the Overview's SlInvestmentPanel) — not a true fill-rate metric.
 */
export function ServiceLevelTable({ bands }: ServiceLevelTableProps) {
  const table = useTableState<ServiceLevelBand, ServiceLevelSort>({
    rows: bands,
    sortAccessors: {
      criticality_tier: (band) => band.criticality_tier,
      target_service_level: (band) => band.target_service_level,
      // actual_coverage is nullable: the accessor signature is `string | number`
      // with no `null`, but `sortRows` explicitly handles null/undefined at
      // runtime (sorts last, both directions) — cast once to the declared type.
      actual_coverage: (band) => band.actual_coverage as number,
      sku_count: (band) => band.sku_count,
    },
    defaultSort: "criticality_tier",
  });

  return (
    <table className="w-full text-sm">
      <TableCaption>Differentiated service-level policy by criticality tier</TableCaption>
      <thead>
        <tr className="border-b border-line text-left text-xs text-ink-2">
          <SortHeader<ServiceLevelSort>
            column="criticality_tier"
            label="Criticality tier"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            className="pb-2 pr-3"
          />
          <SortHeader<ServiceLevelSort>
            column="target_service_level"
            label="Target SL"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            className="pb-2 pr-3"
          />
          <SortHeader<ServiceLevelSort>
            column="actual_coverage"
            label="Actual coverage (proxy)"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            className="pb-2 pr-3"
          />
          <SortHeader<ServiceLevelSort>
            column="sku_count"
            label="SKUs"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            className="pb-2"
          />
        </tr>
      </thead>
      <tbody>
        {table.rows.length === 0 ? (
          <EmptyRow colSpan={4}>No service-level policy data available.</EmptyRow>
        ) : (
          table.rows.map((band) => (
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
          ))
        )}
      </tbody>
    </table>
  );
}
