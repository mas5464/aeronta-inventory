import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PlanningRunDetail } from "@/features/portfolio/PlanningRunDetail";
import type {
  PlanningRunSelectionRecord,
  PlanningRunView,
} from "@/lib/api/planningRuns";

const selectionState = vi.hoisted(() => ({
  items: [] as unknown[],
  total: null as number | null,
  params: null as unknown,
}));

vi.mock("@/lib/api/usePlanningRuns", () => ({
  usePlanningRunSelections: (...args: unknown[]) => {
    selectionState.params = args[2];
    return {
      isPending: false,
      isError: false,
      data: {
        items: selectionState.items,
        total: selectionState.total ?? selectionState.items.length,
        limit: 25,
        offset: 0,
      },
      refetch: vi.fn(),
    };
  },
}));

function objective(
  total: string,
  shortageValue: string,
): PlanningRunSelectionRecord["detail"]["selected"]["objective"] {
  return {
    currency: "USD",
    criticality_weight: "5",
    shortage_reduction: shortageValue,
    aog_risk_reduction: "0.3",
    incremental_holding_cost: "0",
    incremental_ordering_cost: "0",
    shortage_value: shortageValue,
    aog_value: String(Number(total) - Number(shortageValue)),
    holding_penalty: "0",
    ordering_penalty: "0",
    total,
  };
}

function completedSelection(): PlanningRunSelectionRecord {
  const current = {
    candidate_id: "candidate-current",
    label: "Current baseline",
    candidate_kind: "no_change",
    acquisition_cash: "0",
    expected_shortage: "10",
    expected_service_level: "0.6",
    expected_aog_risk: "0.4",
    objective: objective("0", "0"),
    confidence: "0.55",
    feasible: false,
    infeasibility_reasons: [],
    hard_constraint_ids: [],
    mandatory_floor_ids: ["service-floor"],
  };
  const selected = {
    candidate_id: "candidate-selected",
    label: "Buy five",
    candidate_kind: "purchase",
    acquisition_cash: "500",
    expected_shortage: "2",
    expected_service_level: "0.92",
    expected_aog_risk: "0.1",
    objective: objective("8", "6.5"),
    confidence: "0.9",
    feasible: true,
    infeasibility_reasons: [],
    hard_constraint_ids: [],
    mandatory_floor_ids: [],
  };
  const rejected = {
    candidate_id: "candidate-rejected",
    label: "Buy ten",
    candidate_kind: "purchase",
    acquisition_cash: "900",
    expected_shortage: "0",
    expected_service_level: "1",
    expected_aog_risk: "0.05",
    objective: objective("10", "8"),
    confidence: "0.72",
    feasible: false,
    infeasibility_reasons: [],
    hard_constraint_ids: ["vendor-cap"],
    mandatory_floor_ids: [],
  };
  return {
    decision_key: "PN-1@MIA",
    current_candidate_id: current.candidate_id,
    selected_candidate_id: selected.candidate_id,
    selected_is_no_change: false,
    acquisition_cash: selected.acquisition_cash,
    objective: selected.objective.total,
    selection: {
      decision_key: "PN-1@MIA",
      current_candidate_id: current.candidate_id,
      selected_candidate_id: selected.candidate_id,
      selected_is_no_change: false,
      acquisition_cash: selected.acquisition_cash,
      expected_shortage: selected.expected_shortage,
      expected_service_level: selected.expected_service_level,
      expected_aog_risk: selected.expected_aog_risk,
      objective: selected.objective,
      floor_states: [
        {
          floor_id: "service-floor",
          source: "tenant-policy",
          satisfied: true,
          binding: true,
        },
      ],
    },
    detail: {
      decision_key: "PN-1@MIA",
      current,
      selected,
      selected_reason:
        "Selected Buy five with confidence 0.9, supported by planning_trace evidence from snapshot.",
      rejected_alternatives: [
        {
          candidate: rejected,
          reason_code: "hard_constraint",
          reason: "Rejected because hard constraint vendor-cap is not satisfied.",
        },
      ],
    },
  };
}

