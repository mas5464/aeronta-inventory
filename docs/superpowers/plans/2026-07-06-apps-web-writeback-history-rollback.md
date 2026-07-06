# apps/web Writeback History + Rollback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add writeback-history timeline + rollback (with a confirm dialog) to `apps/web`, on Part Drill-Down, plus a Workbench "History" deep-link.

**Architecture:** Reuse the existing BFF `history`/`rollback` routes (no backend change). Add TS types + two client methods + two hooks, a `WritebackHistory` timeline section on Part Drill-Down, a `useFocusTrap` rollback confirm dialog, and a Workbench row deep-link to `/parts/{pn}/{location}#history`.

**Tech Stack:** React 18 + TypeScript + TanStack Query + Vitest + Testing Library (`apps/web`), over the existing FastAPI BFF.

## Global Constraints

- Do NOT touch the (now-retired) `apps/planner-ui` or the BFF — the `history`/`rollback` routes already exist and are unchanged.
- Do NOT implement Waves 3–4 territory (no Reports/BVR view, no dark theme).
- The value dicts (`old_values`/`new_values`, and rollback `from_values`/`to_values`) have exactly these keys: `rop, eoq, safety_stock, max_stock` (backend `_FIELDS`). Format them as `ROP {rop} · EOQ {eoq} · SS {safety_stock} · Max {max_stock}`.
- `WritebackStatus` values: `written, deferred_open_order, failed, shadowed`. `RollbackStatus` values: `rolled_back, outside_window, nothing_to_revert`.
- `revertible` rule: the latest entry whose `status === "written"` exists AND has a non-null `old_values`.
- History rows render WITHOUT `ProvChip` — they are audit events, not `MetricValue`s (a deliberate documented boundary). They carry `provenance_id`/`changed_by_principal` inline.
- `principal` is hardcoded `"planner"` (apps/web has no auth yet). `reason` is collected from the confirm dialog (required).
- On rollback success, invalidate ONLY the `["history", tenant]` query — the part-context `current_policy` comes from the feature-store snapshot and is unaffected by ledger writes.
- Frontend commands (run from `apps/web`): tests `npm test -- <file>`; typecheck+build `npm run build`; lint `npm run lint` (2 pre-existing shadcn/ui `react-refresh` warnings on badge.tsx/button.tsx are acceptable).
- Standing (no work this wave): keep `apps/web` embeddable in eMRO later — HashRouter already in use, don't preclude it.

---

### Task 1: Data layer — TS types + client methods

**Files:**
- Modify: `apps/web/src/lib/api/types.ts` (add history/rollback types near the other mirror types)
- Modify: `apps/web/src/lib/api/client.ts` (add `getHistory` + `rollback` methods to `bffClient`)
- Test: `apps/web/src/lib/api/client.test.ts` (append a `describe` block)

**Interfaces:**
- Consumes: `request<T>`, `BASE_URL`, `DEFAULT_TENANT` (client.ts); `AutonomyTier`, `PolicyView` (types.ts).
- Produces: types `WritebackStatus`, `RollbackStatus`, `HistoryEntry`, `RollbackRequest`, `RollbackResult`; methods `bffClient.getHistory(pn, location, tenant?) => Promise<HistoryEntry[]>` and `bffClient.rollback(req: RollbackRequest, tenant?) => Promise<RollbackResult>`.

- [ ] **Step 1: Write the failing tests**

Append to `apps/web/src/lib/api/client.test.ts` (add `HistoryEntry`, `RollbackRequest`, `RollbackResult` to the type import from `@/lib/api/types` and confirm `bffClient`, `ApiError`, `DEFAULT_BFF_URL` are already imported):

```typescript
const sampleHistory: HistoryEntry[] = [
  {
    tenant_id: "acme", pn: "19000-231-3", location: "YYC", version: 1,
    status: "written", old_values: null, new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
    provenance_id: "prov-1", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
    idempotency_key: "k1", parent_version: null, changed_at: "2026-06-20T00:00:00Z",
  },
];

describe("bffClient.getHistory", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GETs the (pn,location)-scoped history route with URL-encoded query params", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleHistory) });
    vi.stubGlobal("fetch", fetchMock);

    const result = await bffClient.getHistory("19000-231-3", "YYC", "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/history?pn=19000-231-3&location=YYC`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result[0].new_values.rop).toBe(3);
  });

  it("URL-encodes pn/location containing special characters", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve([]) });
    vi.stubGlobal("fetch", fetchMock);
    await bffClient.getHistory("A/B 1", "Y Z", "acme");
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("pn=A%2FB+1");
    expect(url).toContain("location=Y+Z");
  });

  it("throws an ApiError on a non-OK response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 404, statusText: "Not Found", json: () => Promise.resolve({ detail: "unknown tenant ghost" }),
    }));
    await expect(bffClient.getHistory("p", "l", "ghost")).rejects.toThrow(ApiError);
  });
});

