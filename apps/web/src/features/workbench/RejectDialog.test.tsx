import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RejectDialog } from "@/features/workbench/RejectDialog";

describe("RejectDialog", () => {
  it("is a modal dialog and focuses its first control on open", async () => {
    render(
      <RejectDialog recommendationId="rec-1" onCancel={vi.fn()} onConfirm={vi.fn()} />,
    );

    const dialog = screen.getByRole("dialog", { name: "Dismiss recommendation rec-1" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    await waitFor(() => expect(screen.getByLabelText("Reason")).toHaveFocus());
  });

  it("closes (calls onCancel) when Escape is pressed", async () => {
    const onCancel = vi.fn();
    const user = userEvent.setup();
    render(
      <RejectDialog recommendationId="rec-1" onCancel={onCancel} onConfirm={vi.fn()} />,
    );

    await waitFor(() => expect(screen.getByLabelText("Reason")).toHaveFocus());
    await user.keyboard("{Escape}");

    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("traps Tab within the dialog's controls (wraps from Confirm dismiss back to Reason)", async () => {
    const user = userEvent.setup();
    render(
      <RejectDialog recommendationId="rec-1" onCancel={vi.fn()} onConfirm={vi.fn()} />,
    );

    const reasonSelect = screen.getByLabelText("Reason");
    await waitFor(() => expect(reasonSelect).toHaveFocus());

    const confirmButton = screen.getByRole("button", { name: "Confirm dismiss" });
    confirmButton.focus();
    await user.tab();

    expect(reasonSelect).toHaveFocus();
  });
});
