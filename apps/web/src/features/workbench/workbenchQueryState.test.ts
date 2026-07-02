import { describe, expect, it } from "vitest";
import {
  decodeWorkbenchQueryState,
  DEFAULT_WORKBENCH_QUERY_STATE,
  encodeWorkbenchQueryState,
  type WorkbenchQueryState,
} from "@/features/workbench/workbenchQueryState";

describe("encodeWorkbenchQueryState", () => {
  it("returns empty params for the default state", () => {
    const params = encodeWorkbenchQueryState(DEFAULT_WORKBENCH_QUERY_STATE);
    expect(params.toString()).toBe("");
  });

  it("encodes only the fields that differ from the default", () => {
    const state: WorkbenchQueryState = { ...DEFAULT_WORKBENCH_QUERY_STATE, tier: 2 };
    const params = encodeWorkbenchQueryState(state);
    expect(Array.from(params.keys())).toEqual(["tier"]);
    expect(params.get("tier")).toBe("2");
  });

  it("encodes every non-default field", () => {
    const state: WorkbenchQueryState = {
      sort: "estimated_cost_impact",
      dir: "asc",
      tier: 3,
      type: "transfer",
      aogOnly: true,
    };
    const params = encodeWorkbenchQueryState(state);
    expect(params.get("sort")).toBe("estimated_cost_impact");
    expect(params.get("dir")).toBe("asc");
    expect(params.get("tier")).toBe("3");
    expect(params.get("type")).toBe("transfer");
    expect(params.get("aog")).toBe("true");
  });
});

describe("decodeWorkbenchQueryState", () => {
  it("returns the defaults for empty params", () => {
    expect(decodeWorkbenchQueryState(new URLSearchParams())).toEqual(
      DEFAULT_WORKBENCH_QUERY_STATE,
    );
  });

  it("round-trips a fully non-default state through encode/decode", () => {
    const state: WorkbenchQueryState = {
      sort: "confidence_score",
      dir: "asc",
      tier: 1,
      type: "adjust_min_max",
      aogOnly: true,
    };
    const decoded = decodeWorkbenchQueryState(encodeWorkbenchQueryState(state));
    expect(decoded).toEqual(state);
  });

  it("falls back to defaults entirely for garbage params", () => {
    const params = new URLSearchParams("?sort=bogus&dir=sideways&tier=99");
    expect(decodeWorkbenchQueryState(params)).toEqual(DEFAULT_WORKBENCH_QUERY_STATE);
  });

  it("rejects a garbage type and aog value alongside garbage sort/dir/tier", () => {
    const params = new URLSearchParams("?sort=nope&dir=up&tier=abc&type=explode&aog=maybe");
    expect(decodeWorkbenchQueryState(params)).toEqual(DEFAULT_WORKBENCH_QUERY_STATE);
  });

  it("merges partial params with defaults for the untouched fields", () => {
    const params = new URLSearchParams("?sort=confidence_score&dir=asc&tier=2");
    expect(decodeWorkbenchQueryState(params)).toEqual({
      sort: "confidence_score",
      dir: "asc",
      tier: 2,
      type: "all",
      aogOnly: false,
    });
  });

  it("treats aog=false explicitly the same as its absence", () => {
    const params = new URLSearchParams("?aog=false");
    expect(decodeWorkbenchQueryState(params)).toEqual(DEFAULT_WORKBENCH_QUERY_STATE);
  });
});
