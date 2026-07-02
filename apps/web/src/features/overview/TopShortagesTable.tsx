import { Link } from "react-router-dom";
import { Metric } from "@/components/Metric";
import { SortHeader } from "@/components/table/SortHeader";
import { TableCaption, EmptyRow } from "@/components/table/TableChrome";
import { useTableState } from "@/lib/table/useTableState";
import { withProvenance, type Provenance } from "@/lib/provenance";
import type { PartShortfall } from "@/lib/api/types";

export interface TopShortagesTableProps {
  rows: PartShortfall[];
  /** Stamped onto every numeric cell — the same object Overview already computes once. */
  provenance: Provenance;
}

type ShortageSort = "pn" | "location" | "on_hand" | "shortage" | "projected_demand";

const integerFormatter = new Intl.NumberFormat("en-US");

/**
 * Full `top_shortages` table for the priority-actions drill panel — every
 * row the `PriorityActionsPreview` card truncates to its top 5, each
 * linking into the Part Drill-Down exactly like the preview card does.
 * Sortable via the shared table primitives (default: shortage desc, same
 * ranking the preview card already uses).
 */
export function TopShortagesTable({ rows, provenance }: TopShortagesTableProps) {
  const table = useTableState<PartShortfall, ShortageSort>({
    rows,
    sortAccessors: {
      pn: (row) => row.pn,
      location: (row) => row.location,
      on_hand: (row) => row.on_hand,
      shortage: (row) => row.shortage,
      projected_demand: (row) => row.projected_demand,
    },
    defaultSort: "shortage",
    defaultDir: "desc",
  });

  return (
    <table className="w-full text-sm">
      <TableCaption>Full top-shortages list</TableCaption>
      <thead>
        <tr className="border-b border-line text-left text-xs text-ink-2">
          <SortHeader<ShortageSort>
            column="pn"
            label="Part"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            className="pb-2 pr-3"
          />
          <SortHeader<ShortageSort>
            column="location"
            label="Location"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            className="pb-2 pr-3"
          />
          <SortHeader<ShortageSort>
            column="on_hand"
            label="On-hand"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            align="right"
            className="pb-2 pr-3"
          />
          <SortHeader<ShortageSort>
            column="shortage"
            label="Shortage"
            activeSort={table.sort}
            dir={table.dir}
            onSort={table.setSort}
            align="right"
            className="pb-2 pr-3"
          />
          <SortHeader<ShortageSort>
            column="projected_demand"
            label="Projected demand"
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
          <EmptyRow colSpan={5}>No shortages — nothing to prioritize right now.</EmptyRow>
        ) : (
          table.rows.map((row) => (
            <tr key={`${row.pn}-${row.location}`} className="border-b border-line/60 last:border-0">
              <td className="py-2 pr-3 font-medium">
                <Link
                  to={`/parts/${encodeURIComponent(row.pn)}/${encodeURIComponent(row.location)}`}
                  className="text-brand hover:underline"
                >
                  {row.pn}
                </Link>
              </td>
              <td className="py-2 pr-3 text-ink-2">{row.location}</td>
              <td className="py-2 pr-3 text-right">
                <Metric
                  metric={withProvenance(row.on_hand, provenance)}
                  format={integerFormatter.format}
                />
              </td>
              <td className="py-2 pr-3 text-right">
                <Metric
                  metric={withProvenance(row.shortage, provenance)}
                  format={integerFormatter.format}
                />
              </td>
              <td className="py-2 text-right">
                <Metric
                  metric={withProvenance(row.projected_demand, provenance)}
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
