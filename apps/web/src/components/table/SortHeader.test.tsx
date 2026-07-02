import type { ComponentProps } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SortHeader } from "@/components/table/SortHeader";

type Col = "name" | "count";

function renderHeader(overrides: Partial<ComponentProps<typeof SortHeader<Col>>> = {}) {
  const onSort = vi.fn();
  render(
    <table>
      <thead>
        <tr>
          <SortHeader<Col>
            column="name"
            label="Name"
            activeSort="name"
            dir="asc"
            onSort={onSort}
            {...overrides}
          />
        </tr>
      </thead>
    </table>,
  );
  return { onSort };
}

describe("SortHeader", () => {
  it("renders a <th scope='col'> with the label as a button", () => {
    renderHeader();
    const th = screen.getByRole("columnheader");
    expect(th).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Name/ })).toBeInTheDocument();
  });

  it("sets aria-sort='ascending' when active and dir='asc'", () => {
    renderHeader({ activeSort: "name", dir: "asc" });
    expect(screen.getByRole("columnheader")).toHaveAttribute("aria-sort", "ascending");
  });

  it("sets aria-sort='descending' when active and dir='desc'", () => {
    renderHeader({ activeSort: "name", dir: "desc" });
    expect(screen.getByRole("columnheader")).toHaveAttribute("aria-sort", "descending");
  });

  it("sets aria-sort='none' when this column is not the active sort", () => {
    renderHeader({ activeSort: "count", dir: "asc" });
    expect(screen.getByRole("columnheader")).toHaveAttribute("aria-sort", "none");
  });

  it("calls onSort with the column when clicked", async () => {
    const user = userEvent.setup();
    const { onSort } = renderHeader({ column: "name" });

    await user.click(screen.getByRole("button", { name: /Name/ }));

    expect(onSort).toHaveBeenCalledWith("name");
    expect(onSort).toHaveBeenCalledTimes(1);
  });

  it("renders icons as aria-hidden (label text alone conveys meaning)", () => {
    renderHeader({ activeSort: "name", dir: "asc" });
    const svg = screen.getByRole("button", { name: /Name/ }).querySelector("svg");
    expect(svg).toHaveAttribute("aria-hidden", "true");
  });

  it("applies a focus-visible ring class matching the existing Button treatment", () => {
    renderHeader();
    expect(screen.getByRole("button", { name: /Name/ }).className).toMatch(
      /focus-visible:ring-2/,
    );
  });

  it("right-aligns the header cell and button content when align='right'", () => {
    renderHeader({ align: "right" });
    expect(screen.getByRole("columnheader").className).toMatch(/text-right/);
  });

  it("merges an optional className onto the header cell", () => {
    renderHeader({ className: "w-24" });
    expect(screen.getByRole("columnheader").className).toMatch(/w-24/);
  });
});
