import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AtaRiskList } from "@/components/AtaRiskList";

describe("AtaRiskList", () => {
  it("ranks ATA chapters by shortage descending and shows the numeric value as text", () => {
    render(
      <AtaRiskList
        chapters={[
          { key: "21", count: 100, on_hand: 500, shortage: 10 },
          { key: "32", count: 200, on_hand: 300, shortage: 50 },
        ]}
      />,
    );

    const items = screen.getAllByRole("listitem");
    // ATA 32 (shortage 50) should rank above ATA 21 (shortage 10).
    expect(items[0]).toHaveTextContent("ATA 32");
    expect(items[1]).toHaveTextContent("ATA 21");
    expect(screen.getByText("50 short")).toBeInTheDocument();
  });

  it("renders an empty state when there is no data", () => {
    render(<AtaRiskList chapters={[]} />);
    expect(screen.getByText("No ATA chapter data available.")).toBeInTheDocument();
  });
});
