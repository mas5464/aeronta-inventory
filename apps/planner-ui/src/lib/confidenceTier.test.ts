import { describe, expect, it } from "vitest";
import { confidenceTier } from "./confidenceTier";

describe("confidenceTier", () => {
  it("classifies 0.8 and above as high", () => {
    expect(confidenceTier(0.8)).toBe("high");
    expect(confidenceTier(0.81)).toBe("high");
    expect(confidenceTier(1)).toBe("high");
  });

  it("classifies 0.5 up to (but not including) 0.8 as medium", () => {
    expect(confidenceTier(0.5)).toBe("medium");
    expect(confidenceTier(0.65)).toBe("medium");
    expect(confidenceTier(0.79)).toBe("medium");
  });

  it("classifies below 0.5 as low", () => {
    expect(confidenceTier(0.49)).toBe("low");
    expect(confidenceTier(0.1)).toBe("low");
    expect(confidenceTier(0)).toBe("low");
  });
});
