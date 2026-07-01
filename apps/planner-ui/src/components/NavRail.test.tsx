import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { NavRail } from "./NavRail";

function renderNav(active?: "review" | "dashboard") {
  return render(
    <MemoryRouter>
      <NavRail active={active} />
    </MemoryRouter>,
  );
}

describe("NavRail", () => {
  it("marks Review as the current section by default and disables the placeholders", () => {
    renderNav();
    const review = screen.getByRole("button", { name: /review/i });
    expect(review).toHaveAttribute("aria-current", "page");
    expect(review).toBeEnabled();
    expect(screen.getByRole("button", { name: /dashboard/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /settings/i })).toBeDisabled();
  });

  it("marks Dashboard as the current section when active", () => {
    renderNav("dashboard");
    expect(screen.getByRole("button", { name: /dashboard/i })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: /review/i })).not.toHaveAttribute("aria-current");
  });
});
