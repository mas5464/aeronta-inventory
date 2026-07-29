import type { ReactNode } from "react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  CreatePlanningRunBody,
  PlanningRunSubmission,
  PlanningRunView,
} from "@/lib/api/planningRuns";

const api = vi.hoisted(() => ({
  createPlanningRun: vi.fn(),
  getPlanningRuns: vi.fn(),
}));

vi.mock("@/lib/api/planningRuns", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("@/lib/api/planningRuns")>();
  return {
    ...original,
    createPlanningRun: api.createPlanningRun,
    getPlanningRuns: api.getPlanningRuns,
  };
});

import {
  planningRunQueryKey,
  planningRunsQueryKey,
  useCreatePlanningRun,
  usePlanningRuns,
} from "@/lib/api/usePlanningRuns";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function run(runId: string): PlanningRunView {
  return {
    run_id: runId,
    status: "completed",
  } as PlanningRunView;
}

function wrapper(client: QueryClient) {
  return function TestQueryProvider({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        {children}
      </QueryClientProvider>
    );
  };
}

describe("planning tenant changes during in-flight work", () => {
  beforeEach(() => {
    api.createPlanningRun.mockReset();
    api.getPlanningRuns.mockReset();
  });

  it("keeps a late polling response in its originating tenant cache", async () => {
    const tenantA = deferred<PlanningRunView[]>();
    api.getPlanningRuns.mockImplementation((tenant: string) =>
      tenant === "tenant-a"
        ? tenantA.promise
        : Promise.resolve([run("run-b")]),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, rerender } = renderHook(
      ({ tenant }) => usePlanningRuns(tenant),
      {
        initialProps: { tenant: "tenant-a" },
        wrapper: wrapper(client),
      },
    );

    rerender({ tenant: "tenant-b" });
    await waitFor(() => {
      expect(result.current.data?.[0]?.run_id).toBe("run-b");
    });

    await act(async () => {
      tenantA.resolve([run("run-a")]);
      await tenantA.promise;
    });

    expect(result.current.data?.[0]?.run_id).toBe("run-b");
    expect(
      client.getQueryData<PlanningRunView[]>(
        planningRunsQueryKey("tenant-a"),
      )?.[0]?.run_id,
    ).toBe("run-a");
    expect(
      client.getQueryData<PlanningRunView[]>(
        planningRunsQueryKey("tenant-b"),
      )?.[0]?.run_id,
    ).toBe("run-b");
  });

  it("writes a late mutation result only to its submitted tenant", async () => {
    const pending = deferred<PlanningRunSubmission>();
    api.createPlanningRun.mockReturnValue(pending.promise);
    const client = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const { result, rerender } = renderHook(
      ({ tenant }) => useCreatePlanningRun(tenant),
      {
        initialProps: { tenant: "tenant-a" },
        wrapper: wrapper(client),
      },
    );

    act(() => {
      result.current.mutate({} as CreatePlanningRunBody);
    });
    await waitFor(() => {
      expect(api.createPlanningRun).toHaveBeenCalledWith(
        expect.anything(),
        "tenant-a",
      );
    });
    rerender({ tenant: "tenant-b" });

    await act(async () => {
      pending.resolve({ run: run("run-a"), created: true });
      await pending.promise;
    });
    await waitFor(() => {
      expect(
        client.getQueryData(
          planningRunQueryKey("tenant-a", "run-a"),
        ),
      ).toEqual(run("run-a"));
    });

    expect(
      client.getQueryData(planningRunQueryKey("tenant-b", "run-a")),
    ).toBeUndefined();
  });
});
