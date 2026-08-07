import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  PlanningRerunConfig,
  PlanningRunView,
} from "@/lib/api/planningRuns";

const api = vi.hoisted(() => ({
  getPlanningRunRerunConfig: vi.fn(),
}));

vi.mock("@/lib/api/planningRuns", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("@/lib/api/planningRuns")>();
  return {
    ...original,
    getPlanningRunRerunConfig: api.getPlanningRunRerunConfig,
  };
});

import { PlanningRunForm } from "@/features/portfolio/PlanningRunForm";

const PARENT_ID = "11111111-1111-1111-1111-111111111111";

const parentRun = {
  run_id: PARENT_ID,
  status: "completed",
} as PlanningRunView;

function rerunConfig(
  repairAssumptionChangeAvailable = false,
): PlanningRerunConfig {
  return {
    contract_version: "planning-rerun-config.v1",
    parent_run_id: PARENT_ID,
    scope_kind: "explicit",
    keys: [{ pn: "HYD-PUMP-001", location: "YYZ" }],
    budget: "5000",
    horizon_days: 60,
    currency: "USD",
    objective_weights: {
      shortage_reduction_weight: "2",
      aog_risk_reduction_weight: "3",
      holding_cost_penalty_weight: "0.02",
      ordering_cost_penalty_weight: "0.01",
      criticality_weights: {
        "1": "7",
        "2": "4",
        "3": "2",
        "4": "1",
        "5": "1",
      },
    },
    mandatory_floors: {
      "HYD-PUMP-001@YYZ": [
        {
          floor_id: "tier-3-service",
          source: "tenant-policy-v7",
          min_service_level: "0.9",
          max_aog_risk: "0.2",
          detail: "Protect service.",
        },
      ],
    },
    time_limit_seconds: 15,
    source_generation_hash: "planning_generation_parent",
    parent_model_profile: {
      tenant_policy_version: "policy-v1",
      forecast_version: "forecast-v1",
      repair_model_version: "repair-return-v1",
      candidate_planner_version: "candidate-v1",
      optimizer_version: "optimizer-v1",
    },
    current_trusted_model_profile: {
      tenant_policy_version: "policy-v1",
      forecast_version: "forecast-v1",
      repair_model_version: repairAssumptionChangeAvailable
        ? "repair-return-v2"
        : "repair-return-v1",
      candidate_planner_version: "candidate-v1",
    },
    repair_assumption_change_available: repairAssumptionChangeAvailable,
    repair_assumption_mode: "current_trusted",
  };
}

function renderForm(
  onSubmit = vi.fn(),
  terminalRuns: PlanningRunView[] = [],
) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={client}>
      <PlanningRunForm
        tenant="acme"
        terminalRuns={terminalRuns}
        isPending={false}
        error={null}
        onSubmit={onSubmit}
      />
    </QueryClientProvider>,
  );
  return onSubmit;
}

describe("PlanningRunForm", () => {
  beforeEach(() => {
    api.getPlanningRunRerunConfig.mockReset();
  });

  it("defaults to the authoritative full portfolio without browser keys", async () => {
    const user = userEvent.setup();
    const onSubmit = renderForm();

    await user.click(
      screen.getByRole("button", { name: /submit advisory plan/i }),
    );

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        scope_kind: "all_eligible",
        keys: [],
        time_limit_seconds: 30,
      }),
    );
    expect(screen.queryByLabelText(/explicit keys/i)).not.toBeInTheDocument();
    expect(
      screen.getByRole("group", { name: /mandatory floors/i }),
    ).toBeInTheDocument();
  });

  it("canonicalizes an explicit preview and enforces the 600-second ceiling", async () => {
    const user = userEvent.setup();
    const onSubmit = renderForm();
    await user.click(
      screen.getByRole("radio", { name: /explicit preview/i }),
    );
    await user.type(
      screen.getByLabelText(/explicit keys/i),
      "VALVE-2@YYZ\nPUMP-1@MIA",
    );
    await user.clear(screen.getByLabelText(/solver time limit/i));
    await user.type(screen.getByLabelText(/solver time limit/i), "601");
    await user.click(
      screen.getByRole("button", { name: /submit advisory plan/i }),
    );

    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/1 and 600 seconds/i);

    await user.clear(screen.getByLabelText(/solver time limit/i));
    await user.type(screen.getByLabelText(/solver time limit/i), "600");
    await user.click(
      screen.getByRole("button", { name: /submit advisory plan/i }),
    );

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        scope_kind: "explicit",
        keys: [
          { pn: "PUMP-1", location: "MIA" },
          { pn: "VALVE-2", location: "YYZ" },
        ],
        time_limit_seconds: 600,
      }),
    );
  });

  it("prefills bounded saved inputs and blocks an unchanged rerun", async () => {
    api.getPlanningRunRerunConfig.mockResolvedValue(rerunConfig());
    const user = userEvent.setup();
    const onSubmit = renderForm(vi.fn(), [parentRun]);

    await user.selectOptions(
      screen.getByLabelText(/parent planning run/i),
      PARENT_ID,
    );

    await waitFor(() => {
      expect(screen.getByLabelText(/acquisition budget/i)).toHaveValue(
        "5000",
      );
    });
    expect(screen.getByLabelText(/explicit keys/i)).toHaveValue(
      "HYD-PUMP-001@YYZ",
    );
    expect(
      screen.getByText(/current trusted repair model matches/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/7×, 4×, 2×, 1×, 1×/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /submit advisory plan/i }),
    ).toBeDisabled();

    await user.clear(screen.getByLabelText(/acquisition budget/i));
    await user.type(screen.getByLabelText(/acquisition budget/i), "6000");
    await user.click(
      screen.getByRole("button", { name: /submit advisory plan/i }),
    );

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        parent_run_id: PARENT_ID,
        budget: "6000",
        keys: [{ pn: "HYD-PUMP-001", location: "YYZ" }],
        mandatory_floors: {
          "HYD-PUMP-001@YYZ": [
            expect.objectContaining({
              floor_id: "tier-3-service",
              min_service_level: "0.9",
              max_aog_risk: "0.2",
            }),
          ],
        },
        objective_weights: expect.objectContaining({
          criticality_weights: {
            "1": "7",
            "2": "4",
            "3": "2",
            "4": "1",
            "5": "1",
          },
        }),
      }),
    );
  });

  it("shows the parent/current repair diff and requires current trusted assumptions", async () => {
    api.getPlanningRunRerunConfig.mockResolvedValue(rerunConfig(true));
    const user = userEvent.setup();
    const onSubmit = renderForm(vi.fn(), [parentRun]);

    await user.selectOptions(
      screen.getByLabelText(/parent planning run/i),
      PARENT_ID,
    );

    const trustedRepair = await screen.findByRole("checkbox", {
      name: /use current trusted repair assumptions/i,
    });
    expect(screen.getByText("repair-return-v1")).toBeInTheDocument();
    expect(screen.getByText("repair-return-v2")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /submit advisory plan/i }),
    ).toBeDisabled();

    await user.click(trustedRepair);
    await user.click(
      screen.getByRole("button", { name: /submit advisory plan/i }),
    );

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        parent_run_id: PARENT_ID,
        budget: "5000",
      }),
    );
    expect(JSON.stringify(onSubmit.mock.calls[0][0])).not.toContain(
      "repair-return-v1",
    );
    expect(JSON.stringify(onSubmit.mock.calls[0][0])).not.toContain(
      "repair-return-v2",
    );
  });
});