function run(
  status: PlanningRunView["status"],
  updates: Partial<PlanningRunView> = {},
): PlanningRunView {
  return {
    run_id: "11111111-1111-1111-1111-111111111111",
    planning_fingerprint: `planning_${"a".repeat(64)}`,
    contract_version: "planning.v1",
    parent_run_id: null,
    parent_planning_fingerprint: null,
    parent_source_snapshot_hash: null,
    assumption_diff: [],
    status,
    source_snapshot_hash: "candidate_snapshot_submitted",
    source_generation_hash: "planning_generation_submitted",
    scope: {
      kind: "all_eligible",
      key_count: 58_899,
      preview_keys: ["PN-1@MIA"],
      preview_truncated: true,
    },
    key_count: 58_899,
    budget: "100000",
    horizon_days: 60,
    currency: "USD",
    model_profile: {
      tenant_policy_version: "policy-v1",
      forecast_version: "forecast-v1",
      repair_model_version: "repair-v1",
      candidate_planner_version: "candidate-v1",
      optimizer_version: "optimizer-v1",
      objective_weights: {
        shortage_reduction_weight: "1",
        aog_risk_reduction_weight: "1",
        holding_cost_penalty_weight: "0.01",
        ordering_cost_penalty_weight: "0.01",
        criticality_weights: {
          "1": "5",
          "2": "3",
          "3": "2",
          "4": "1",
          "5": "1",
        },
      },
    },
    advisory_only: true,
    progress_completed: status === "queued" ? 0 : 58_899,
    progress_total: 58_899,
    summary: null,
    infeasibility: null,
    detail: {
      error_code: null,
      guidance: null,
      retryable: null,
      failed_attempt: null,
      last_failed_attempt: null,
    },
    solver: null,
    warnings: {
      total: 0,
      counted_items: 0,
      by_code: [],
      code_list_truncated: false,
    },
    skipped_keys: {
      total: 0,
      counted_items: 0,
      by_code: [],
      code_list_truncated: false,
    },
    submitted_by: "planner-user",
    attempts: status === "queued" ? 0 : 1,
    claimed_at: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-07-28T12:00:00Z",
    updated_at: "2026-07-28T12:00:00Z",
    coverage: null,
    stale: null,
    current_source_snapshot_hash: null,
    current_source_generation_hash: null,
    stale_reason: null,
    ...updates,
  };
}