describe("bffClient.rollback", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("POSTs the RollbackRequest body to .../rollback", async () => {
    const rollbackResult: RollbackResult = {
      tenant_id: "acme", pn: "19000-231-3", location: "YYC", status: "rolled_back",
      from_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
      to_values: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
      reverted_from_version: 1, new_version: 2, rolled_back_at: "2026-07-06T00:00:00Z", error_message: null,
    };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(rollbackResult) });
    vi.stubGlobal("fetch", fetchMock);

    const req: RollbackRequest = {
      tenant_id: "acme", pn: "19000-231-3", location: "YYC",
      reason: "wrong policy", principal: "planner", requested_at: "2026-07-06T00:00:00Z",
    };
    const result = await bffClient.rollback(req, "acme");

    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/rollback`,
      expect.objectContaining({ method: "POST", body: JSON.stringify(req) }),
    );
    expect(result.status).toBe("rolled_back");
    expect(result.new_version).toBe(2);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd apps/web && npm test -- client.test`
Expected: FAIL — `getHistory`/`rollback` not on `bffClient`, and the new types don't exist (compile error).

- [ ] **Step 3: Add the types**

In `apps/web/src/lib/api/types.ts`, add (near the other BFF mirror types):

```typescript
/** WritebackStatus — mirror of trax_io_spine.contracts.WritebackStatus. */
export type WritebackStatus = "written" | "deferred_open_order" | "failed" | "shadowed";

/** RollbackStatus — mirror of trax_io_spine.contracts.RollbackStatus. */
export type RollbackStatus = "rolled_back" | "outside_window" | "nothing_to_revert";

/**
 * One writeback-ledger entry for a (pn, location), mirroring
 * trax_io_spine.contracts.HistoryEntry. Audit event — rendered as a timeline
 * row, NOT a MetricValue (carries its own provenance_id/changed_by inline).
 */
export interface HistoryEntry {
  tenant_id: string;
  pn: string;
  location: string;
  version: number;
  status: WritebackStatus;
  old_values: Record<string, number> | null;
  new_values: Record<string, number>;
  provenance_id: string;
  tier: AutonomyTier | null;
  agent_version: string;
  changed_by_principal: string;
  idempotency_key: string | null;
  parent_version: number | null;
  changed_at: string;
}

/** RollbackRequest — mirror of trax_io_spine.contracts.RollbackRequest. */
export interface RollbackRequest {
  tenant_id: string;
  pn: string;
  location: string;
  reason: string;
  principal: string;
  requested_at: string;
}

/** RollbackResult — mirror of trax_io_spine.contracts.RollbackResult. */
export interface RollbackResult {
  tenant_id: string;
  pn: string;
  location: string;
  status: RollbackStatus;
  from_values: Record<string, number> | null;
  to_values: Record<string, number> | null;
  reverted_from_version: number | null;
  new_version: number | null;
  rolled_back_at: string | null;
  error_message: string | null;
}
```

- [ ] **Step 4: Add the client methods**

In `apps/web/src/lib/api/client.ts`, add `HistoryEntry`, `RollbackRequest`, `RollbackResult` to the type import block, then add these two methods to the `bffClient` object (e.g. after `getPartContext`):

```typescript
  getHistory(
    pn: string,
    location: string,
    tenant: string = DEFAULT_TENANT,
  ): Promise<HistoryEntry[]> {
    const params = new URLSearchParams({ pn, location });
    return request<HistoryEntry[]>(
      `/v1/tenants/${encodeURIComponent(tenant)}/history?${params.toString()}`,
    );
  },

  rollback(req: RollbackRequest, tenant: string = DEFAULT_TENANT): Promise<RollbackResult> {
    return request<RollbackResult>(`/v1/tenants/${encodeURIComponent(tenant)}/rollback`, {
      method: "POST",
      body: JSON.stringify(req),
    });
  },
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd apps/web && npm test -- client.test`
Expected: the new tests pass, all existing client tests still green.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/api/types.ts apps/web/src/lib/api/client.ts apps/web/src/lib/api/client.test.ts
git commit -m "feat(web): add history/rollback TS types + bffClient methods"
```

---

### Task 2: Hooks — useHistory + useRollback

**Files:**
- Create: `apps/web/src/lib/api/useWriteback.ts`
- Test: `apps/web/src/lib/api/useWriteback.test.ts`

**Interfaces:**
- Consumes: `bffClient.getHistory`/`bffClient.rollback` (Task 1); `HistoryEntry`/`RollbackRequest`/`RollbackResult` types; TanStack `useQuery`/`useMutation`/`useQueryClient`.
- Produces: `historyQueryKey(tenant, pn, location)`; `useHistory(pn, location, tenant?)`; `useRollback(tenant?)` (a `useMutation<RollbackResult, Error, RollbackRequest>` that invalidates `["history", tenant]` on success).

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/lib/api/useWriteback.test.ts` (mirror the harness style of existing hook/component tests — a `QueryClientProvider` with `retry: false`):

```typescript
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
  afterEach(() => vi.unstubAllGlobals());

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
  afterEach(() => vi.unstubAllGlobals());

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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- useWriteback`
Expected: FAIL — `useWriteback` module doesn't exist.

- [ ] **Step 3: Implement the hooks**

Create `apps/web/src/lib/api/useWriteback.ts`:

```typescript
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bffClient, DEFAULT_TENANT } from "@/lib/api/client";
import type { HistoryEntry, RollbackRequest, RollbackResult } from "@/lib/api/types";

export function historyQueryKey(tenant: string, pn: string, location: string) {
  return ["history", tenant, pn, location] as const;
}

/** Writeback history for a (pn, location). Disabled until both are present. */
export function useHistory(pn: string, location: string, tenant: string = DEFAULT_TENANT) {
  return useQuery<HistoryEntry[]>({
    queryKey: historyQueryKey(tenant, pn, location),
    queryFn: () => bffClient.getHistory(pn, location, tenant),
    enabled: Boolean(pn) && Boolean(location),
  });
}

/** Rollback mutation — invalidates the tenant's history queries on success. */
export function useRollback(tenant: string = DEFAULT_TENANT) {
  const queryClient = useQueryClient();
  return useMutation<RollbackResult, Error, RollbackRequest>({
    mutationFn: (req: RollbackRequest) => bffClient.rollback(req, tenant),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["history", tenant] }),
  });
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- useWriteback`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/api/useWriteback.ts apps/web/src/lib/api/useWriteback.test.ts
git commit -m "feat(web): add useHistory + useRollback hooks"
```

---

### Task 3: WritebackHistory timeline component

**Files:**
- Create: `apps/web/src/features/part/WritebackHistory.tsx`
- Create: `apps/web/src/features/part/writebackView.ts` (pure helpers: value summary, status label, revertible)
- Test: `apps/web/src/features/part/WritebackHistory.test.tsx`, `apps/web/src/features/part/writebackView.test.ts`

**Interfaces:**
- Consumes: `useHistory` (Task 2); `HistoryEntry`/`WritebackStatus` types; `<QueryState>`'s `QueryLoading`/`QueryError`/`QueryEmpty` (`components/QueryState.tsx` — confirm exact exports before use).
- Produces: `formatPolicyValues(v: Record<string, number>): string`; `writebackStatusLabel(s: WritebackStatus): string`; `latestRevertibleEntry(history: HistoryEntry[]): HistoryEntry | null`; the `WritebackHistory` component (props `{ pn: string; location: string; onRollback: (entry: HistoryEntry) => void }`), rendering a section with `id="history"`.

- [ ] **Step 1: Write the failing tests (pure helpers first)**

Create `apps/web/src/features/part/writebackView.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { formatPolicyValues, latestRevertibleEntry, writebackStatusLabel, writebackStatusVariant } from "@/features/part/writebackView";
import type { HistoryEntry } from "@/lib/api/types";

function entry(over: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    tenant_id: "acme", pn: "P1", location: "YYC", version: 1, status: "written",
    old_values: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
    new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
    provenance_id: "prov-1", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
    idempotency_key: null, parent_version: null, changed_at: "2026-06-20T00:00:00Z", ...over,
  };
}

