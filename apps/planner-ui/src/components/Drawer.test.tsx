import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Drawer } from "./Drawer";

describe("Drawer", () => {
  it("renders nothing when closed", () => {
    render(
      <Drawer open={false} onClose={vi.fn()}>
        <p>content</p>
      </Drawer>,
    );
    expect(screen.queryByText("content")).not.toBeInTheDocument();
  });

  it("renders children in a dialog when open", () => {
    render(
      <Drawer open onClose={vi.fn()}>
        <p>content</p>
      </Drawer>,
    );
    expect(screen.getByText("content")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose}>
        <p>content</p>
      </Drawer>,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on backdrop click but not when clicking inside the panel", async () => {
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose}>
        <p>content</p>
      </Drawer>,
    );
    await userEvent.click(screen.getByText("content"));
    expect(onClose).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("dialog").parentElement!); // the backdrop
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes via the explicit close button", async () => {
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose}>
        <p>content</p>
      </Drawer>,
    );
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("traps Tab focus within the panel", async () => {
    render(
      <Drawer open onClose={vi.fn()}>
        <button>First</button>
        <button>Last</button>
      </Drawer>,
    );
    const closeBtn = screen.getByRole("button", { name: /close/i }); // first focusable (renders before children)
    screen.getByRole("button", { name: "Last" }).focus();
    await userEvent.tab();
    expect(closeBtn).toHaveFocus(); // wraps forward past Last back to Close
  });

  it("restores focus to the previously-focused element on close", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button onClick={() => setOpen(true)}>Open</button>
          <Drawer open={open} onClose={() => setOpen(false)}>
            <p>content</p>
          </Drawer>
        </div>
      );
    }
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    opener.focus();
    await userEvent.click(opener);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(opener).toHaveFocus();
  });
});
