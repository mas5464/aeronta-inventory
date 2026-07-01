import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NavRail } from "./NavRail";

describe("NavRail", () => {
  it("marks Review as the current section and disables the placeholders", () => {
    render(<NavRail />);
    const review = screen.getByRole("button", { name: /review/i });
    expect(review).toHaveAttribute("aria-current", "page");
    expect(review).toBeEnabled();
    expect(screen.getByRole("button", { name: /dashboard/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /settings/i })).toBeDisabled();
  });
});