describe("formatPolicyValues", () => {
  it("formats the rop/eoq/safety_stock/max_stock keys", () => {
    expect(formatPolicyValues({ rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 }))
      .toBe("ROP 3 · EOQ 5 · SS 2 · Max 8");
  });
});

describe("writebackStatusLabel", () => {
  it("maps enum values to human labels", () => {
    expect(writebackStatusLabel("written")).toBe("Written");
    expect(writebackStatusLabel("deferred_open_order")).toBe("Deferred (open order)");
    expect(writebackStatusLabel("failed")).toBe("Failed");
    expect(writebackStatusLabel("shadowed")).toBe("Shadowed");
  });
});

describe("writebackStatusVariant", () => {
  it("maps each status to a Badge variant (color-coded; text label always accompanies it)", () => {
    expect(writebackStatusVariant("written")).toBe("good");
    expect(writebackStatusVariant("deferred_open_order")).toBe("warn");
    expect(writebackStatusVariant("failed")).toBe("bad");
    expect(writebackStatusVariant("shadowed")).toBe("default");
  });
});

describe("latestRevertibleEntry", () => {
  it("returns the latest written entry with non-null old_values", () => {
    const h = [entry({ version: 1 }), entry({ version: 2, status: "shadowed" })];
    expect(latestRevertibleEntry(h)?.version).toBe(1);
  });
  it("returns null when the latest written entry has null old_values", () => {
    expect(latestRevertibleEntry([entry({ version: 1, old_values: null })])).toBeNull();
  });
  it("returns null for empty history", () => {
    expect(latestRevertibleEntry([])).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- writebackView`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the pure helpers**

Create `apps/web/src/features/part/writebackView.ts`:

```typescript
import type { HistoryEntry, WritebackStatus } from "@/lib/api/types";

/** Verbatim from planner-ui's valueSummary — the value dict keys are fixed. */
export function formatPolicyValues(v: Record<string, number>): string {
  return `ROP ${v.rop} · EOQ ${v.eoq} · SS ${v.safety_stock} · Max ${v.max_stock}`;
}

const STATUS_LABELS: Record<WritebackStatus, string> = {
  written: "Written",
  deferred_open_order: "Deferred (open order)",
  failed: "Failed",
  shadowed: "Shadowed",
};

export function writebackStatusLabel(s: WritebackStatus): string {
  return STATUS_LABELS[s];
}

/** Badge variant per status — color reinforces the always-present text label
 * (color-not-only). Uses the existing Badge variants (good/warn/bad/default). */
const STATUS_VARIANTS: Record<WritebackStatus, "good" | "warn" | "bad" | "default"> = {
  written: "good",
  deferred_open_order: "warn",
  failed: "bad",
  shadowed: "default",
};

export function writebackStatusVariant(s: WritebackStatus): "good" | "warn" | "bad" | "default" {
  return STATUS_VARIANTS[s];
}

/**
 * The latest applied write that can be reverted — mirrors planner-ui: scan
 * newest-first for a `written` entry whose old_values (the prior value to
 * restore) is known.
 */
export function latestRevertibleEntry(history: HistoryEntry[]): HistoryEntry | null {
  const latestWritten = [...history].reverse().find((e) => e.status === "written");
  return latestWritten && latestWritten.old_values !== null ? latestWritten : null;
}
```

- [ ] **Step 4: Write the failing component test**

First open `apps/web/src/components/QueryState.tsx` and confirm the exact exported names (`QueryLoading`, `QueryError`, `QueryEmpty`) and their props before importing them. Create `apps/web/src/features/part/WritebackHistory.test.tsx`:

```typescript
import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WritebackHistory } from "@/features/part/WritebackHistory";
import { bffClient } from "@/lib/api/client";
import type { HistoryEntry } from "@/lib/api/types";

function entry(over: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    tenant_id: "acme", pn: "P1", location: "YYC", version: 1, status: "written",
    old_values: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
    new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
    provenance_id: "prov-1", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
    idempotency_key: null, parent_version: null, changed_at: "2026-06-20T00:00:00Z", ...over,
  };
}

function renderIt(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("WritebackHistory", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders a newest-first timeline with value summary + principal", async () => {
    vi.spyOn(bffClient, "getHistory").mockResolvedValue([
      entry({ version: 1 }),
      entry({ version: 2, new_values: { rop: 4, eoq: 6, safety_stock: 3, max_stock: 10 } }),
    ]);
    renderIt(<WritebackHistory pn="P1" location="YYC" onRollback={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/ROP 4/)).toBeInTheDocument());
    const rows = screen.getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("v2"); // newest first
  });

  it("shows the empty state when there is no history", async () => {
    vi.spyOn(bffClient, "getHistory").mockResolvedValue([]);
    renderIt(<WritebackHistory pn="P1" location="YYC" onRollback={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/No prior writes for P1 · YYC/)).toBeInTheDocument());
  });

  it("disables the rollback button when nothing is revertible", async () => {
    vi.spyOn(bffClient, "getHistory").mockResolvedValue([entry({ status: "shadowed" })]);
    renderIt(<WritebackHistory pn="P1" location="YYC" onRollback={vi.fn()} />);
    const btn = await screen.findByRole("button", { name: /roll back/i });
    expect(btn).toBeDisabled();
  });

  it("enables rollback and calls onRollback with the revertible entry", async () => {
    const onRollback = vi.fn();
    vi.spyOn(bffClient, "getHistory").mockResolvedValue([entry({ version: 1 })]);
    renderIt(<WritebackHistory pn="P1" location="YYC" onRollback={onRollback} />);
    const btn = await screen.findByRole("button", { name: /roll back/i });
    expect(btn).toBeEnabled();
    btn.click();
    expect(onRollback).toHaveBeenCalledWith(expect.objectContaining({ version: 1 }));
  });
});
```

- [ ] **Step 5: Run to verify it fails**

Run: `cd apps/web && npm test -- WritebackHistory`
Expected: FAIL — component missing.

- [ ] **Step 6: Implement the component**

Create `apps/web/src/features/part/WritebackHistory.tsx`. Use the exact `QueryState` export names you confirmed in Step 4; the code below assumes `QueryLoading`/`QueryError` (adjust to the real names if they differ). Match the Tailwind class vocabulary already used in Part Drill-Down (`text-ink`, `text-ink-2`, `border-line`, `Card`/`CardHeader`/`CardTitle`/`CardContent`, `Button`).

```tsx
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { useHistory } from "@/lib/api/useWriteback";
import type { HistoryEntry } from "@/lib/api/types";
import { formatPolicyValues, latestRevertibleEntry, writebackStatusLabel, writebackStatusVariant } from "@/features/part/writebackView";

