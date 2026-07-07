# apps/web Reports / Business Value Report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Reports view to `apps/web` that renders the Business Value Report (BVR) as a document, over the existing BFF `/reports/bvr` endpoints.

**Architecture:** Reuse the existing BFF routes (no backend change). Add BVR TS types + a `getBvr` client method + a `bvrDocumentUrl` builder + a `useBvr` hook, pure `reportView` label/format helpers, a `Reports` view component (7 sections, rendered via `<QueryState>`), and wire a `/reports` route + "Reports" nav item into `App.tsx`.

**Tech Stack:** React 18 + TypeScript + TanStack Query + Vitest + Testing Library (`apps/web`), over the existing FastAPI BFF.

## Global Constraints

- Do NOT touch the (now-retired) `apps/planner-ui` or the BFF — the `/reports/bvr*` routes already exist and are unchanged.
- Do NOT implement Wave 4 (dark/light theme).
- Render the BVR as a **report document**: NO `Metric` / `ProvChip` / `withProvenance` / `MetricValue` in the Reports view. The methodology section is the report's provenance disclosure (a deliberate, documented boundary — same as Wave 2's history rows). The view's test asserts no `ProvChip` is rendered.
- Currency amounts (`amount`, `total_projected*`, `open_pipeline_value`, `estimated_cost_impact`) are Decimal-serialized **strings** from the BFF — display with a `$` prefix. Do NOT parse to a float / run through `Intl.NumberFormat` (avoids the float-precision bug class the earlier UX audit found).
- `ProjectedComponent.name` is the raw snake_case key (verified live: `"holding_cost_delta"`). Map it to a human label via a display-name map with a title-cased fallback — never render `name` raw.
- Rates (`approval_rate`, `override_rate`, `posture_rate`, `target_fill_rate`) are 0–1 numbers — format as `(rate * 100).toFixed(1)` + `%`.
- Document links are real `<a href>` (browser navigation, same rationale as Wave 1's CSV export — sidesteps the standalone-dev CORS gap). HTML opens in a new tab; PDF's `Content-Disposition` drives the download. Both endpoints are live-verified 200 (the deployed BFF image has the `pdf` extra).
- Frontend commands (run from `apps/web`): tests `npm test -- <file>`; typecheck+build `npm run build`; lint `npm run lint` (2 pre-existing shadcn/ui `react-refresh` warnings on badge.tsx/button.tsx are acceptable).
- Test-hygiene: any `afterEach` that spies on `bffClient` must use `vi.restoreAllMocks()` (not only `vi.unstubAllGlobals()`) so spies + call history don't leak across tests (Wave 2 PR-review lesson).
- Standing (no work this wave): keep `apps/web` embeddable in eMRO later — HashRouter already in use.

---

### Task 1: Data layer — BVR types + client methods

**Files:**
- Modify: `apps/web/src/lib/api/types.ts` (add the BVR types)
- Modify: `apps/web/src/lib/api/client.ts` (add `getBvr` + `bvrDocumentUrl`)
- Test: `apps/web/src/lib/api/client.test.ts` (append a `describe`)

**Interfaces:**
- Consumes: `request<T>`, `BASE_URL`, `DEFAULT_TENANT` (client.ts).
- Produces: types `ProjectedComponent`, `BvrSavings`, `TierPosture`, `BvrGovernance`, `BvrReport`; methods `bffClient.getBvr(tenant?) => Promise<BvrReport>` and `bffClient.bvrDocumentUrl(tenant?, kind: "html" | "pdf") => string`.

- [ ] **Step 1: Write the failing tests**

Append to `apps/web/src/lib/api/client.test.ts` (add `BvrReport` to the type import from `@/lib/api/types`):

```typescript
const sampleBvr: BvrReport = {
  schema_version: "1.1.0",
  tenant_id: "acme",
  period: { extract_date: "2026-04-01", decision_window_start: null, decision_window_end: null, generated_at: "2026-07-06T00:00:00Z", label: "As of 2026-04-01" },
  executive_summary: { total_projected: "1250.00", changes_applied: 3, changes_shadowed: 1, keys_under_management: 57605, open_pipeline_value: "42000.00", service_headline: "0/5 tiers at target posture" },
  savings: {
    holding_cost_delta: { name: "holding_cost_delta", amount: "-0.06", formula: "Δ carrying cost", inputs: {}, assumptions: [] },
    ordering_cost_delta: { name: "ordering_cost_delta", amount: "0.00", formula: "Δ ordering cost", inputs: {}, assumptions: [] },
    stockout_risk_delta: { name: "stockout_risk_delta", amount: "0.00", formula: "Δ stockout risk", inputs: {}, assumptions: [] },
    total_projected_applied: "1250.00", total_projected_shadowed: "0.00", total_projected: "1250.00",
    changes_total: 4, changes_valued: 3, assumption_rates: { holding: 0.2 },
  },
  service_posture: { tiers: [], note: "Posture note" },
  governance: {
    recommendations_total: 4, pending: 2, approved: 1, rejected: 1, deferred: 0,
    approval_rate: 0.5, override_rate: 0.25, writes_written: 1, writes_shadowed: 0,
    writes_failed: 0, writes_deferred_open_order: 0, rollbacks: 0, tier_mix: { "1": 2 }, kill_switch_engaged: false,
  },
  forward_look: { open_pipeline_value: "42000.00", projected_demand_horizon: 90, top_opportunities: [{ pn: "P1", location: "YYC", type: "purchase", estimated_cost_impact: "8400.00" }] },
  methodology: { formulas: ["holding = ..."], assumption_rates: { holding: 0.2 }, ledger_entries: 1, recommendations: 4, keys: 57605, keys_total_portfolio: 58899, input_snapshot_hashes: ["abc"], input_snapshot_hash_count: 1, agent_version: "agent-spine-v1", generated_by: "bvr" },
};

describe("bffClient.getBvr", () => {
  afterEach(() => vi.restoreAllMocks());

  it("fetches the BVR from the tenant-scoped route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleBvr) });
    vi.stubGlobal("fetch", fetchMock);
    const result = await bffClient.getBvr("acme");
    expect(fetchMock).toHaveBeenCalledWith(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/reports/bvr`,
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(result.schema_version).toBe("1.1.0");
    expect(result.savings.holding_cost_delta.name).toBe("holding_cost_delta");
  });

  it("defaults to the acme tenant", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(sampleBvr) });
    vi.stubGlobal("fetch", fetchMock);
    await bffClient.getBvr();
    expect(fetchMock).toHaveBeenCalledWith(`${DEFAULT_BFF_URL}/v1/tenants/acme/reports/bvr`, expect.anything());
  });

  it("throws an ApiError on a non-OK response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "Server Error", json: () => Promise.resolve({ detail: "failed to build BVR report" }) }));
    await expect(bffClient.getBvr("acme")).rejects.toThrow(ApiError);
  });
});

