import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { QueryEmpty, QueryError, QueryLoading } from "@/components/QueryState";

describe("QueryLoading", () => {
  it("renders a role=status live region with the given label", () => {
    render(<QueryLoading label="Loading widgets…" />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Loading widgets…");
    expect(status).toHaveAttribute("aria-live", "polite");
  });
});

describe("QueryError", () => {
  it("renders a role=alert with the label + error message and a Retry button wired to onRetry", async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <QueryError label="Failed to load widgets" error={new Error("network down")} onRetry={onRetry} />,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Failed to load widgets: network down");

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("falls back to 'unknown error' for a non-Error thrown value", () => {
    render(<QueryError label="Failed to load widgets" error={"just a string"} onRetry={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Failed to load widgets: unknown error");
  });

  it("the Retry button has a visible focus-visible ring", () => {
    render(<QueryError label="Failed" error={new Error("x")} onRetry={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Retry" }).className).toMatch(/focus-visible:ring-2/);
  });
});

describe("QueryEmpty", () => {
  it("renders its children as a distinguishable (non-status, non-alert) empty-state message", () => {
    render(<QueryEmpty>Nothing here yet.</QueryEmpty>);
    const message = screen.getByText("Nothing here yet.");
    expect(message).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
