import { Badge } from "@/components/ui/badge";
import { SortHeader } from "@/components/table/SortHeader";
import { TableCaption, EmptyRow } from "@/components/table/TableChrome";
import { useTableState } from "@/lib/table/useTableState";
import type { FeedHealthRow } from "@/lib/api/types";
import {
  FEED_STATUS_LABEL,
  filterFeeds,
  type FeedStatusFilter,
} from "@/features/feeds/feedTableView";

export interface FeedTableProps {
  rows: FeedHealthRow[];
  filter: FeedStatusFilter;
  onFilterChange: (filter: FeedStatusFilter) => void;
}

const FILTER_OPTIONS: FeedStatusFilter[] = ["all", "connected", "partial", "not_connected"];

function statusVariant(status: FeedHealthRow["status"]): "good" | "warn" | "bad" {
  if (status === "connected") return "good";
  if (status === "partial") return "warn";
  return "bad";
}

const integerFormatter = new Intl.NumberFormat("en-US");

/** Sortable columns. "specOrder" is a synthetic column (index in the row array
 * as it arrives from the BFF, i.e. today's canonical FeedId order) — it is
 * never rendered as its own header, only used as the default sort so the
 * initial render is byte-identical to the table's pre-sort behavior. */
type FeedSort = "specOrder" | "name" | "status" | "rows" | "last_sync";

/**
 * PRD §6.7 — "Source-feed table (13 feeds) ... filterable." Every column comes
 * straight from the BFF's honest FeedHealthRow: status/domains/notes are derived
 * from the code-verified feed->domain mapping (bff/feeds.py), rows/last_sync come
 * from the extract's manifest when available.
 */
export function FeedTable({ rows, filter, onFilterChange }: FeedTableProps) {
  const filtered = filterFeeds(rows, filter);

  // Captured on the pre-sort (filtered, but not yet sorted) array so
  // "specOrder" always reproduces the canonical FeedId order the BFF sends,
  // regardless of which filter is active.
  const specOrderByFeedId = new Map(filtered.map((row, index) => [row.feed_id, index]));

  const table = useTableState<FeedHealthRow, FeedSort>({
    rows: filtered,
    sortAccessors: {
      specOrder: (row) => specOrderByFeedId.get(row.feed_id) ?? 0,
      name: (row) => row.name,
      status: (row) => row.status,
      // rows/last_sync are nullable columns: the accessor signature is
      // `string | number` with no `null`, but `sortRows` explicitly handles
      // null/undefined at runtime (sorts last, both directions) — cast once
      // to the declared type rather than pretend the column can't be null.
      rows: (row) => row.rows as number,
      last_sync: (row) => row.last_sync as string,
    },
    defaultSort: "specOrder",
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter feeds by status">
        {FILTER_OPTIONS.map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => onFilterChange(option)}
            aria-pressed={filter === option}
            data-active={filter === option}
            className="rounded-full border border-line px-3 py-1 text-xs font-medium data-[active=true]:bg-brand data-[active=true]:text-white"
          >
            {option === "all" ? "All feeds" : FEED_STATUS_LABEL[option]}
          </button>
        ))}
      </div>

      <table className="w-full text-left text-sm">
        <TableCaption>Source feed table — 13 canonical eMRO feeds</TableCaption>
        <thead>
          <tr className="border-b border-line text-xs text-ink-2">
            <SortHeader<FeedSort>
              column="name"
              label="Feed"
              activeSort={table.sort}
              dir={table.dir}
              onSort={table.setSort}
            />
            <SortHeader<FeedSort>
              column="status"
              label="Status"
              activeSort={table.sort}
              dir={table.dir}
              onSort={table.setSort}
            />
            <th scope="col" className="p-3 font-medium">
              Backing eMRO domains
            </th>
            <SortHeader<FeedSort>
              column="rows"
              label="Rows"
              activeSort={table.sort}
              dir={table.dir}
              onSort={table.setSort}
            />
            <SortHeader<FeedSort>
              column="last_sync"
              label="Last sync"
              activeSort={table.sort}
              dir={table.dir}
              onSort={table.setSort}
            />
            <th scope="col" className="p-3 font-medium">
              Notes
            </th>
          </tr>
        </thead>
        <tbody>
          {table.rows.length === 0 ? (
            <EmptyRow colSpan={6}>No feeds match the current filter.</EmptyRow>
          ) : (
            table.rows.map((row) => (
              <tr key={row.feed_id} className="border-t border-line align-top">
                <td className="p-3">
                  <div className="font-medium text-ink">{row.name}</div>
                  <div className="text-xs text-ink-3">{row.feed_id}</div>
                </td>
                <td className="p-3">
                  <Badge variant={statusVariant(row.status)}>{FEED_STATUS_LABEL[row.status]}</Badge>
                </td>
                <td className="p-3 text-ink-2">
                  {row.domains.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {row.domains.map((domain) => (
                        <code key={domain} className="rounded bg-panel-2 px-1.5 py-0.5 text-xs">
                          {domain}
                        </code>
                      ))}
                    </div>
                  ) : (
                    <span className="text-ink-3">none</span>
                  )}
                </td>
                <td className="p-3 tabular-nums text-ink-2">
                  {row.rows === null ? "—" : integerFormatter.format(row.rows)}
                </td>
                <td className="p-3 tabular-nums text-ink-2">{row.last_sync ?? "—"}</td>
                <td className="p-3 max-w-xs text-xs text-ink-2">{row.notes}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