describe("PlanningRunDetail", () => {
  beforeEach(() => {
    selectionState.items = [];
    selectionState.total = null;
    selectionState.params = null;
  });

  it("renders accessible progress for a full-network active run", () => {
    render(<PlanningRunDetail run={run("queued")} tenant="acme" />);

    expect(
      screen.getByRole("progressbar", { name: /planning run progress/i }),
    ).toHaveAttribute("aria-valuemax", "58899");
    expect(screen.getByText(/58,899 keys · all eligible/i)).toBeInTheDocument();
    expect(screen.getByText(/staleness unavailable/i)).toBeInTheDocument();
  });

  it("does not overclaim optimality when the solver returns a bounded gap", () => {
    render(
      <PlanningRunDetail
        tenant="acme"
        run={run("completed", {
          summary: {
            currency: "USD",
            budget: "100000",
            selected_acquisition_cash: "90000",
            budget_slack: "10000",
            selected_key_count: 58_899,
            no_change_key_count: 50_000,
            selected_objective: "125.5",
            expected_shortage: "40",
            average_service_level: "0.95",
            maximum_aog_risk: "0.1",
          },
          solver: {
            implementation: "scipy",
            implementation_version: "1",
            optimizer_version: "optimizer-v1",
            termination: "not_proven",
            optimality_proven: false,
            objective: "125.5",
            objective_bound: "130",
            relative_gap: "0.035",
            duration_ms: "600000",
            node_count: 42,
            message: "Time limit reached with a feasible incumbent.",
          },
        })}
      />,
    );

    expect(
      screen.getByText(/feasible · not proven optimal/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/not a claim of optimality/i)).toBeInTheDocument();
    expect(
      screen.getByText(/coverage evidence is unavailable for this legacy run/i),
    ).toBeInTheDocument();
  });

  it("renders a textual reconciliation instead of an invalid zero-range meter", () => {
    render(
      <PlanningRunDetail
        tenant="acme"
        run={run("completed", {
          budget: "0",
          summary: {
            currency: "USD",
            budget: "0",
            selected_acquisition_cash: "0",
            budget_slack: "0",
            selected_key_count: 0,
            no_change_key_count: 58_899,
            selected_objective: "0",
            expected_shortage: "0",
            average_service_level: "1",
            maximum_aog_risk: "0",
          },
        })}
      />,
    );

    expect(
      screen.queryByRole("meter", { name: /acquisition budget used/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Zero acquisition budget; selected spend and slack both reconcile to $0.00.",
      ),
    ).toHaveAttribute("role", "status");
  });

  it("renders only safe failure evidence and no actionable selection ledger", () => {
    render(
      <PlanningRunDetail
        tenant="acme"
        run={run("failed", {
          detail: {
            error_code: "planning_worker_interrupted",
            guidance: "Submit a new immutable run after verifying worker health.",
            retryable: false,
            failed_attempt: 3,
            last_failed_attempt: null,
          },
        })}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "planning_worker_interrupted",
    );
    expect(
      screen.getByText(/submit a new immutable run/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/key selections and objective ledger/i),
    ).not.toBeInTheDocument();
  });

  it("keeps missing candidate frontiers in the authoritative denominator", () => {
    render(
      <PlanningRunDetail
        tenant="acme"
        run={run("queued", {
          coverage: {
            scope_key_count: 100,
            authoritative_key_count: 100,
            eligible_key_count: 90,
            missing_candidate_frontier_key_count: 10,
            criticality_unknown_key_count: 2,
            candidate_menu_key_count: 90,
            candidate_count: 270,
            feasible_candidate_count: 260,
            candidate_menu_coverage_rate: "0.9",
            repair_model_key_count: 50,
            repair_model_coverage_rate: "0.5",
            repair_credit_key_count: 40,
            repair_credit_coverage_rate: "0.4",
            low_confidence_key_count: 3,
            minimum_candidate_confidence: "0.45",
            tat_confidence_status: "partial",
            disclosure: "Authoritative universe coverage.",
          },
        })}
      />,
    );

    expect(screen.getByText("90/100 keys")).toBeInTheDocument();
    expect(
      screen.getByText(/10 authoritative keys were excluded/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/2 authoritative keys lack a known criticality tier/i),
    ).toBeInTheDocument();
  });

  it("drills into current, selected, and rejected numeric evidence", async () => {
    selectionState.items = [completedSelection()];
    const user = userEvent.setup();
    render(
      <PlanningRunDetail
        tenant="acme"
        run={run("completed", {
          summary: {
            currency: "USD",
            budget: "1000",
            selected_acquisition_cash: "500",
            budget_slack: "500",
            selected_key_count: 1,
            no_change_key_count: 0,
            selected_objective: "8",
            expected_shortage: "2",
            average_service_level: "0.92",
            maximum_aog_risk: "0.1",
            warning_count: 2,
            confidence_summary: {
              selected_confidence_total: "0.9",
              minimum_selected_confidence: "0.9",
              low_confidence_threshold: "0.5",
              low_confidence_key_count: 0,
            },
          },
          warnings: {
            total: 2,
            counted_items: 2,
            by_code: [{ code: "repair_evidence_limited", count: 2 }],
            code_list_truncated: false,
          },
        })}
      />,
    );

    expect(screen.getByText("Selected confidence")).toBeInTheDocument();
    expect(
      screen.getByText("Reconciled warnings").parentElement,
    ).toHaveTextContent("2");

    await user.click(
      screen.getByRole("button", {
        name: /compare choices for pn-1@mia/i,
      }),
    );
    const comparison = screen.getByRole("region", {
      name: /choice comparison for pn-1@mia/i,
    });
    const currentRow = screen.getByRole("row", {
      name: /current policy current baseline/i,
    });
    const selectedRow = screen.getByRole("row", {
      name: /selected plan buy five/i,
    });
    const rejectedRow = screen.getByRole("row", {
      name: /rejected buy ten/i,
    });

    expect(comparison).toContainElement(currentRow);
    expect(currentRow).toHaveTextContent("$0.00");
    expect(currentRow).toHaveTextContent("60%");
    expect(currentRow).toHaveTextContent("55%");
    expect(currentRow).toHaveTextContent("service-floor");
    expect(selectedRow).toHaveTextContent("$500.00");
    expect(selectedRow).toHaveTextContent("92%");
    expect(selectedRow).toHaveTextContent("8");
    expect(selectedRow).toHaveTextContent("90%");
    expect(rejectedRow).toHaveTextContent("$900.00");
    expect(rejectedRow).toHaveTextContent("100%");
    expect(rejectedRow).toHaveTextContent("10");
    expect(rejectedRow).toHaveTextContent("72%");
    expect(rejectedRow).toHaveTextContent("vendor-cap");
    expect(comparison).toHaveTextContent(/planning_trace evidence from snapshot/i);
    expect(comparison).toHaveTextContent(/hard constraint vendor-cap/i);
  });

  it("resets selection paging when the run identity changes", async () => {
    selectionState.items = [completedSelection()];
    selectionState.total = 50;
    const user = userEvent.setup();
    const summary = {
      currency: "USD",
      budget: "1000",
      selected_acquisition_cash: "500",
      budget_slack: "500",
      selected_key_count: 1,
      no_change_key_count: 0,
      selected_objective: "8",
      expected_shortage: "2",
      average_service_level: "0.92",
      maximum_aog_risk: "0.1",
    };
    const { rerender } = render(
      <PlanningRunDetail
        tenant="acme"
        run={run("completed", { summary })}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => {
      expect(selectionState.params).toMatchObject({ offset: 25 });
    });

    rerender(
      <PlanningRunDetail
        tenant="acme"
        run={run("completed", {
          run_id: "22222222-2222-2222-2222-222222222222",
          summary,
        })}
      />,
    );

    await waitFor(() => {
      expect(selectionState.params).toMatchObject({ offset: 0 });
    });
  });
});
