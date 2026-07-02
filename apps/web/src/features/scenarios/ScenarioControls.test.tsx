import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ScenarioControls } from "@/features/scenarios/ScenarioControls";
import type { ScenarioParams } from "@/lib/api/types";

const DEFAULT: ScenarioParams = {
  service_level_target: 0.95,
  lead_time_delta_pct: 0,
  budget_cap: null,
  scope: "all",
  scope_value: null,
};

describe("ScenarioControls", () => {
  it("fires onChange with an updated service_level_target when the SL slider moves", () => {
    const onChange = vi.fn();
    render(<ScenarioControls params={DEFAULT} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("Target service level"), {
      target: { value: "0.99" },
    });

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ service_level_target: 0.99 }),
    );
  });

  it("fires onChange with an updated lead_time_delta_pct when the TAT slider moves", () => {
    const onChange = vi.fn();
    render(<ScenarioControls params={DEFAULT} onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("Repair-TAT / lead-time delta"), {
      target: { value: "0.3" },
    });

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ lead_time_delta_pct: 0.3 }));
  });

  it("fires onChange with a numeric budget_cap when typed, and null when cleared", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<ScenarioControls params={DEFAULT} onChange={onChange} />);

    await userEvent.type(screen.getByLabelText("Inventory budget cap"), "5");
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ budget_cap: 5 }));

    rerender(<ScenarioControls params={{ ...DEFAULT, budget_cap: 5 }} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText("Inventory budget cap"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ budget_cap: null }));
  });

  it("shows a criticality-tier select when scope is criticality_tier", async () => {
    const onChange = vi.fn();
    render(
      <ScenarioControls
        params={{ ...DEFAULT, scope: "criticality_tier", scope_value: "1" }}
        onChange={onChange}
      />,
    );
    expect(screen.getByLabelText("Criticality tier")).toBeInTheDocument();
    expect(screen.queryByLabelText("ATA chapter")).not.toBeInTheDocument();
  });

  it("shows an ATA-chapter text input when scope is ata_chapter", () => {
    const onChange = vi.fn();
    render(
      <ScenarioControls
        params={{ ...DEFAULT, scope: "ata_chapter", scope_value: "32" }}
        onChange={onChange}
      />,
    );
    expect(screen.getByLabelText("ATA chapter")).toBeInTheDocument();
    expect(screen.queryByLabelText("Criticality tier")).not.toBeInTheDocument();
  });

  it("resets scope_value to null when the scope select changes", () => {
    const onChange = vi.fn();
    render(
      <ScenarioControls
        params={{ ...DEFAULT, scope: "criticality_tier", scope_value: "1" }}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByLabelText("Scenario scope"), {
      target: { value: "ata_chapter" },
    });

    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "ata_chapter", scope_value: null }),
    );
  });
});
