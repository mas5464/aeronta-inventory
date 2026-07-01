import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Scenarios } from "@/features/scenarios/Scenarios";
import type {
  Scenario,
  ScenarioAuditEvent,
  ScenarioParams,
  ScenarioSolveResult,
} from "@/lib/api/types";

function renderWithProviders(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function solveResult(overrides: Partial<ScenarioSolveResult> = {}): ScenarioSolveResult {
  return {
    params: { lead_time_delta_pct: 0, scope: "all", service_level_target: 0.95 },
    current: {
      service_level: 0.95,
      projected_investment: 1_000_000,
      projected_coverage: 0.95,
      on_hand_gap_ratio: 0.8,
      scored_keys: 21215,
    },
    proposed: {
      service_level: 0.95,
      projected_investment: 1_000_000,
      projected_coverage: 0.95,
      on_hand_gap_ratio: 0.8,
      scored_keys: 21215,
    },
    delta_investment: 0,
    delta_coverage: 0,
    frontier: [
      { service_level: 0.9, projected_investment: 900_000, projected_coverage: 0.9 },
      { service_level: 0.95, projected_investment: 1_000_000, projected_coverage: 0.95 },
      { service_level: 0.99, projected_investment: 1_300_000, projected_coverage: 0.99 },
    ],
    skipped_keys: 617,
    total_keys: 21215,
    budget_cap_binds: false,
    ...overrides,
  };
}

function mockFetchRouter(options: {
  solve?: (body: ScenarioParams) => ScenarioSolveResult;
  scenarios?: Scenario[];
  onSave?: () => void;
  onDelete?: () => void;
  onCommit?: () => void;
}) {
  const scenarios = options.scenarios ?? [];

  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.includes("/scenarios/solve")) {
      const body: ScenarioParams = init?.body ? JSON.parse(init.body as string) : {};
      const result = options.solve ? options.solve(body) : solveResult();
      return Promise.resolve({ ok: true, json: () => Promise.resolve(result) });
    }
    if (url.includes("/commit")) {
      options.onCommit?.();
      const event: ScenarioAuditEvent = {
        scenario_id: "scn-1",
        scenario_name: "Test scenario",
        action: "commit",
        at: "2026-07-01T00:00:00Z",
        note: "Scenario committed as the tenant's target plan. No eMRO writeback occurred.",
      };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(event) });
    }
    if (url.match(/\/scenarios\/[^/]+$/) && method === "DELETE") {
      options.onDelete?.();
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ deleted: "scn-1" }) });
    }
    if (url.endsWith("/scenarios") && method === "POST") {
      options.onSave?.();
      const body = init?.body ? JSON.parse(init.body as string) : {};
      const scenario: Scenario = {
        id: "scn-1",
        name: body.name ?? "Untitled",
        params: body.params,
        result: body.result,
        status: "draft",
        created_at: "2026-07-01T00:00:00Z",
        committed_at: null,
      };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(scenario) });
    }
    if (url.endsWith("/scenarios")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(scenarios) });
    }
    return Promise.reject(new Error(`Unhandled request: ${method} ${url}`));
  });
}

describe("Scenarios", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("solves the default scenario on mount and renders the outcome with provenance", async () => {
    vi.stubGlobal("fetch", mockFetchRouter({}));

    renderWithProviders(<Scenarios />);

    await waitFor(() => expect(screen.getByText("$1,000,000")).toBeInTheDocument());
    expect(screen.getAllByTestId("prov-chip").length).toBeGreaterThan(0);
  });

  it("renders the cost-service frontier chart once solved", async () => {
    vi.stubGlobal("fetch", mockFetchRouter({}));

    renderWithProviders(<Scenarios />);

    await waitFor(() =>
      expect(screen.getByRole("img", { name: /cost-service frontier/i })).toBeInTheDocument(),
    );
  });

  it("re-solves (debounced) when the service-level slider changes", async () => {
    const solve = vi.fn((body: ScenarioParams) =>
      solveResult({
        proposed: {
          service_level: body.service_level_target ?? 0.95,
          projected_investment: (body.service_level_target ?? 0.95) > 0.95 ? 1_500_000 : 1_000_000,
          projected_coverage: body.service_level_target ?? 0.95,
          on_hand_gap_ratio: 0.8,
          scored_keys: 21215,
        },
      }),
    );
    vi.stubGlobal("fetch", mockFetchRouter({ solve }));

    renderWithProviders(<Scenarios />);
    await waitFor(() => expect(screen.getByText("$1,000,000")).toBeInTheDocument());

    const slider = screen.getByLabelText("Target service level");
    fireEvent.change(slider, { target: { value: "0.99" } });

    await waitFor(() => expect(screen.getByText("$1,500,000")).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(solve.mock.calls.some(([body]) => body.service_level_target === 0.99)).toBe(true);
  });

  it("shows the skipped-keys honest disclosure", async () => {
    vi.stubGlobal("fetch", mockFetchRouter({}));

    renderWithProviders(<Scenarios />);

    await waitFor(() =>
      expect(screen.getByText(/617 of 21,215 parts network-wide/)).toBeInTheDocument(),
    );
  });

  it("shows a budget-cap-exceeded warning when the solve binds", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetchRouter({ solve: () => solveResult({ budget_cap_binds: true }) }),
    );

    renderWithProviders(<Scenarios />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/exceeds the budget cap/i);
  });

  it("saves a named scenario and shows a save acknowledgement", async () => {
    const onSave = vi.fn();
    vi.stubGlobal("fetch", mockFetchRouter({ onSave }));

    renderWithProviders(<Scenarios />);
    await waitFor(() => expect(screen.getByText("$1,000,000")).toBeInTheDocument());

    const nameInput = screen.getByLabelText("Scenario name");
    await userEvent.type(nameInput, "Baseline 95%");
    await userEvent.click(screen.getByRole("button", { name: "Save scenario" }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("Scenario saved.")).toBeInTheDocument());
  });

  it("renders saved scenarios and commits one via the confirm dialog", async () => {
    const onCommit = vi.fn();
    const saved: Scenario = {
      id: "scn-1",
      name: "Tier 1 to 99%",
      params: { lead_time_delta_pct: 0, scope: "all" },
      result: solveResult(),
      status: "draft",
      created_at: "2026-07-01T00:00:00Z",
      committed_at: null,
    };
    vi.stubGlobal("fetch", mockFetchRouter({ scenarios: [saved], onCommit }));

    renderWithProviders(<Scenarios />);

    await waitFor(() => expect(screen.getByText("Tier 1 to 99%")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Commit" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm commit" }));

    await waitFor(() => expect(onCommit).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByText(/committed and recorded in the audit log/i)).toBeInTheDocument(),
    );
  });

  it("renders an error state when the initial solve fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/scenarios/solve")) {
          return Promise.resolve({
            ok: false,
            status: 500,
            statusText: "Internal Server Error",
            json: () => Promise.resolve({}),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }),
    );

    renderWithProviders(<Scenarios />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
