import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DrillableCard } from "@/components/drill/DrillableCard";

describe("DrillableCard", () => {
  it("renders the title inside a trigger button with aria-expanded/aria-controls", () => {
    render(
      <DrillableCard title="Parts" open={false} onToggle={vi.fn()} panelId="panel-parts">
        <span>42</span>
      </DrillableCard>,
    );

    const trigger = screen.getByRole("button", { name: "Parts" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveAttribute("aria-controls", "panel-parts");
  });

  it("reflects open=true via aria-expanded and rotates the chevron", () => {
    render(
      <DrillableCard title="Parts" open onToggle={vi.fn()} panelId="panel-parts">
        <span>42</span>
      </DrillableCard>,
    );

    const trigger = screen.getByRole("button", { name: "Parts" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const chevron = trigger.querySelector("svg");
    expect(chevron).toHaveAttribute("aria-hidden", "true");
    expect(chevron?.getAttribute("class")).toMatch(/rotate-180/);
  });

  it("calls onToggle when the trigger is clicked", async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    render(
      <DrillableCard title="Parts" open={false} onToggle={onToggle} panelId="panel-parts">
        <span>42</span>
      </DrillableCard>,
    );

    await user.click(screen.getByRole("button", { name: "Parts" }));
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("always renders its content children regardless of open state", () => {
    const { rerender } = render(
      <DrillableCard title="Parts" open={false} onToggle={vi.fn()} panelId="panel-parts">
        <span>42</span>
      </DrillableCard>,
    );
    expect(screen.getByText("42")).toBeInTheDocument();

    rerender(
      <DrillableCard title="Parts" open onToggle={vi.fn()} panelId="panel-parts">
        <span>42</span>
      </DrillableCard>,
    );
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("applies a focus-visible ring class to the trigger", () => {
    render(
      <DrillableCard title="Parts" open={false} onToggle={vi.fn()} panelId="panel-parts">
        <span>42</span>
      </DrillableCard>,
    );
    expect(screen.getByRole("button", { name: "Parts" }).className).toMatch(
      /focus-visible:ring-2/,
    );
  });
});
