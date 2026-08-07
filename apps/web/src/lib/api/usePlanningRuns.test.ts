import { describe, expect, it } from "vitest";
import {
  PLANNING_RUN_POLL_MS,
  planningRunPollInterval,
  planningRunQueryKey,
  planningRunSelectionsQueryKey,
  planningRunsQueryKey,
} from "@/lib/api/usePlanningRuns";
import type { PlanningRunView } from "@/lib/api/planningRuns";

describe("planning query isolation and polling", () => {
  it("includes the tenant and bounded selection filters in every cache key", () => {
    expect(planningRunsQueryKey("tenant-a")).toEqual([
      "planning-runs",
      "tenant-a",
    ]);
    expect(planningRunQueryKey("tenant-b", "run-1")).toEqual([
      "planning-runs",
      "tenant-b",
      "run-1",
    ]);
    expect(
      planningRunSelectionsQueryKey("tenant-a", "run-1", {
        limit: 25,
        offset: 50,
        selectedIsNoChange: false,
      }),
    ).toEqual([
      "planning-runs",
      "tenant-a",
      "run-1",
      "selections",
      {
        limit: 25,
        offset: 50,
        decisionKey: null,
        selectedIsNoChange: false,
      },
    ]);
  });

  it("polls only active run states", () => {
    expect(
      planningRunPollInterval({ status: "queued" } as PlanningRunView),
    ).toBe(PLANNING_RUN_POLL_MS);
    expect(
      planningRunPollInterval({ status: "running" } as PlanningRunView),
    ).toBe(PLANNING_RUN_POLL_MS);
    expect(
      planningRunPollInterval({ status: "completed" } as PlanningRunView),
    ).toBe(false);
    expect(
      planningRunPollInterval({ status: "infeasible" } as PlanningRunView),
    ).toBe(false);
    expect(
      planningRunPollInterval({ status: "failed" } as PlanningRunView),
    ).toBe(false);
  });
});
