import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { sortRows, useTableState } from "@/lib/table/useTableState";

interface Row {
  id: string;
  name: string;
  count: number | null;
  label: string | undefined;
}

const ROWS: Row[] = [
  { id: "a", name: "Bravo", count: 3, label: "bravo-label" },
  { id: "b", name: "alpha", count: null, label: undefined },
  { id: "c", name: "Charlie", count: 1, label: "charlie-label" },
  { id: "d", name: "delta", count: 2, label: "delta-label" },
];

const ACCESSORS: Record<"name" | "count" | "label", (row: Row) => string | number> = {
  name: (row) => row.name,
  count: (row) => row.count as number, // exercises the null-handling path directly
  label: (row) => row.label as string,
};

describe("sortRows", () => {
  it("sorts strings via localeCompare, ascending", () => {
    const sorted = sortRows(ROWS, ACCESSORS, "name", "asc");
    expect(sorted.map((r) => r.name)).toEqual(["alpha", "Bravo", "Charlie", "delta"]);
  });

  it("sorts strings via localeCompare, descending", () => {
    const sorted = sortRows(ROWS, ACCESSORS, "name", "desc");
    expect(sorted.map((r) => r.name)).toEqual(["delta", "Charlie", "Bravo", "alpha"]);
  });

  it("sorts numbers via subtraction, ascending, with nulls last", () => {
    const sorted = sortRows(ROWS, ACCESSORS, "count", "asc");
    expect(sorted.map((r) => r.id)).toEqual(["c", "d", "a", "b"]);
  });

  it("sorts numbers via subtraction, descending, with nulls STILL last (not resurfaced)", () => {
    const sorted = sortRows(ROWS, ACCESSORS, "count", "desc");
    expect(sorted.map((r) => r.id)).toEqual(["a", "d", "c", "b"]);
  });

  it("sorts undefined accessor results last too, in both directions", () => {
    const ascending = sortRows(ROWS, ACCESSORS, "label", "asc");
    expect(ascending.map((r) => r.id)).toEqual(["a", "c", "d", "b"]);

    const descending = sortRows(ROWS, ACCESSORS, "label", "desc");
    expect(descending.map((r) => r.id)).toEqual(["d", "c", "a", "b"]);
  });

  it("is stable: rows comparing equal keep their relative input order", () => {
    const tiedRows: Row[] = [
      { id: "x1", name: "same", count: 5, label: "l" },
      { id: "x2", name: "same", count: 5, label: "l" },
      { id: "x3", name: "same", count: 5, label: "l" },
    ];
    const sorted = sortRows(tiedRows, ACCESSORS, "name", "asc");
    expect(sorted.map((r) => r.id)).toEqual(["x1", "x2", "x3"]);
  });

  it("does not mutate the input array", () => {
    const copy = [...ROWS];
    sortRows(ROWS, ACCESSORS, "name", "asc");
    expect(ROWS).toEqual(copy);
  });
});

describe("useTableState", () => {
  it("applies defaultSort/defaultDir on initial render", () => {
    const { result } = renderHook(() =>
      useTableState({ rows: ROWS, sortAccessors: ACCESSORS, defaultSort: "name" }),
    );

    expect(result.current.sort).toBe("name");
    expect(result.current.dir).toBe("asc");
    expect(result.current.rows.map((r) => r.name)).toEqual(["alpha", "Bravo", "Charlie", "delta"]);
  });

  it("honors an explicit defaultDir", () => {
    const { result } = renderHook(() =>
      useTableState({
        rows: ROWS,
        sortAccessors: ACCESSORS,
        defaultSort: "name",
        defaultDir: "desc",
      }),
    );

    expect(result.current.dir).toBe("desc");
    expect(result.current.rows.map((r) => r.name)).toEqual(["delta", "Charlie", "Bravo", "alpha"]);
  });

  it("toggles direction when setSort is called on the already-active column", () => {
    const { result } = renderHook(() =>
      useTableState({ rows: ROWS, sortAccessors: ACCESSORS, defaultSort: "name" }),
    );

    expect(result.current.dir).toBe("asc");

    act(() => result.current.setSort("name"));
    expect(result.current.sort).toBe("name");
    expect(result.current.dir).toBe("desc");

    act(() => result.current.setSort("name"));
    expect(result.current.dir).toBe("asc");
  });

  it("switching to a NEW string column defaults to asc", () => {
    const { result } = renderHook(() =>
      useTableState<Row, "name" | "count" | "label">({
        rows: ROWS,
        sortAccessors: ACCESSORS,
        defaultSort: "count",
      }),
    );

    act(() => result.current.setSort("name"));
    expect(result.current.sort).toBe("name");
    expect(result.current.dir).toBe("asc");
  });

  it("switching to a NEW numeric column defaults to desc", () => {
    const { result } = renderHook(() =>
      useTableState<Row, "name" | "count" | "label">({
        rows: ROWS,
        sortAccessors: ACCESSORS,
        defaultSort: "name",
      }),
    );

    act(() => result.current.setSort("count"));
    expect(result.current.sort).toBe("count");
    expect(result.current.dir).toBe("desc");
  });

  it("search is a no-op when searchAccessor is absent", () => {
    const { result } = renderHook(() =>
      useTableState({ rows: ROWS, sortAccessors: ACCESSORS, defaultSort: "name" }),
    );

    act(() => result.current.setSearch("zzz-no-match"));
    expect(result.current.rows).toHaveLength(4);
  });

  it("search is a no-op when the query is empty", () => {
    const { result } = renderHook(() =>
      useTableState({
        rows: ROWS,
        sortAccessors: ACCESSORS,
        defaultSort: "name",
        searchAccessor: (row) => row.name,
      }),
    );

    act(() => result.current.setSearch(""));
    expect(result.current.rows).toHaveLength(4);
  });

  it("search filters case-insensitively via searchAccessor", () => {
    const { result } = renderHook(() =>
      useTableState({
        rows: ROWS,
        sortAccessors: ACCESSORS,
        defaultSort: "name",
        searchAccessor: (row) => row.name,
      }),
    );

    act(() => result.current.setSearch("CHAR"));
    expect(result.current.rows.map((r) => r.id)).toEqual(["c"]);
    expect(result.current.search).toBe("CHAR");
  });
});
