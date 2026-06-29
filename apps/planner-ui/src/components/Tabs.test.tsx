import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
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
});
