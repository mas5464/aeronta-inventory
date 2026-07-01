import { Badge } from "@/components/ui/badge";
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

/**
 * PRD §6.7 — "Source-feed table (13 feeds) ... filterable." Every column comes
 * straight from the BFF's honest FeedHealthRow: status/domains/notes are derived
 * from the code-verified feed->domain mapping (bff/feeds.py), rows/last_sync come
 * from the extract's manifest when available.
 */
export function FeedTable({ rows, filter, onFilterChange }: FeedTableProps) {
  const filtered = filterFeeds(rows, filter);

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

      {filtered.length === 0 ? (
        <p className="p-4 text-sm text-ink-2">No feeds match the current filter.</p>
      ) : (
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Source feed table — 13 canonical eMRO feeds</caption>
          <thead>
            <tr className="border-b border-line text-xs text-ink-2">
              <th scope="col" className="p-3 font-medium">
                Feed
              </th>
              <th scope="col" className="p-3 font-medium">
                Status
              </th>
              <th scope="col" className="p-3 font-medium">
                Backing eMRO domains
              </th>
              <th scope="col" className="p-3 font-medium">
                Rows
              </th>
              <th scope="col" className="p-3 font-medium">
                Last sync
              </th>
              <th scope="col" className="p-3 font-medium">
                Notes
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((row) => (
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
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
