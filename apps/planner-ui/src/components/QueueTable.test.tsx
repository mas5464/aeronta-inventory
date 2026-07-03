import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SAMPLE_SEED } from "../api/sample";
import { QueueTable } from "./QueueTable";

const ROWS = SAMPLE_SEED.map((e) => e.row);

describe("QueueTable", () => {
  it("renders one row per recommendation with its tier badge", () => {
    render(
      <QueueTable rows={ROWS} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />,
    );
    const bodyRows = screen.getAllByRole("row").slice(1); // drop the header row
    expect(bodyRows).toHaveLength(4);
    expect(within(bodyRows[0]).getByText("HYD-PUMP-001")).toBeInTheDocument();
    expect(within(bodyRows[0]).getByText("A")).toBeInTheDocument();
    expect(within(bodyRows[2]).getByText("B")).toBeInTheDocument();
    expect(within(bodyRows[3]).getByText("C")).toBeInTheDocument();
  });

  it("selecting a row fires onSelect; approve fires onApprove without selecting", async () => {
    const onSelect = vi.fn();
    const onApprove = vi.fn();
    render(
      <QueueTable rows={ROWS} selectedId={null} onSelect={onSelect} onApprove={onApprove} />,
    );
    const firstRow = screen.getAllByRole("row")[1];
    await userEvent.click(within(firstRow).getByText("HYD-PUMP-001"));
    expect(onSelect).toHaveBeenCalledWith("rec-hyd-yyz");

    onSelect.mockClear();
    await userEvent.click(within(firstRow).getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledWith("rec-hyd-yyz");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("disables approve buttons when disabled", () => {
    render(
      <QueueTable rows={ROWS} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} disabled />,
    );
    for (const btn of screen.getAllByRole("button", { name: "Approve" })) {
      expect(btn).toBeDisabled();
    }
  });

  it("disables approve for non-approvable (advisory) rows", () => {
    render(<QueueTable rows={ROWS} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    const rows = screen.getAllByRole("row");
    // row[1] = HYD-PUMP-001 (approvable), row[3] = FILTER-EXP-042 (advisory)
    expect(within(rows[1]).getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(within(rows[3]).getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("the row selector is a keyboard-operable button exposing criticality as text", () => {
    render(<QueueTable rows={ROWS} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    // Two rows share the PN (YYZ/YOW), so scope to the first body row.
    const firstRow = screen.getAllByRole("row")[1];
    const selector = within(firstRow).getByRole("button", { name: /HYD-PUMP-001/ });
    expect(selector).toHaveAccessibleName(/criticality 1/i);
  });

  it("shows Part, Location and Description as separate columns", () => {
    render(<QueueTable rows={ROWS} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    expect(screen.getByRole("columnheader", { name: /^part$/i })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /location/i })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /description/i })).toBeInTheDocument();
    // Part cell holds the PN; Location and Description are their own cells.
    const firstRow = screen.getAllByRole("row")[1];
    expect(within(firstRow).getByText("YYZ")).toBeInTheDocument();
    expect(within(firstRow).getByText("Hydraulic pump")).toBeInTheDocument();
  });

  it("renders the AOG risk badge and confidence as a percentage", () => {
    render(<QueueTable rows={ROWS} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    expect(screen.getByText("High")).toBeInTheDocument(); // HYD-PUMP-001·YYZ has aog 3
    expect(screen.getByText("Medium")).toBeInTheDocument(); // ·YOW has aog 2
    expect(screen.getByText("78%")).toBeInTheDocument(); // confidence, was 0.78
    expect(screen.queryByText("0.78")).not.toBeInTheDocument();
  });

  it("colors the confidence badge by tier", () => {
    const rows = [
      { ...ROWS[0], recommendation_id: "r-high", confidence_score: 0.95 },
      { ...ROWS[0], recommendation_id: "r-medium", confidence_score: 0.65 },
      { ...ROWS[0], recommendation_id: "r-low", confidence_score: 0.2 },
    ];
    render(<QueueTable rows={rows} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    expect(screen.getByText("95%").className).toContain("confHigh");
    expect(screen.getByText("65%").className).toContain("confMedium");
    expect(screen.getByText("20%").className).toContain("confLow");
  });

  it("fires onSort with the column key when a sortable header is clicked", async () => {
    const onSort = vi.fn();
    render(
      <QueueTable
        rows={ROWS}
        selectedId={null}
        onSelect={vi.fn()}
        onApprove={vi.fn()}
        sort={{ key: "priority_score", dir: "desc" }}
        onSort={onSort}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /priority/i }));
    expect(onSort).toHaveBeenCalledWith("priority_score");
    // the active column advertises its sort direction to assistive tech
    expect(screen.getByRole("columnheader", { name: /priority/i })).toHaveAttribute(
      "aria-sort",
      "descending",
    );
  });

  it("shows an empty state when there are no rows", () => {
    render(<QueueTable rows={[]} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    expect(screen.getByText(/no pending recommendations/i)).toBeInTheDocument();
  });

  it("decided mode shows a status badge instead of an approve button", () => {
    const decidedRows = [{ ...ROWS[0], status: "approved" as const }];
    render(
      <QueueTable
        rows={decidedRows}
        selectedId={null}
        onSelect={vi.fn()}
        onApprove={vi.fn()}
        decided
      />,
    );
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByText("approved")).toBeInTheDocument();
  });

  it("decided mode has its own empty state", () => {
    render(
      <QueueTable rows={[]} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} decided />,
    );
    expect(screen.getByText(/no decided recommendations/i)).toBeInTheDocument();
  });

  it("shows on-hand and need columns", () => {
    render(<QueueTable rows={ROWS} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    expect(screen.getByRole("columnheader", { name: /on hand/i })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /need/i })).toBeInTheDocument();
  });

  it("renders each row's current stock and rounded shortage quantity", () => {
    render(<QueueTable rows={ROWS} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    const bodyRows = screen.getAllByRole("row").slice(1);
    // HYD-PUMP-001 @ YYZ: current_stock 4, shortage_quantity 3
    expect(within(bodyRows[0]).getByText("4")).toBeInTheDocument();
    expect(within(bodyRows[0]).getByText("3")).toBeInTheDocument();
  });
});
