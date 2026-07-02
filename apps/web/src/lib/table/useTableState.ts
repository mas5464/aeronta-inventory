import { useMemo, useState } from "react";

export interface TableStateConfig<TRow, TSort extends string> {
  rows: TRow[];
  sortAccessors: Record<TSort, (row: TRow) => string | number>;
  defaultSort: TSort;
  defaultDir?: "asc" | "desc";
  searchAccessor?: (row: TRow) => string;
}

export interface TableState<TRow, TSort extends string> {
  rows: TRow[];
  sort: TSort;
  dir: "asc" | "desc";
  search: string;
  setSort: (col: TSort) => void;
  setSearch: (q: string) => void;
}

/**
 * Sorts `rows` by the accessor for `sort`, stably. Exported and unit-tested
 * without React so both `useTableState` and any future non-hook caller share
 * one implementation.
 *
 * - string results compare via `localeCompare`.
 * - number results compare via subtraction.
 * - null/undefined accessor results always sort last, in BOTH directions —
 *   "last" here means last in the rendered order, not merely "greatest",
 *   so a `desc` sort does not resurface them at the top.
 */
export function sortRows<TRow, TSort extends string>(
  rows: TRow[],
  accessors: Record<TSort, (row: TRow) => string | number>,
  sort: TSort,
  dir: "asc" | "desc",
): TRow[] {
  const accessor = accessors[sort];
  const decorated = rows.map((row, index) => ({ row, index, key: accessor(row) }));

  decorated.sort((a, b) => {
    const aMissing = a.key === null || a.key === undefined;
    const bMissing = b.key === null || b.key === undefined;

    if (aMissing && bMissing) return a.index - b.index;
    if (aMissing) return 1;
    if (bMissing) return -1;

    let cmp: number;
    if (typeof a.key === "string" && typeof b.key === "string") {
      cmp = a.key.localeCompare(b.key);
    } else {
      cmp = (a.key as number) - (b.key as number);
    }

    if (cmp === 0) return a.index - b.index;
    return dir === "asc" ? cmp : -cmp;
  });

  return decorated.map((entry) => entry.row);
}

/** Filters rows whose `searchAccessor` haystack case-insensitively includes `query`. */
function searchRows<TRow>(
  rows: TRow[],
  searchAccessor: ((row: TRow) => string) | undefined,
  query: string,
): TRow[] {
  if (!searchAccessor || query.trim() === "") return rows;
  const needle = query.toLowerCase();
  return rows.filter((row) => searchAccessor(row).toLowerCase().includes(needle));
}

/**
 * Client-side table state: sort column/direction + search query, applied to
 * `config.rows` in one pass (search first, then sort — matching how a user
 * reasons about "narrow, then order"). Pure filtering/sorting logic lives in
 * `sortRows`/`searchRows` so it stays testable without mounting React.
 */
export function useTableState<TRow, TSort extends string>(
  config: TableStateConfig<TRow, TSort>,
): TableState<TRow, TSort> {
  const { rows, sortAccessors, defaultSort, defaultDir = "asc", searchAccessor } = config;

  const [sort, setSortState] = useState<TSort>(defaultSort);
  const [dir, setDir] = useState<"asc" | "desc">(defaultDir);
  const [search, setSearch] = useState("");

  const setSort = (col: TSort) => {
    if (col === sort) {
      setDir((prev) => (prev === "asc" ? "desc" : "asc"));
      return;
    }
    // New column: default direction depends on the accessor's value type,
    // sampled from the current rows (numeric columns start high-to-low;
    // string columns start A-to-Z). Falls back to "asc" when there is no
    // row to sample (empty table) — a sensible, deterministic default.
    const sample = rows.find((row) => {
      const value = sortAccessors[col](row);
      return value !== null && value !== undefined;
    });
    const sampledValue = sample ? sortAccessors[col](sample) : undefined;
    setDir(typeof sampledValue === "number" ? "desc" : "asc");
    setSortState(col);
  };

  const visibleRows = useMemo(() => {
    const searched = searchRows(rows, searchAccessor, search);
    return sortRows(searched, sortAccessors, sort, dir);
  }, [rows, searchAccessor, search, sortAccessors, sort, dir]);

  return {
    rows: visibleRows,
    sort,
    dir,
    search,
    setSort,
    setSearch,
  };
}
