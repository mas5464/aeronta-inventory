import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { cn } from "@/lib/utils";

export interface SortHeaderProps<TSort extends string> {
  column: TSort;
  label: string;
  activeSort: TSort;
  dir: "asc" | "desc";
  onSort: (col: TSort) => void;
  align?: "left" | "right";
  className?: string;
}

/**
 * A sortable `<th>` — a native `<button>` wrapping the label + direction icon,
 * so it's keyboard/click accessible with zero extra ARIA wiring beyond
 * `aria-sort` on the header cell itself (the WAI-ARIA "sortable table"
 * pattern). Icons are `aria-hidden`; the button text alone conveys the label.
 */
export function SortHeader<TSort extends string>({
  column,
  label,
  activeSort,
  dir,
  onSort,
  align = "left",
  className,
}: SortHeaderProps<TSort>) {
  const active = column === activeSort;
  const ariaSort = active ? (dir === "asc" ? "ascending" : "descending") : "none";

  const Icon = active ? (dir === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;

  return (
    <th
      scope="col"
      aria-sort={ariaSort}
      className={cn("p-3 font-medium", align === "right" && "text-right", className)}
    >
      <button
        type="button"
        onClick={() => onSort(column)}
        className={cn(
          "inline-flex items-center gap-1 rounded-control p-0 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-2 hover:text-ink",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
          align === "right" && "flex-row-reverse",
        )}
      >
        <span>{label}</span>
        <Icon aria-hidden="true" className="h-3.5 w-3.5" />
      </button>
    </th>
  );
}
