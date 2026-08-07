import { describe, expect, it } from "vitest";
import { parsePlanningScope } from "@/features/portfolio/portfolioView";

describe("parsePlanningScope", () => {
  it("canonicalizes a bounded explicit preview", () => {
    expect(
      parsePlanningScope("VALVE-2@YYZ\nPUMP-1@MIA"),
    ).toEqual({
      keys: [
        { pn: "PUMP-1", location: "MIA" },
        { pn: "VALVE-2", location: "YYZ" },
      ],
      error: null,
    });
  });

  it("rejects duplicates, malformed keys, and more than 200 entries", () => {
    expect(parsePlanningScope("PUMP-1@MIA,PUMP-1@MIA").error).toMatch(
      /unique/i,
    );
    expect(parsePlanningScope("PUMP-1").error).toMatch(/PN@LOCATION/i);
    expect(
      parsePlanningScope(
        Array.from({ length: 201 }, (_, index) => `PN-${index}@MIA`).join(
          "\n",
        ),
      ).error,
    ).toMatch(/at most 200/i);
  });
});
