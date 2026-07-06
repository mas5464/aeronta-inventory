import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { bffClient } from "@/lib/api/client";
import { historyQueryKey, useHistory, useRollback } from "@/lib/api/useWriteback";
import type { HistoryEntry, RollbackResult } from "@/lib/api/types";

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { client, Wrapper: ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  ) };
}

const entry: HistoryEntry = {
  tenant_id: "acme", pn: "P1", location: "YYC", version: 1, status: "written",
  old_values: null, new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
  provenance_id: "prov-1", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
  idempotency_key: null, parent_version: null, changed_at: "2026-06-20T00:00:00Z",
};

describe("historyQueryKey", () => {
  it("is scoped by tenant/pn/location under a stable 'history' prefix", () => {
    expect(historyQueryKey("acme", "P1", "YYC")).toEqual(["history", "acme", "P1", "YYC"]);
  });
});

describe("useHistory", () => {
  afterEach(() => vi.restoreAllMocks());

  it("fetches history when pn+location are present", async () => {
    vi.spyOn(bffClient, "getHistory").mockResolvedValue([entry]);
    const { Wrapper } = wrapper();
    const { result } = renderHook(() => useHistory("P1", "YYC", "acme"), { wrapper: Wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.[0].version).toBe(1);
  });

  it("is disabled (does not fetch) when pn or location is empty", () => {
    const spy = vi.spyOn(bffClient, "getHistory").mockResolvedValue([]);
    const { Wrapper } = wrapper();
    renderHook(() => useHistory("", "YYC", "acme"), { wrapper: Wrapper });
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("useRollback", () => {
  afterEach(() => vi.restoreAllMocks());

  it("invalidates the history query on success", async () => {
    const rollbackResult: RollbackResult = {
      tenant_id: "acme", pn: "P1", location: "YYC", status: "rolled_back",
      from_values: null, to_values: null, reverted_from_version: 1, new_version: 2,
      rolled_back_at: "2026-07-06T00:00:00Z", error_message: null,
    };
    vi.spyOn(bffClient, "rollback").mockResolvedValue(rollbackResult);
    const { client, Wrapper } = wrapper();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderHook(() => useRollback("acme"), { wrapper: Wrapper });
    result.current.mutate({
      tenant_id: "acme", pn: "P1", location: "YYC", reason: "r", principal: "planner",
      requested_at: "2026-07-06T00:00:00Z",
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["history", "acme"] });
  });
});
