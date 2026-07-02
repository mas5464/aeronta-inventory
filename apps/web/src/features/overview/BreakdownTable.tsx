import { Metric } from "@/components/Metric";
import { SortHeader } from "@/components/table/SortHeader";
import { TableCaption, EmptyRow } from "@/components/table/TableChrome";
import { useTableState } from "@/lib/table/useTableState";
import { withProvenance, type Provenance } from "@/lib/provenance";
import type { Breakdown } from "@/lib/api/types";

export interface BreakdownTableProps {
  rows: Breakdown[];
  /** Table caption / empty-state noun, e.g. "criticality tier", "ATA chapter". */
  rowNoun: string;
  labelFor?: (key: string) => string;
  /** Stamped onto every numeric cell — the same object Overview already computes once. */
  provenance: Provenance;
}

type BreakdownSort = "key" | "count" | "on_hand" | "shortage";

const integerFormatter = new Intl.NumberFormat("en-US");

/**
 * One table for every `Breakdown[]` a `DrillSpec` can point at
 * (by_criticality / by_ata / by_part_class / by_tier) — columns label/
 * count/on-hand/shortage, sortable via the shared `useTableState` +
 * `SortHeader` primitives (default: shortage desc, since shortage is the
 * risk signal a planner most wants ranked first). Every numeric cell is
 * `Metric`-wrapped with the dashboard's provenance stamp — the provenance
 * invariant applies inside drill panels exactly as it does on the KPI cards.
 */
export function BreakdownTable({ rows, rowNoun, labelFor, provenance }: BreakdownTableProps) {
  const table = useTableState<Breakdown, BreakdownSort>({
    rows,
    sortAccessors: {
      key: (row) => (labelFor ? labelFor(row.key) : row.key),
      count: (row) => row.count,
      on_hand: (row) => row.on_hand,
      shortage: (row) => row.shortage,
    },
    defaultSort: "shortage",
    defaultDir: "desc",
  });

  return (
    <table className="w-full text-sm">
      <TableCaption>{`Full breakdown by ${rowNoun}`}</TableCaption>
      <thead>
        <tr className="border-b border-line text-left text-xs text-ink-2">
          <SortHeader<BreakdownSort>
            column="key"
            label="Label"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            className="pb-2 pr-3"
          />
          <SortHeader<BreakdownSort>
            column="count"
            label="Parts"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            align="right"
            className="pb-2 pr-3"
          />
          <SortHeader<BreakdownSort>
            column="on_hand"
            label="On-hand"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            align="right"
            className="pb-2 pr-3"
          />
          <SortHeader<BreakdownSort>
            column="shortage"
            label="Shortage"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            align="right"
            className="pb-2"
          />
        </tr>
      </thead>
      <tbody>
        {table.rows.length === 0 ? (
          <EmptyRow colSpan={4}>{`No ${rowNoun} breakdown data available.`}</EmptyRow>
        ) : (
          table.rows.map((row) => (
            <tr key={row.key} className="border-b border-line/60 last:border-0">
              <td className="py-2 pr-3 font-medium text-ink">
                {labelFor ? labelFor(row.key) : row.key}
              </td>
              <td className="py-2 pr-3 text-right">
                <Metric
                  metric={withProvenance(row.count, provenance)}
                  format={integerFormatter.format}
                />
              </td>
              <td className="py-2 pr-3 text-right">
                <Metric
                  metric={withProvenance(row.on_hand, provenance)}
                  format={integerFormatter.format}
                />
              </td>
              <td className="py-2 text-right">
                <Metric
                  metric={withProvenance(row.shortage, provenance)}
                  format={integerFormatter.format}
                />
              </td>
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}
