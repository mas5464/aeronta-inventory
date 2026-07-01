import { describe, expect, it } from "vitest";
import { demand } from "./format";

describe("demand", () => {
  it("shows sub-unit projected demand with real precision instead of rounding to 0", () => {
    expect(demand(0)).toBe("0");
    expect(demand(0.42)).toBe("0.42");
    expect(demand(2.73)).toBe("2.73");
    expect(demand(0.318)).toBe("0.32");
    expect(demand(9.5)).toBe("9.5");
    expect(demand(15)).toBe("15");
  });
});
