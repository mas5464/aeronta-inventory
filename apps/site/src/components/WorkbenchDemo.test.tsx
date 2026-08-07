// apps/site/src/components/WorkbenchDemo.test.tsx
//
// jsdom has no matchMedia, so the component's reduced-motion fallback kicks
// in and the savings counter lands immediately — no rAF faking needed.
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WorkbenchDemo } from "./WorkbenchDemo";

describe("WorkbenchDemo", () => {
  it("starts with a pending recommendation and the synthetic-demo disclosure", () => {
    render(<WorkbenchDemo />);
    expect(screen.getByText("TRAX eMRO · synthetic demo")).toBeInTheDocument();
    expect(screen.getByText("Pending approval")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(screen.queryByTestId("ledger-entry")).not.toBeInTheDocument();
    expect(screen.getByTestId("savings-counter")).toHaveTextContent("$0");
  });

  it("approve writes the change: status flips, ledger entry appears, counter lands", () => {
    render(<WorkbenchDemo />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(screen.getByText("Written to eMRO")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    const entry = screen.getByTestId("ledger-entry");
    expect(entry).toHaveTextContent("3290-45-11");
    expect(entry).toHaveTextContent("MIA");
    expect(entry).toHaveTextContent("ROP 6→3");
    expect(entry).toHaveTextContent("EOQ 12→5");
    expect(entry).toHaveTextContent("SS 4→2");
    expect(entry).toHaveTextContent("Max 18→8");
    expect(entry).toHaveTextContent("planner");
    // matchMedia unavailable → reduced-motion path → counter jumps to final.
    expect(screen.getByTestId("savings-counter")).toHaveTextContent("$9,120");
  });

  it("reset restores the pending state", () => {
    render(<WorkbenchDemo />);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset demo" }));
    expect(screen.getByText("Pending approval")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(screen.queryByTestId("ledger-entry")).not.toBeInTheDocument();
    expect(screen.getByTestId("savings-counter")).toHaveTextContent("$0");
  });
});
