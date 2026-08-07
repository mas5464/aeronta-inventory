import type { ReactNode } from "react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  replayRunsQueryKey,
  useReplayRuns,
  useSubmitReplayRun,
  type CreateReplayRunBody,
  type ReplayRun,
} from "@/lib/api/replay";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function response<T>(body: T, status = 200): Response {
  return {
    ok: true,
    status,
    statusText: "OK",
    json: () => Promise.resolve(body),
  } as Response;
}

function run(replayId: string): ReplayRun {
  return {
    replay_id: replayId,
    status: "completed",
  } as ReplayRun;
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

describe("replay tenant changes during in-flight work", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps a late polling response in its originating tenant cache", async () => {
    const tenantA = deferred<Response>();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) =>
        url.includes("/tenant-a/")
          ? tenantA.promise
          : Promise.resolve(response([run("replay-b")])),
      ),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { result, rerender } = renderHook(
      ({ tenant }) => useReplayRuns(tenant),
      {
        initialProps: { tenant: "tenant-a" },
        wrapper: wrapper(client),
      },
    );

    rerender({ tenant: "tenant-b" });
    await waitFor(() => {
      expect(result.current.data?.[0]?.replay_id).toBe("replay-b");
    });

    await act(async () => {
      tenantA.resolve(response([run("replay-a")]));
      await tenantA.promise;
    });

    expect(result.current.data?.[0]?.replay_id).toBe("replay-b");
    expect(
      client.getQueryData<ReplayRun[]>(replayRunsQueryKey("tenant-a"))?.[0]
        .replay_id,
    ).toBe("replay-a");
  });

  it("writes a late replay submission only to its submitted tenant", async () => {
    const pending = deferred<Response>();
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending.promise));
    const client = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const { result, rerender } = renderHook(
      ({ tenant }) => useSubmitReplayRun(tenant),
      {
        initialProps: { tenant: "tenant-a" },
        wrapper: wrapper(client),
      },
    );
    const body = {
      universe_ref: "trusted-q1",
      currency: "USD",
      current_policy_label: "Current",
      challenger_policy_label: "Challenger",
      comparison_rule: "matched_budget",
      match_tolerance: "0",
    } satisfies CreateReplayRunBody;

    act(() => {
      result.current.mutate(body);
    });
    rerender({ tenant: "tenant-b" });

    await act(async () => {
      pending.resolve(
        response({ run: run("replay-a"), created: true }, 201),
      );
      await pending.promise;
    });
    await waitFor(() => {
      expect(
        client.getQueryData<ReplayRun[]>(
          replayRunsQueryKey("tenant-a"),
        )?.[0]?.replay_id,
      ).toBe("replay-a");
    });

    expect(
      client.getQueryData(replayRunsQueryKey("tenant-b")),
    ).toBeUndefined();
  });
});