function changedOn(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(0, 10);
}

export interface WritebackHistoryProps {
  pn: string;
  location: string;
  onRollback: (entry: HistoryEntry) => void;
}

export function WritebackHistory({ pn, location, onRollback }: WritebackHistoryProps) {
  const { data, isPending, isError, error, refetch } = useHistory(pn, location);
  const history = data ?? [];
  const revertible = latestRevertibleEntry(history);

  return (
    <Card id="history">
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <CardTitle>Writeback history</CardTitle>
        <Button
          variant="outline"
          size="sm"
          disabled={revertible === null}
          title={revertible === null ? "Nothing to roll back — no prior agent-applied value is on record" : undefined}
          onClick={() => revertible && onRollback(revertible)}
        >
          Roll back last change
        </Button>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <QueryLoading label={`Loading history for ${pn} / ${location}…`} />
        ) : isError ? (
          <QueryError label={`Failed to load history for ${pn} / ${location}`} error={error} onRetry={() => refetch()} />
        ) : history.length === 0 ? (
          <p className="text-sm text-ink-2">No prior writes for {pn} · {location}.</p>
        ) : (
          <ol className="flex flex-col gap-2">
            {[...history].reverse().map((e) => (
              <li key={e.version} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-2 text-sm">
                <span className="font-medium text-ink">v{e.version}</span>
                <Badge variant={writebackStatusVariant(e.status)}>{writebackStatusLabel(e.status)}</Badge>
                <span className="text-ink">{formatPolicyValues(e.new_values)}</span>
                <span className="text-xs text-ink-3">{changedOn(e.changed_at)} · {e.changed_by_principal}</span>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 7: Run both test files to verify they pass**

Run: `cd apps/web && npm test -- writebackView WritebackHistory`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/features/part/writebackView.ts apps/web/src/features/part/writebackView.test.ts apps/web/src/features/part/WritebackHistory.tsx apps/web/src/features/part/WritebackHistory.test.tsx
git commit -m "feat(web): add WritebackHistory timeline section + pure view helpers"
```

---

### Task 4: RollbackConfirmDialog

**Files:**
- Create: `apps/web/src/features/part/RollbackConfirmDialog.tsx`
- Test: `apps/web/src/features/part/RollbackConfirmDialog.test.tsx`

**Interfaces:**
- Consumes: `useFocusTrap(ref, onClose)` (`lib/useFocusTrap.ts`); `formatPolicyValues` (Task 3); `HistoryEntry` type; `Button`.
- Produces: `RollbackConfirmDialog` (props `{ entry: HistoryEntry; onCancel: () => void; onConfirm: (reason: string) => void; isSubmitting?: boolean; resultError?: string | null }`). Models `RejectDialog`'s structure (`role="dialog" aria-modal="true"`, focus trap, Cancel/Confirm). Confirm disabled until reason non-empty. Shows `from → to` = the entry's `new_values` → `old_values`.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/part/RollbackConfirmDialog.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RollbackConfirmDialog } from "@/features/part/RollbackConfirmDialog";
import type { HistoryEntry } from "@/lib/api/types";

const entry: HistoryEntry = {
  tenant_id: "acme", pn: "P1", location: "YYC", version: 3, status: "written",
  old_values: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
  new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
  provenance_id: "prov-1", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
  idempotency_key: null, parent_version: null, changed_at: "2026-06-20T00:00:00Z",
};

describe("RollbackConfirmDialog", () => {
  it("shows the from→to values and requires a reason before confirming", async () => {
    const onConfirm = vi.fn();
    render(<RollbackConfirmDialog entry={entry} onCancel={vi.fn()} onConfirm={onConfirm} />);
    // from = new_values, to = old_values
    expect(screen.getByText(/ROP 3 · EOQ 5 · SS 2 · Max 8/)).toBeInTheDocument();
    expect(screen.getByText(/ROP 2 · EOQ 4 · SS 1 · Max 6/)).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: /confirm rollback/i });
    expect(confirm).toBeDisabled(); // no reason yet
    await userEvent.type(screen.getByLabelText(/reason/i), "policy was wrong");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledWith("policy was wrong");
  });

  it("calls onCancel from the Cancel button", async () => {
    const onCancel = vi.fn();
    render(<RollbackConfirmDialog entry={entry} onCancel={onCancel} onConfirm={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("surfaces a result error inline", () => {
    render(<RollbackConfirmDialog entry={entry} onCancel={vi.fn()} onConfirm={vi.fn()} resultError="outside rollback window" />);
    expect(screen.getByText(/outside rollback window/i)).toBeInTheDocument();
  });

  it("is a labelled modal dialog", () => {
    render(<RollbackConfirmDialog entry={entry} onCancel={vi.fn()} onConfirm={vi.fn()} />);
    const dlg = screen.getByRole("dialog");
    expect(dlg).toHaveAttribute("aria-modal", "true");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- RollbackConfirmDialog`
Expected: FAIL — component missing.

- [ ] **Step 3: Implement the dialog**

Create `apps/web/src/features/part/RollbackConfirmDialog.tsx` (mirror `RejectDialog`'s structure — `useRef` + `useFocusTrap`, inline `role="dialog"`):

```tsx
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { useFocusTrap } from "@/lib/useFocusTrap";
import { formatPolicyValues } from "@/features/part/writebackView";
import type { HistoryEntry } from "@/lib/api/types";

export interface RollbackConfirmDialogProps {
  entry: HistoryEntry;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
  isSubmitting?: boolean;
  resultError?: string | null;
}

export function RollbackConfirmDialog({ entry, onCancel, onConfirm, isSubmitting, resultError }: RollbackConfirmDialogProps) {
  const [reason, setReason] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(containerRef, onCancel);

  return (
    <div
      ref={containerRef}
      role="dialog"
      aria-modal="true"
      aria-label={`Roll back ${entry.pn} / ${entry.location}`}
      className="flex flex-col gap-2 rounded-md border border-line bg-panel-2 p-3"
    >
      <p className="text-sm text-ink">
        Reverting <span className="font-medium">v{entry.version}</span> — this restores the prior value.
      </p>
      <div className="text-xs text-ink-2">
        <div>From: {entry.new_values ? formatPolicyValues(entry.new_values) : "—"}</div>
        <div>To: {entry.old_values ? formatPolicyValues(entry.old_values) : "—"}</div>
      </div>
      <label className="flex flex-col gap-1 text-xs text-ink-2">
        Reason
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className="h-8 rounded-control border border-line bg-panel px-2 text-sm text-ink"
          placeholder="Why are you rolling this back?"
        />
      </label>
      {resultError && <p role="alert" className="text-xs text-bad">{resultError}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} disabled={isSubmitting}>Cancel</Button>
        <Button
          variant="default"
          size="sm"
          onClick={() => onConfirm(reason)}
          disabled={isSubmitting || reason.trim() === ""}
        >
          Confirm rollback
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- RollbackConfirmDialog`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/part/RollbackConfirmDialog.tsx apps/web/src/features/part/RollbackConfirmDialog.test.tsx
git commit -m "feat(web): add RollbackConfirmDialog (useFocusTrap, required reason, from→to)"
```

---

### Task 5: Wire history + rollback into Part Drill-Down (+ #history scroll)

**Files:**
- Modify: `apps/web/src/features/part/PartDrillDown.tsx`
- Test: `apps/web/src/features/part/PartDrillDown.test.tsx` (extend existing)

**Interfaces:**
- Consumes: `WritebackHistory` (Task 3), `RollbackConfirmDialog` (Task 4), `useRollback` (Task 2), `HistoryEntry`/`RollbackRequest` types, `useLocation` (react-router-dom, already available via the app's router).
- Produces: the assembled feature on the page. No new exports.

- [ ] **Step 1: First fix the existing tests' fetch mock (REQUIRED — they will break otherwise)**

The existing `PartDrillDown.test.tsx` tests stub `fetch` with a single blanket mock that returns `samplePartContext` for **every** call:
```typescript
vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(samplePartContext) }));
```
Once this task mounts `WritebackHistory`, the page also fires `bffClient.getHistory` → `GET .../history` through that same `fetch`, which would return a `PartContext` object where the component expects a `HistoryEntry[]`, throwing on `[...history]`. So the blanket stub must become URL-aware. Add a helper near the top of the test file and use it in place of the blanket stub in the existing render tests:

```typescript
function stubFetch(history: HistoryEntry[] = []) {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
    if (url.includes("/history")) return Promise.resolve({ ok: true, json: () => Promise.resolve(history) });
    return Promise.resolve({ ok: true, json: () => Promise.resolve(samplePartContext) });
  }));
}
```
Replace the blanket `vi.stubGlobal("fetch", …samplePartContext…)` in each existing render test with `stubFetch();` (empty history — the existing assertions are unaffected, they just no longer choke on the history call). Add `HistoryEntry`, `RollbackResult` to the test's type imports.

Then add the new test (rollback path uses `bffClient.rollback` — spy on it directly since it's a POST the `stubFetch` router doesn't model):

```typescript
it("renders the writeback history section and rolls back via the confirm dialog", async () => {
  stubFetch([
    { tenant_id: "acme", pn: "19000-231-3", location: "YYC", version: 1, status: "written",
      old_values: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
      new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
      provenance_id: "p", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
      idempotency_key: null, parent_version: null, changed_at: "2026-06-20T00:00:00Z" },
  ]);
  const rollbackSpy = vi.spyOn(bffClient, "rollback").mockResolvedValue({
    tenant_id: "acme", pn: "19000-231-3", location: "YYC", status: "rolled_back",
    from_values: null, to_values: null, reverted_from_version: 1, new_version: 2,
    rolled_back_at: "2026-07-06T00:00:00Z", error_message: null,
  });

  renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");
  await userEvent.click(await screen.findByRole("button", { name: /roll back/i }));
  await userEvent.type(screen.getByLabelText(/reason/i), "wrong");
  await userEvent.click(screen.getByRole("button", { name: /confirm rollback/i }));
  await waitFor(() => expect(rollbackSpy).toHaveBeenCalledWith(
    expect.objectContaining({ pn: "19000-231-3", location: "YYC", reason: "wrong", principal: "planner" }),
    expect.anything(),
  ));
});
```

Add `import userEvent from "@testing-library/user-event";` and `import { bffClient } from "@/lib/api/client";` if not already present.

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- PartDrillDown`
Expected: FAIL — no rollback button / history section on the page yet.

