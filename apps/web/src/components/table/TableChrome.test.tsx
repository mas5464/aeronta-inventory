import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyRow, TableCaption } from "@/components/table/TableChrome";

describe("TableCaption", () => {
  it("renders a visually-hidden <caption> with the given text", () => {
    render(
      <table>
        <TableCaption>Widget inventory table</TableCaption>
        <tbody>
          <tr>
            <td>x</td>
          </tr>
        </tbody>
      </table>,
    );

    const caption = screen.getByText("Widget inventory table");
    expect(caption.tagName).toBe("CAPTION");
    expect(caption.className).toMatch(/sr-only/);
  });
});

describe("EmptyRow", () => {
  it("renders one <tr><td colSpan> with muted styling and the given content", () => {
    render(
      <table>
        <tbody>
          <EmptyRow colSpan={4}>No rows match the current filter.</EmptyRow>
        </tbody>
      </table>,
    );

    const cell = screen.getByText("No rows match the current filter.");
    expect(cell.tagName).toBe("TD");
    expect(cell).toHaveAttribute("colspan", "4");
    expect(cell.className).toMatch(/text-ink-2/);

    const row = cell.closest("tr");
    expect(row).not.toBeNull();
    expect(row?.children).toHaveLength(1);
  });
});
