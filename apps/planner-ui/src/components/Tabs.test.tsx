import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { PlannerTab } from "../hooks/usePlanner";
import { Tabs } from "./Tabs";

describe("Tabs", () => {
  it("marks the active tab selected and fires onChange for the other", async () => {
    const onChange = vi.fn();
    render(<Tabs tab="pending" onChange={onChange} />);
    expect(screen.getByRole("tab", { name: /pending/i })).toHaveAttribute("aria-selected", "true");
    const decided = screen.getByRole("tab", { name: /decided/i });
    expect(decided).toHaveAttribute("aria-selected", "false");
    await userEvent.click(decided);
    expect(onChange).toHaveBeenCalledWith("decided");
  });

  it("exposes a tablist", () => {
    render(<Tabs tab="decided" onChange={vi.fn()} />);
    expect(screen.getByRole("tablist")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /decided/i })).toHaveAttribute("aria-selected", "true");
  });

  it("shows the active tab's count as a badge", () => {
    render(<Tabs tab="pending" onChange={vi.fn()} activeCount={4} />);
    expect(screen.getByRole("tab", { name: /pending/i })).toHaveTextContent("4");
  });

  it("does not show a count on the inactive tab", () => {
    render(<Tabs tab="pending" onChange={vi.fn()} activeCount={4} />);
    expect(screen.getByRole("tab", { name: /decided/i })).not.toHaveTextContent("4");
  });

  it("uses a roving tabindex (only the active tab is in the tab order)", () => {
    render(<Tabs tab="pending" onChange={vi.fn()} />);
    expect(screen.getByRole("tab", { name: /pending/i })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: /decided/i })).toHaveAttribute("tabindex", "-1");
  });

  it("links each tab to the queue tabpanel via aria-controls", () => {
    render(<Tabs tab="pending" onChange={vi.fn()} />);
    expect(screen.getByRole("tab", { name: /pending/i })).toHaveAttribute(
      "aria-controls",
      "queue-tabpanel",
    );
  });

  it("moves to the next/previous tab on arrow keys", async () => {
    function Harness() {
      const [tab, setTab] = useState<PlannerTab>("pending");
      return <Tabs tab={tab} onChange={setTab} />;
    }
    render(<Harness />);
    screen.getByRole("tab", { name: /pending/i }).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /decided/i })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: /pending/i })).toHaveAttribute("aria-selected", "true");
  });
});