- [ ] **Step 3: Wire it into the page**

In `apps/web/src/features/part/PartDrillDown.tsx`:
1. Add imports: `useEffect`, `useRef`, `useState` (from react); `useLocation` (react-router-dom); `WritebackHistory`, `RollbackConfirmDialog`, `useRollback`; `HistoryEntry`, `RollbackRequest` types.
2. Inside the component (after the existing hooks), add rollback state + the mutation:

```tsx
  const location_hash = useLocation().hash;
  const [rollbackEntry, setRollbackEntry] = useState<HistoryEntry | null>(null);
  const rollbackMutation = useRollback();

  // Deep-link: honor #history by scrolling the section into view once rendered.
  useEffect(() => {
    if (location_hash === "#history") {
      document.getElementById("history")?.scrollIntoView({ behavior: "smooth" });
    }
  }, [location_hash]);
```

   (Note: `location` is already the route param name in this component; use `location_hash` for the URL hash to avoid shadowing.)
3. Just before the closing `</div>` of the page, render the history section + dialog (only when a part is loaded — inside the success branch):

```tsx
      <WritebackHistory pn={pn} location={location} onRollback={setRollbackEntry} />

      {rollbackEntry && (
        <RollbackConfirmDialog
          entry={rollbackEntry}
          isSubmitting={rollbackMutation.isPending}
          resultError={rollbackMutation.data?.error_message ?? null}
          onCancel={() => setRollbackEntry(null)}
          onConfirm={(reason) => {
            const req: RollbackRequest = {
              tenant_id: "acme", pn, location, reason, principal: "planner",
              requested_at: new Date().toISOString(),
            };
            rollbackMutation.mutate(req, { onSuccess: (res) => { if (res.status === "rolled_back") setRollbackEntry(null); } });
          }}
        />
      )}
```

   (Hardcoded `tenant_id: "acme"` matches `DEFAULT_TENANT`; apps/web is single-tenant today. On a non-`rolled_back` result, the dialog stays open and shows `error_message` via `resultError`.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- PartDrillDown`
Expected: all pass (existing + new).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/part/PartDrillDown.tsx apps/web/src/features/part/PartDrillDown.test.tsx
git commit -m "feat(web): wire WritebackHistory + rollback dialog into Part Drill-Down (+#history scroll)"
```

---

### Task 6: Workbench "History" deep-link

**Files:**
- Modify: `apps/web/src/features/workbench/Workbench.tsx` (the first `<td>` of each row, near the existing part-number `<Link>` at ~line 353)
- Test: `apps/web/src/features/workbench/Workbench.test.tsx` (extend existing)

**Interfaces:**
- Consumes: `Link` (react-router-dom, already imported in Workbench.tsx).
- Produces: a per-row "History" link to `/parts/{pn}/{location}#history`.

- [ ] **Step 1: Write the failing test**

Add to `apps/web/src/features/workbench/Workbench.test.tsx` (uses `renderWithProviders` + `mockFetchRouter` + `row()`):

```typescript
it("renders a per-row History deep-link to the part's #history section", async () => {
  const fetchMock = mockFetchRouter({
    queue: { items: [row({ pn: "P1", location: "YYC" })], total: 1, limit: 25, offset: 0 },
    killswitch: { engaged: false },
  });
  vi.stubGlobal("fetch", fetchMock);
  renderWithProviders(<Workbench />);
  const link = await screen.findByRole("link", { name: /history/i });
  expect(link).toHaveAttribute("href", "#/parts/P1/YYC#history");
});
```

(If a row's `pn` collides with another "history"-named link, scope the query — e.g. `within(row)` — matching the file's conventions.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- Workbench.test`
Expected: FAIL — no History link.

- [ ] **Step 3: Add the link**

In `apps/web/src/features/workbench/Workbench.tsx`, in the first `<td>` (which holds the part-number `<Link>`, the location, and the reason), add a History link below the reason line:

```tsx
                        <Link
                          to={`/parts/${encodeURIComponent(row.pn)}/${encodeURIComponent(row.location)}#history`}
                          className="text-xs text-brand hover:underline"
                        >
                          History
                        </Link>
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- Workbench.test`
Expected: all pass.

- [ ] **Step 5: Full frontend gate**

Run: `cd apps/web && npm test && npm run build && npm run lint`
Expected: all Vitest green, build clean (0 errors), lint 0 errors (2 pre-existing warnings acceptable).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/workbench/Workbench.tsx apps/web/src/features/workbench/Workbench.test.tsx
git commit -m "feat(web): add per-row History deep-link to Workbench"
```

---

## Final verification (after all tasks)

- `cd apps/web && npm test && npm run build && npm run lint` — full frontend suite green, build + lint clean.
- **Live Docker verification** (rebuild web; bff unchanged so no rebuild needed, but the stack must be up — bff :8001, web :8089): at `http://localhost:8089`, in the Workbench, approve an approvable recommendation → click that row's "History" link → confirm it lands on the Part Drill-Down `#history` section and the write appears in the timeline → click "Roll back last change" → enter a reason → Confirm → confirm a new `rolled_back` entry appears and the timeline refetched. Also hit `GET http://localhost:8089/v1/tenants/acme/history?pn=<PN>&location=<LOC>` directly to confirm the same-origin proxy passes it through.
- Update trackers per repo convention: `CLAUDE.md` (apps/web now has writeback history + rollback — Wave 2 of 4), `ROADMAP.md`, `TASKS.md`, `.superpowers/sdd/progress.md`. Do NOT touch the (now-retired) `apps/planner-ui` docs.
