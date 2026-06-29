import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { KillSwitchHeader } from "./KillSwitchHeader";

describe("KillSwitchHeader", () => {
  it("shows active state and engages on click", async () => {
    const onToggle = vi.fn();
    render(
      <KillSwitchHeader tenant="acme" count={4} state={{ engaged: false }} onToggle={onToggle} />,
    );
    expect(screen.getByText("Agent active")).toBeInTheDocument();
    expect(screen.getByText("acme · 4 pending")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button"));
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it("shows paused state and resumes on click", async () => {
    const onToggle = vi.fn();
    render(
      <KillSwitchHeader tenant="acme" count={0} state={{ engaged: true }} onToggle={onToggle} />,
    );
    const btn = screen.getByRole("button");
    expect(btn).toHaveTextContent("Agent paused");
    expect(btn).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(btn);
    expect(onToggle).toHaveBeenCalledWith(false);
  });
});
