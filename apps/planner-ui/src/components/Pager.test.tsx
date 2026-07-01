import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Pager } from "./Pager";

describe("Pager", () => {
  it("renders nothing when total is 0", () => {
    const { container } = render(
      <Pager page={0} limit={50} total={0} onPrev={vi.fn()} onNext={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the range and disables Prev on the first page", () => {
    render(<Pager page={0} limit={2} total={5} onPrev={vi.fn()} onNext={vi.fn()} />);
    expect(screen.getByText("Showing 1–2 of 5")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prev" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled();
  });

  it("disables Next on the last page and calls onNext when clicked mid-range", async () => {
    const onNext = vi.fn();
    render(<Pager page={1} limit={2} total={5} onPrev={vi.fn()} onNext={onNext} />);
    expect(screen.getByText("Showing 3–4 of 5")).toBeInTheDocument();
    const next = screen.getByRole("button", { name: "Next" });
    expect(next).toBeEnabled();
    await userEvent.click(next);
    expect(onNext).toHaveBeenCalledTimes(1);
  });

  it("disables Next once the last page is reached", () => {
    render(<Pager page={2} limit={2} total={5} onPrev={vi.fn()} onNext={vi.fn()} />);
    expect(screen.getByText("Showing 5–5 of 5")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });
});
