import { useRef, useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { useFocusTrap } from "@/lib/useFocusTrap";

/** Minimal harness — a trigger button that opens a 3-control dialog trapped by the hook. */
function Harness({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(containerRef, () => {
    onClose();
    setOpen(false);
  });

  if (!open) return <button>Reopen</button>;

  return (
    <div ref={containerRef} role="dialog" aria-label="Test dialog" tabIndex={-1}>
      <button>First</button>
      <input aria-label="Middle input" />
      <button>Last</button>
    </div>
  );
}

/**
 * A `Dialog` mounted fresh on open — matching the real contract (see
 * useFocusTrap.ts's docstring): each open is a distinct component instance,
 * not a ref toggled inside one always-mounted parent.
 */
function Dialog({ onClose }: { onClose: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(containerRef, onClose);

  return (
    <div ref={containerRef} role="dialog" aria-label="Test dialog" tabIndex={-1}>
      <button>Only control</button>
    </div>
  );
}

function TriggerHarness({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(false);

  return (
    <div>
      <button onClick={() => setOpen(true)}>Open dialog</button>
      {open && (
        <Dialog
          onClose={() => {
            onClose();
            setOpen(false);
          }}
        />
      )}
    </div>
  );
}

describe("useFocusTrap", () => {
  it("moves focus to the first focusable element on mount", async () => {
    render(<Harness onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "First" })).toHaveFocus());
  });

  it("calls onClose when Escape is pressed", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<Harness onClose={onClose} />);

    await waitFor(() => expect(screen.getByRole("button", { name: "First" })).toHaveFocus());
    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("wraps Tab from the last focusable element back to the first", async () => {
    const user = userEvent.setup();
    render(<Harness onClose={vi.fn()} />);

    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Last" });
    await waitFor(() => expect(first).toHaveFocus());

    last.focus();
    await user.tab();

    expect(first).toHaveFocus();
  });

  it("wraps Shift+Tab from the first focusable element to the last", async () => {
    const user = userEvent.setup();
    render(<Harness onClose={vi.fn()} />);

    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Last" });
    await waitFor(() => expect(first).toHaveFocus());

    await user.tab({ shift: true });

    expect(last).toHaveFocus();
  });

  it("restores focus to the previously-focused element on close", async () => {
    const user = userEvent.setup();
    render(<TriggerHarness onClose={vi.fn()} />);

    const trigger = screen.getByRole("button", { name: "Open dialog" });
    trigger.focus();
    await user.click(trigger);

    await waitFor(() => expect(screen.getByRole("button", { name: "Only control" })).toHaveFocus());

    await user.keyboard("{Escape}");

    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
