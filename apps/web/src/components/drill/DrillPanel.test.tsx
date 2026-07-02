import { useRef, useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DrillPanel } from "@/components/drill/DrillPanel";

describe("DrillPanel", () => {
  it("renders a role=region landmark labeled with the title and matching id", () => {
    render(
      <DrillPanel id="panel-parts" title="Parts breakdown" onClose={vi.fn()}>
        <p>content</p>
      </DrillPanel>,
    );

    const region = screen.getByRole("region", { name: "Parts breakdown" });
    expect(region).toHaveAttribute("id", "panel-parts");
  });

  it("focuses its heading (tabIndex=-1) on mount", () => {
    render(
      <DrillPanel id="panel-parts" title="Parts breakdown" onClose={vi.fn()}>
        <p>content</p>
      </DrillPanel>,
    );

    const heading = screen.getByRole("heading", { name: "Parts breakdown" });
    expect(heading).toHaveAttribute("tabindex", "-1");
    expect(heading).toHaveFocus();
  });

  it("calls onClose when Escape is pressed inside the region", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <DrillPanel id="panel-parts" title="Parts breakdown" onClose={onClose}>
        <p>content</p>
      </DrillPanel>,
    );

    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when the ghost close button is clicked", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <DrillPanel id="panel-parts" title="Parts breakdown" onClose={onClose}>
        <p>content</p>
      </DrillPanel>,
    );

    await user.click(screen.getByRole("button", { name: "Close Parts breakdown" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("is NOT a focus trap — Tab can move focus out of the region to content after it", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <DrillPanel id="panel-parts" title="Parts breakdown" onClose={vi.fn()}>
          <button type="button">inside</button>
        </DrillPanel>
        <button type="button">after panel</button>
      </div>,
    );

    // Heading is focused first (mount effect). Tabbing forward walks the
    // region's own focusable children (close button, then "inside") and
    // then is free to leave the region entirely and land on "after panel" —
    // a real trap would wrap back to the heading/close button instead.
    await user.tab();
    expect(screen.getByRole("button", { name: "Close Parts breakdown" })).toHaveFocus();

    await user.tab();
    expect(screen.getByRole("button", { name: "inside" })).toHaveFocus();

    await user.tab();
    expect(screen.getByRole("button", { name: "after panel" })).toHaveFocus();
  });

  function RestoreFocusHarness() {
    const [open, setOpen] = useState(true);
    const triggerRef = useRef<HTMLButtonElement>(null);
    return (
      <div>
        <button type="button" ref={triggerRef}>
          trigger
        </button>
        {open && (
          <DrillPanel
            id="panel-parts"
            title="Parts breakdown"
            onClose={() => setOpen(false)}
            restoreFocusTo={triggerRef}
          >
            <p>content</p>
          </DrillPanel>
        )}
      </div>
    );
  }

  it("restores focus to restoreFocusTo's element when closed (unmounted)", async () => {
    const user = userEvent.setup();
    render(<RestoreFocusHarness />);

    expect(screen.getByRole("heading", { name: "Parts breakdown" })).toHaveFocus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("region")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "trigger" })).toHaveFocus();
  });
});
