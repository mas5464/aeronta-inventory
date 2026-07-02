import { Link } from "react-router-dom";
import type { PartShortfall } from "@/lib/api/types";

export interface PriorityActionsPreviewProps {
  /** `top_shortages` from the dashboard summary. */
  shortages: PartShortfall[];
  /** Max rows to preview before pointing at the full Workbench. */
  limit?: number;
}

const integerFormatter = new Intl.NumberFormat("en-US");

/**
 * Compact preview of the highest-shortage parts (docs PRD §6.1 "Priority
 * actions preview"), each row linking into the Part Drill-Down, with a
 * "view all" link into the Workbench for the full ranked worklist.
 */
export function PriorityActionsPreview({ shortages, limit = 5 }: PriorityActionsPreviewProps) {
  if (shortages.length === 0) {
    return <p className="text-sm text-ink-2">No shortages — nothing to prioritize right now.</p>;
  }

  const preview = shortages.slice(0, limit);

  return (
    <div className="flex flex-col gap-3">
      <ul className="flex flex-col divide-y divide-line">
        {preview.map((row) => (
          <li key={`${row.pn}-${row.location}`} className="flex items-center justify-between gap-3 py-2 text-sm">
            <div className="min-w-0">
              <Link
                to={`/parts/${encodeURIComponent(row.pn)}/${encodeURIComponent(row.location)}`}
                className="font-medium text-brand hover:underline"
              >
                {row.pn}
              </Link>
              <span className="ml-2 text-ink-2">{row.location}</span>
            </div>
            <div className="flex shrink-0 gap-4 text-right tabular-nums text-ink-2">
              <span>
                <span className="text-bad">{integerFormatter.format(row.shortage)}</span> short
              </span>
              <span>{integerFormatter.format(row.on_hand)} on-hand</span>
              <span>{integerFormatter.format(row.projected_demand)} demand</span>
            </div>
          </li>
        ))}
      </ul>
      <Link to="/workbench" className="self-start text-sm font-medium text-brand hover:underline">
        View all in Workbench →
      </Link>
    </div>
  );
}
