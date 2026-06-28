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

  it("shows an empty state when there are no rows", () => {
    render(<QueueTable rows={[]} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
    expect(screen.getByText(/no pending recommendations/i)).toBeInTheDocument();
  });
});
