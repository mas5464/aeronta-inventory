import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { bffClient } from "@/lib/api/client";
import { bvrQueryKey, useBvr } from "@/lib/api/useBvr";
import type { BvrReport } from "@/lib/api/types";

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const report = { schema_version: "1.1.0", tenant_id: "acme" } as unknown as BvrReport;

describe("bvrQueryKey", () => {
  it("is scoped by tenant under a stable 'bvr' prefix", () => {
    expect(bvrQueryKey("acme")).toEqual(["bvr", "acme"]);
  });
});

describe("useBvr", () => {
  afterEach(() => vi.restoreAllMocks());

  it("fetches the BVR for the tenant", async () => {
    vi.spyOn(bffClient, "getBvr").mockResolvedValue(report);
    const { result } = renderHook(() => useBvr("acme"), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.schema_version).toBe("1.1.0");
    expect(bffClient.getBvr).toHaveBeenCalledWith("acme");
  });
});
