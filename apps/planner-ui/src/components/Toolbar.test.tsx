import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { QueueFilter } from "../lib/queryView";
import { Toolbar } from "./Toolbar";

// Toolbar is controlled; a stateful harness lets the filter prop actually update
// across interactions (a bare vi.fn parent would freeze the controls).
function Harness({
  initial = {},
  onChange,
}: {
  initial?: QueueFilter;
  onChange: (f: QueueFilter) => void;
}) {
  const [filter, setFilter] = useState<QueueFilter>(initial);
  return (
    <Toolbar
      filter={filter}
      onFilterChange={(f) => {
        setFilter(f);
        onChange(f);
      }}
      onExport={vi.fn()}
    />
  );
}

describe("Toolbar", () => {
  it("emits a search filter as the planner types", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.type(screen.getByLabelText(/search part or location/i), "hyd");
    expect(onChange).toHaveBeenLastCalledWith({ search: "hyd" });
  });

  it("toggles a tier chip, preserving existing filters", async () => {
    const onChange = vi.fn();
    render(<Harness initial={{ search: "x" }} onChange={onChange} />);
    await userEvent.click(screen.getByLabelText("Tier A"));
    expect(onChange).toHaveBeenLastCalledWith({ search: "x", tiers: [1] });
  });

  it("selects a type and a minimum AOG level (accumulating)", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await userEvent.selectOptions(screen.getByLabelText(/^type/i), "transfer");
    expect(onChange).toHaveBeenLastCalledWith({ types: ["transfer"] });
    await userEvent.selectOptions(screen.getByLabelText(/aog risk/i), "3");
    expect(onChange).toHaveBeenLastCalledWith({ types: ["transfer"], aogMin: 3 });
  });

  it("clearing the type option removes it from the filter", async () => {
    const onChange = vi.fn();
    render(<Harness initial={{ types: ["transfer"] }} onChange={onChange} />);
    await userEvent.selectOptions(screen.getByLabelText(/^type/i), "");
    expect(onChange).toHaveBeenLastCalledWith({});
  });

  it("fires onExport", async () => {
    const onExport = vi.fn();
    render(<Toolbar filter={{}} onFilterChange={vi.fn()} onExport={onExport} />);
    await userEvent.click(screen.getByRole("button", { name: /export/i }));
    expect(onExport).toHaveBeenCalled();
  });

  it("shows Approve matching only when onBulkApprove is provided, and fires it", async () => {
    const onBulkApprove = vi.fn();
    const { rerender } = render(<Toolbar filter={{}} onFilterChange={vi.fn()} onExport={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /approve matching/i })).not.toBeInTheDocument();
    rerender(
      <Toolbar filter={{}} onFilterChange={vi.fn()} onExport={vi.fn()} onBulkApprove={onBulkApprove} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /approve matching/i }));
    expect(onBulkApprove).toHaveBeenCalled();
  });
});