describe("bffClient.bvrDocumentUrl", () => {
  it("builds the html and pdf document URLs", () => {
    expect(bffClient.bvrDocumentUrl("acme", "html")).toBe(`${DEFAULT_BFF_URL}/v1/tenants/acme/reports/bvr.html`);
    expect(bffClient.bvrDocumentUrl("acme", "pdf")).toBe(`${DEFAULT_BFF_URL}/v1/tenants/acme/reports/bvr.pdf`);
  });

  it("defaults to the acme tenant", () => {
    expect(bffClient.bvrDocumentUrl(undefined, "html")).toContain("/v1/tenants/acme/reports/bvr.html");
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd apps/web && npm test -- client.test`
Expected: FAIL — `getBvr`/`bvrDocumentUrl` not on `bffClient`; `BvrReport` type missing (compile error).

- [ ] **Step 3: Add the types**

In `apps/web/src/lib/api/types.ts`, append the BVR types (copied verbatim from the identical, already-correct mirror in `apps/planner-ui/src/api/types.ts:289-378` — same pydantic contract):

```typescript
// Business Value Report (BVR) — TS mirrors of trax_io_spine.bvr models.
export interface ProjectedComponent {
  name: string;
  amount: string; // Decimal serialized as string by the BFF
  formula: string;
  inputs: Record<string, number>;
  assumptions: string[];
}

export interface BvrSavings {
  holding_cost_delta: ProjectedComponent;
  ordering_cost_delta: ProjectedComponent;
  stockout_risk_delta: ProjectedComponent;
  total_projected_applied: string;
  total_projected_shadowed: string;
  total_projected: string;
  changes_total: number;
  changes_valued: number;
  assumption_rates: Record<string, number>;
}

export interface TierPosture {
  tier: number;
  target_fill_rate: number;
  keys: number;
  keys_at_posture: number;
  posture_rate: number;
}

export interface BvrGovernance {
  recommendations_total: number;
  pending: number;
  approved: number;
  rejected: number;
  deferred: number;
  approval_rate: number;
  override_rate: number;
  writes_written: number;
  writes_shadowed: number;
  writes_failed: number;
  writes_deferred_open_order: number;
  rollbacks: number;
  tier_mix: Record<string, number>;
  kill_switch_engaged: boolean;
}

export interface BvrReport {
  schema_version: string;
  tenant_id: string;
  period: {
    extract_date: string | null;
    decision_window_start: string | null;
    decision_window_end: string | null;
    generated_at: string;
    label: string;
  };
  executive_summary: {
    total_projected: string;
    changes_applied: number;
    changes_shadowed: number;
    keys_under_management: number;
    open_pipeline_value: string;
    service_headline: string;
  };
  savings: BvrSavings;
  service_posture: { tiers: TierPosture[]; note: string };
  governance: BvrGovernance;
  forward_look: {
    open_pipeline_value: string;
    projected_demand_horizon: number;
    top_opportunities: {
      pn: string;
      location: string;
      type: string;
      estimated_cost_impact: string;
    }[];
  };
  methodology: {
    formulas: string[];
    assumption_rates: Record<string, number>;
    ledger_entries: number;
    recommendations: number;
    keys: number;
    keys_total_portfolio: number;
    input_snapshot_hashes: string[];
    input_snapshot_hash_count: number;
    agent_version: string;
    generated_by: string;
  };
}
```

- [ ] **Step 4: Add the client methods**

In `apps/web/src/lib/api/client.ts`, add `BvrReport` to the type import block, then add these to the `bffClient` object (e.g. after `getFeeds`):

```typescript
  getBvr(tenant: string = DEFAULT_TENANT): Promise<BvrReport> {
    return request<BvrReport>(`/v1/tenants/${encodeURIComponent(tenant)}/reports/bvr`);
  },

  /**
   * URL to a BVR document (HTML or PDF), consumed as an `<a href>` — a browser
   * navigation triggers the render/download via the BFF's Content-Disposition,
   * not `fetch()` (same pattern/rationale as `recommendationsExportUrl`).
   */
  bvrDocumentUrl(tenant: string = DEFAULT_TENANT, kind: "html" | "pdf"): string {
    return `${BASE_URL}/v1/tenants/${encodeURIComponent(tenant)}/reports/bvr.${kind}`;
  },
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd apps/web && npm test -- client.test`
Expected: the new tests pass, all existing client tests still green.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/lib/api/types.ts apps/web/src/lib/api/client.ts apps/web/src/lib/api/client.test.ts
git commit -m "feat(web): add BVR types + getBvr/bvrDocumentUrl client methods"
```

---

### Task 2: useBvr hook

**Files:**
- Create: `apps/web/src/lib/api/useBvr.ts`
- Test: `apps/web/src/lib/api/useBvr.test.tsx`

**Interfaces:**
- Consumes: `bffClient.getBvr` (Task 1), `BvrReport` type, TanStack `useQuery`.
- Produces: `bvrQueryKey(tenant)` = `["bvr", tenant]`; `useBvr(tenant?)` = `useQuery<BvrReport>` with `staleTime: 60_000`.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/lib/api/useBvr.test.tsx`:

```typescript
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- useBvr`
Expected: FAIL — `useBvr` module doesn't exist.

- [ ] **Step 3: Implement the hook**

Create `apps/web/src/lib/api/useBvr.ts`:

```typescript
import { useQuery } from "@tanstack/react-query";
import { bffClient, DEFAULT_TENANT } from "@/lib/api/client";
import type { BvrReport } from "@/lib/api/types";

export function bvrQueryKey(tenant: string) {
  return ["bvr", tenant] as const;
}

/** The tenant's Business Value Report. Read-heavy snapshot — staleTime 60s. */
export function useBvr(tenant: string = DEFAULT_TENANT) {
  return useQuery<BvrReport>({
    queryKey: bvrQueryKey(tenant),
    queryFn: () => bffClient.getBvr(tenant),
    staleTime: 60_000,
  });
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- useBvr`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/api/useBvr.ts apps/web/src/lib/api/useBvr.test.tsx
git commit -m "feat(web): add useBvr hook"
```

---

### Task 3: Pure reportView helpers

**Files:**
- Create: `apps/web/src/features/reports/reportView.ts`
- Test: `apps/web/src/features/reports/reportView.test.ts`

**Interfaces:**
- Produces: `savingsComponentLabel(name: string): string` (display-name map + title-cased fallback); `formatRatePct(rate: number): string` (`"50.0%"`); `formatAmount(amount: string): string` (`"$1250.00"`).

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/reports/reportView.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { formatAmount, formatRatePct, savingsComponentLabel } from "@/features/reports/reportView";

describe("savingsComponentLabel", () => {
  it("maps the known savings component keys to human labels", () => {
    expect(savingsComponentLabel("holding_cost_delta")).toBe("Holding cost");
    expect(savingsComponentLabel("ordering_cost_delta")).toBe("Ordering cost");
    expect(savingsComponentLabel("stockout_risk_delta")).toBe("Stockout risk");
  });

  it("title-cases an unknown snake_case key as a fallback (never renders it raw)", () => {
    expect(savingsComponentLabel("some_new_component")).toBe("Some New Component");
  });
});

describe("formatRatePct", () => {
  it("renders a 0-1 rate as a one-decimal percentage", () => {
    expect(formatRatePct(0.5)).toBe("50.0%");
    expect(formatRatePct(0.25)).toBe("25.0%");
    expect(formatRatePct(0)).toBe("0.0%");
  });
});

describe("formatAmount", () => {
  it("prefixes the server-formatted Decimal string with $ (no float parsing)", () => {
    expect(formatAmount("1250.00")).toBe("$1250.00");
    expect(formatAmount("-0.06")).toBe("$-0.06");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- reportView`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the helpers**

Create `apps/web/src/features/reports/reportView.ts`:

```typescript
const SAVINGS_COMPONENT_LABELS: Record<string, string> = {
  holding_cost_delta: "Holding cost",
  ordering_cost_delta: "Ordering cost",
  stockout_risk_delta: "Stockout risk",
};

/**
 * Human label for a savings `ProjectedComponent.name` (raw snake_case on the
 * wire). Falls back to title-casing the key so an unknown component is never
 * rendered as a raw snake_case string to users.
 */
export function savingsComponentLabel(name: string): string {
  return (
    SAVINGS_COMPONENT_LABELS[name] ??
    name.split("_").map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(" ")
  );
}

/** A 0-1 rate as a one-decimal percentage, e.g. 0.5 -> "50.0%". */
export function formatRatePct(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/**
 * A BFF Decimal-string amount displayed with a `$` prefix. NOT parsed to a
 * float — the string is already correctly formatted server-side (avoids the
 * float-precision issue the UX audit flagged in the integer formatter).
 */
export function formatAmount(amount: string): string {
  return `$${amount}`;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- reportView`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/reports/reportView.ts apps/web/src/features/reports/reportView.test.ts
git commit -m "feat(web): add pure reportView helpers (label map, rate/amount formatters)"
```

---

### Task 4: Reports view component

**Files:**
- Create: `apps/web/src/features/reports/Reports.tsx`
- Test: `apps/web/src/features/reports/Reports.test.tsx`

**Interfaces:**
- Consumes: `useBvr` (Task 2); `savingsComponentLabel`/`formatRatePct`/`formatAmount` (Task 3); `bffClient.bvrDocumentUrl` (Task 1); `<QueryLoading>`/`<QueryError>` (`components/QueryState.tsx`); `Card`/`CardHeader`/`CardTitle`/`CardContent`, `Badge`; `Link` (react-router-dom); `DEFAULT_TENANT`.
- Produces: the `Reports` component (default tenant; no props). Renders 7 sections; no `Metric`/`ProvChip`.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/features/reports/Reports.test.tsx` (mirrors the other view tests — a `QueryClientProvider` + `MemoryRouter`, spy on `bffClient`):

```typescript
import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Reports } from "@/features/reports/Reports";
import { bffClient } from "@/lib/api/client";
import type { BvrReport } from "@/lib/api/types";

const sampleBvr: BvrReport = {
  schema_version: "1.1.0",
  tenant_id: "acme",
  period: { extract_date: "2026-04-01", decision_window_start: null, decision_window_end: null, generated_at: "2026-07-06T00:00:00Z", label: "As of 2026-04-01" },
  executive_summary: { total_projected: "1250.00", changes_applied: 3, changes_shadowed: 1, keys_under_management: 57605, open_pipeline_value: "42000.00", service_headline: "0/5 tiers at target posture" },
  savings: {
    holding_cost_delta: { name: "holding_cost_delta", amount: "-0.06", formula: "Δ carrying cost", inputs: {}, assumptions: [] },
    ordering_cost_delta: { name: "ordering_cost_delta", amount: "0.00", formula: "Δ ordering cost", inputs: {}, assumptions: [] },
    stockout_risk_delta: { name: "stockout_risk_delta", amount: "0.00", formula: "Δ stockout risk", inputs: {}, assumptions: [] },
    total_projected_applied: "1250.00", total_projected_shadowed: "0.00", total_projected: "1250.00",
    changes_total: 4, changes_valued: 3, assumption_rates: { holding: 0.2 },
  },
  service_posture: { tiers: [], note: "Posture note" },
  governance: {
    recommendations_total: 4, pending: 2, approved: 1, rejected: 1, deferred: 0,
    approval_rate: 0.5, override_rate: 0.25, writes_written: 1, writes_shadowed: 0,
    writes_failed: 0, writes_deferred_open_order: 0, rollbacks: 0, tier_mix: { "1": 2 }, kill_switch_engaged: false,
  },
  forward_look: { open_pipeline_value: "42000.00", projected_demand_horizon: 90, top_opportunities: [{ pn: "P1", location: "YYC", type: "purchase", estimated_cost_impact: "8400.00" }] },
  methodology: { formulas: ["holding = ..."], assumption_rates: { holding: 0.2 }, ledger_entries: 1, recommendations: 4, keys: 57605, keys_total_portfolio: 58899, input_snapshot_hashes: ["abc"], input_snapshot_hash_count: 1, agent_version: "agent-spine-v1", generated_by: "bvr" },
};

function renderReports(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Reports", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the report header, exec summary, mapped savings labels, governance, forward look, and methodology", async () => {
    vi.spyOn(bffClient, "getBvr").mockResolvedValue(sampleBvr);
    renderReports(<Reports />);

    expect(await screen.findByRole("heading", { name: /business value report/i })).toBeInTheDocument();
    expect(screen.getByText("As of 2026-04-01")).toBeInTheDocument();
    // savings labels are HUMAN, not raw snake_case
    expect(screen.getByText("Holding cost")).toBeInTheDocument();
    expect(screen.queryByText("holding_cost_delta")).not.toBeInTheDocument();
    // governance rate formatted
    expect(screen.getByText(/50\.0%/)).toBeInTheDocument();
    // forward-look opportunity links to Part Drill-Down
    const oppLink = screen.getByRole("link", { name: /P1/ });
    expect(oppLink).toHaveAttribute("href", "/parts/P1/YYC");
    // methodology's keys-of-portfolio disclosure
    expect(screen.getByText(/57,?605.*58,?899|57605 of 58899/)).toBeInTheDocument();
  });

  it("renders the HTML and PDF document links", async () => {
    vi.spyOn(bffClient, "getBvr").mockResolvedValue(sampleBvr);
    renderReports(<Reports />);
    const html = await screen.findByRole("link", { name: /printable report/i });
    const pdf = screen.getByRole("link", { name: /pdf/i });
    expect(html.getAttribute("href")).toContain("/reports/bvr.html");
    expect(pdf.getAttribute("href")).toContain("/reports/bvr.pdf");
  });

  it("does NOT render provenance chips (report-document boundary)", async () => {
    vi.spyOn(bffClient, "getBvr").mockResolvedValue(sampleBvr);
    renderReports(<Reports />);
    await screen.findByRole("heading", { name: /business value report/i });
    // ProvChip renders with data-testid="prov-chip" (verified: ProvChip.tsx:43,
    // and Metric.test.tsx asserts its presence the same way). The report-document
    // boundary means NONE should appear in the Reports view.
    expect(screen.queryByTestId("prov-chip")).not.toBeInTheDocument();
  });

  it("shows loading then error+Retry when the query fails", async () => {
    vi.spyOn(bffClient, "getBvr").mockRejectedValue(new Error("boom"));
    renderReports(<Reports />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/failed to load/i);
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });
});
```

(The `queryByTestId("prov-chip")` assertion is verified real — `ProvChip.tsx:43` renders `data-testid="prov-chip"`, and `Metric.test.tsx:28` asserts its presence the same way.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- Reports.test`
Expected: FAIL — `Reports` component missing.

- [ ] **Step 3: Implement the component**

Create `apps/web/src/features/reports/Reports.tsx`. Use `apps/web`'s existing Tailwind/`Card` vocabulary (match `DataConnections.tsx`/`PartDrillDown.tsx`). Confirm the exact `QueryLoading`/`QueryError` import path + props (`components/QueryState.tsx`) and `Card`/`Badge` paths before writing.

```tsx
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { useBvr } from "@/lib/api/useBvr";
import { bffClient, DEFAULT_TENANT } from "@/lib/api/client";
import { formatAmount, formatRatePct, savingsComponentLabel } from "@/features/reports/reportView";
import type { BvrSavings } from "@/lib/api/types";

const SAVINGS_KEYS: (keyof Pick<BvrSavings, "holding_cost_delta" | "ordering_cost_delta" | "stockout_risk_delta">)[] = [
  "holding_cost_delta",
  "ordering_cost_delta",
  "stockout_risk_delta",
];

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-control border border-line p-3">
      <div className="text-lg font-semibold text-ink">{value}</div>
      <div className="text-xs text-ink-2">{label}</div>
    </div>
  );
}

export function Reports() {
  const tenant = DEFAULT_TENANT;
  const { data, isPending, isError, error, refetch } = useBvr(tenant);

  if (isPending) return <QueryLoading label="Loading Business Value Report…" />;
  if (isError) return <QueryError label="Failed to load Business Value Report" error={error} onRetry={() => refetch()} />;

  const { period, executive_summary: exec, savings, governance, forward_look, methodology } = data;

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex flex-col gap-2 border-b border-line pb-4">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold text-ink">Business Value Report</h1>
          <Badge variant="warn" title="Figures are projected against the pre-agent baseline">Projected vs pre-agent baseline</Badge>
        </div>
        <p className="text-sm text-ink-2">
          {period.label} · generated {new Date(period.generated_at).toISOString().slice(0, 10)} · schema {data.schema_version} · {methodology.agent_version}
        </p>
      </header>

      <section aria-label="Executive summary" className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Tile label="Total projected" value={formatAmount(exec.total_projected)} />
        <Tile label="Changes applied" value={String(exec.changes_applied)} />
        <Tile label="Changes shadowed" value={String(exec.changes_shadowed)} />
        <Tile label="Keys under management" value={exec.keys_under_management.toLocaleString("en-US")} />
        <Tile label="Open pipeline" value={formatAmount(exec.open_pipeline_value)} />
        <Tile label="Service" value={exec.service_headline} />
      </section>

      <Card>
        <CardHeader><CardTitle>Savings (projected)</CardTitle></CardHeader>
        <CardContent>
          <ul className="flex flex-col gap-2">
            {SAVINGS_KEYS.map((k) => {
              const c = savings[k];
              return (
                <li key={k} className="flex flex-wrap items-baseline justify-between gap-2 border-t border-line pt-2 text-sm">
                  <span className="text-ink">{savingsComponentLabel(c.name)}</span>
                  <span className="font-medium tabular-nums text-ink">{formatAmount(c.amount)}</span>
                  <span className="w-full text-xs text-ink-3">{c.formula}</span>
                </li>
              );
            })}
          </ul>
          <p className="mt-3 text-xs text-ink-2">
            Applied {formatAmount(savings.total_projected_applied)} · shadowed {formatAmount(savings.total_projected_shadowed)} · total {formatAmount(savings.total_projected)} · {savings.changes_valued}/{savings.changes_total} changes valued
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Governance</CardTitle></CardHeader>
        <CardContent className="text-sm text-ink-2">
          {governance.recommendations_total} recommendations · approval rate {formatRatePct(governance.approval_rate)} · override rate {formatRatePct(governance.override_rate)} · {governance.writes_written} written · {governance.rollbacks} rollbacks{" "}
          <Badge variant={governance.kill_switch_engaged ? "bad" : "good"}>
            Kill switch {governance.kill_switch_engaged ? "engaged" : "off"}
          </Badge>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Forward look</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          <p className="text-ink-2">
            Open pipeline {formatAmount(forward_look.open_pipeline_value)} · demand horizon {forward_look.projected_demand_horizon} days
          </p>
          {forward_look.top_opportunities.length === 0 ? (
            <p className="text-ink-2">No open opportunities.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {forward_look.top_opportunities.map((o) => (
                <li key={`${o.pn}/${o.location}`} className="flex flex-wrap items-baseline gap-2">
                  <Link
                    to={`/parts/${encodeURIComponent(o.pn)}/${encodeURIComponent(o.location)}`}
                    className="font-medium text-brand hover:underline"
                  >
                    {o.pn}
                  </Link>
                  <span className="text-ink-2">{o.location} · {o.type}</span>
                  <span className="tabular-nums text-ink">{formatAmount(o.estimated_cost_impact)}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Methodology</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-2 text-xs text-ink-2">
          <p>
            Valued {methodology.keys.toLocaleString("en-US")} of {methodology.keys_total_portfolio.toLocaleString("en-US")} portfolio keys · {methodology.ledger_entries} ledger entries · {methodology.recommendations} recommendations · {methodology.input_snapshot_hash_count} input snapshots · {methodology.agent_version} · {methodology.generated_by}
          </p>
          <ul className="list-disc pl-5">
            {methodology.formulas.map((f) => (<li key={f}>{f}</li>))}
          </ul>
        </CardContent>
      </Card>

      <p className="flex gap-4 text-sm">
        <a href={bffClient.bvrDocumentUrl(tenant, "html")} target="_blank" rel="noreferrer" className="text-brand hover:underline">
          Open printable report
        </a>
        <a href={bffClient.bvrDocumentUrl(tenant, "pdf")} className="text-brand hover:underline">
          Download PDF
        </a>
      </p>
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- Reports.test`
Expected: all pass. (If the methodology keys-disclosure assertion's regex doesn't match your exact copy, align the test regex to the rendered text — keep the assertion meaningful, i.e. both `57,605` and `58,899` appear.)

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/reports/Reports.tsx apps/web/src/features/reports/Reports.test.tsx
git commit -m "feat(web): add Reports view rendering the BVR as a document (no ProvChip)"
```

---

### Task 5: Wire the Reports route + nav item into App.tsx

**Files:**
- Modify: `apps/web/src/App.tsx` (add to `NAV_ITEMS` + `Routes`)
- Modify: `apps/web/src/App.test.tsx` (add "Reports" to the nav-label list + a route-resolves assertion)

**Interfaces:**
- Consumes: `Reports` (Task 4).
- Produces: `/reports` route + "Reports" nav item. No new exports.

- [ ] **Step 1: Write the failing test**

In `apps/web/src/App.test.tsx`:
1. Add `"Reports"` to the hardcoded nav-label list in the "renders the header and every nav item" test (the array at lines ~47-56, currently the 6 existing labels).
2. Add a new test that deep-linking to `#/reports` marks the Reports nav item active (mirrors the existing "deep-links directly to a non-root route" test):

```typescript
it("deep-links to the Reports route via the URL hash", async () => {
  window.location.hash = "#/reports";
  stubPendingFetch();

  renderApp();

  await waitFor(() =>
    expect(screen.getByRole("link", { name: "Reports" })).toHaveAttribute("aria-current", "page"),
  );
});
```

(The existing `stubPendingFetch` never resolves, so the mounted `Reports` view stays in its `isPending` state — no BVR payload needed here; the Reports view's own test covers its data render.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- App.test`
Expected: FAIL — no "Reports" link exists yet (both the extended label-list assertion and the new deep-link test fail).

- [ ] **Step 3: Wire it in**

In `apps/web/src/App.tsx`:
1. Add the import: `import { Reports } from "@/features/reports/Reports";`
2. Add to `NAV_ITEMS` (after the "Data & Connections" entry): `{ to: "/reports", label: "Reports" },`
3. Add to `Routes` (with the other top-level routes, before the `/parts/:pn/:location` route): `<Route path="/reports" element={<Reports />} />`

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- App.test`
Expected: all App tests pass (extended nav list + new deep-link test).

- [ ] **Step 5: Full frontend gate**

Run: `cd apps/web && npm test && npm run build && npm run lint`
Expected: all Vitest green, build clean (0 errors), lint 0 errors (2 pre-existing warnings acceptable).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/App.tsx apps/web/src/App.test.tsx
git commit -m "feat(web): wire /reports route + Reports nav item"
```

---

## Final verification (after all tasks)

- `cd apps/web && npm test && npm run build && npm run lint` — full frontend suite green, build + lint clean.
- **Live Docker verification** (rebuild web; BFF unchanged so no rebuild needed, but the stack must be up — bff :8001, web :8089): at `http://localhost:8089`, click the "Reports" nav item → `/reports` renders the BVR with real network-scale figures (savings labels are human, not `snake_case`; governance rates formatted; methodology shows "N of M portfolio keys"); a forward-look opportunity link lands on the correct Part Drill-Down; "Open printable report" opens the HTML document and "Download PDF" fetches the PDF (both BFF-served). Also confirm no console errors.
- Update trackers per repo convention: `CLAUDE.md` (apps/web now has the Reports/BVR view — Wave 3 of 4), `ROADMAP.md`, `TASKS.md`, `.superpowers/sdd/progress.md`. Do NOT touch the (now-retired) `apps/planner-ui` docs.
