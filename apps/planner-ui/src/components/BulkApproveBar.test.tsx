import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { BulkApproveFilter } from "../api/types";
import { BulkApproveBar } from "./BulkApproveBar";

function setup(disabled = false) {
  const onBulkApprove = vi.fn<(f: BulkApproveFilter) => void>();
  render(<BulkApproveBar onBulkApprove={onBulkApprove} disabled={disabled} />);
  return { onBulkApprove };
}

describe("BulkApproveBar", () => {
  it("approves with an empty filter when nothing is set", async () => {
    const { onBulkApprove } = setup();
    await userEvent.click(screen.getByRole("button", { name: /approve matching/i }));
    expect(onBulkApprove).toHaveBeenCalledWith({});
  });

  it("includes only the tiers that are checked", async () => {
    const { onBulkApprove } = setup();
    await userEvent.click(screen.getByLabelText("Tier B"));
    await userEvent.click(screen.getByLabelText("Tier C"));
    await userEvent.click(screen.getByRole("button", { name: /approve matching/i }));
    expect(onBulkApprove).toHaveBeenCalledWith({ tiers: [2, 3] });
  });

  it("includes max change % and min criticality when entered", async () => {
    const { onBulkApprove } = setup();
    await userEvent.type(screen.getByLabelText(/max change/i), "50");
    await userEvent.type(screen.getByLabelText(/min criticality/i), "3");
    await userEvent.click(screen.getByRole("button", { name: /approve matching/i }));
    expect(onBulkApprove).toHaveBeenCalledWith({ max_delta_pct: 50, criticality_min: 3 });
  });

  it("includes the selected recommendation types", async () => {
    const { onBulkApprove } = setup();
    await userEvent.click(screen.getByLabelText("Transfer"));
    await userEvent.click(screen.getByLabelText("Reduce stock"));
    await userEvent.click(screen.getByRole("button", { name: /approve matching/i }));
    expect(onBulkApprove).toHaveBeenCalledWith({ types: ["transfer", "reduce_stock"] });
  });

  it("disables the action when disabled", () => {
    setup(true);
    expect(screen.getByRole("button", { name: /approve matching/i })).toBeDisabled();
  });
});
