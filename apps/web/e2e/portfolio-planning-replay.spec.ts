import { expect, test, type Route } from "@playwright/test";
import type {
  PlanningRunSelectionRecord,
  PlanningRunView,
} from "../src/lib/api/planningRuns";
import type {
  ReplayEvidencePage,
  ReplayExclusionRecord,
  ReplayMetrics,
  ReplayRun,
  ReplayScorecardHeader,
} from "../src/lib/api/replay";

const TENANT = "acme";
const COMPLETED_RUN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const INFEASIBLE_RUN_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const REPLAY_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

const objectiveWeights = {
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
};

const coverage = {
  scope_key_count: 5,
  authoritative_key_count: 5,
  eligible_key_count: 3,
  missing_candidate_frontier_key_count: 2,
  criticality_unknown_key_count: 1,
  candidate_menu_key_count: 3,
  candidate_count: 6,
  feasible_candidate_count: 5,
  candidate_menu_coverage_rate: "0.6",
  repair_model_key_count: 2,
  repair_model_coverage_rate: "0.4",
  repair_credit_key_count: 1,
  repair_credit_coverage_rate: "0.2",
  low_confidence_key_count: 1,
  minimum_candidate_confidence: "0.55",
  tat_confidence_status: "partial" as const,
  disclosure: "Authoritative portfolio coverage from the submitted snapshot.",
};

function planningRun(
  runId: string,
  status: PlanningRunView["status"],
  updates: Partial<PlanningRunView> = {},
): PlanningRunView {
  const terminal = ["completed", "infeasible", "failed"].includes(status);
  return {
    run_id: runId,
    planning_fingerprint: `planning_${runId[0].repeat(64)}`,
    contract_version: "planning.v1",
    parent_run_id: null,
    parent_planning_fingerprint: null,
    parent_source_snapshot_hash: null,
    assumption_diff: [],
    status,
    source_snapshot_hash: "snapshot-submitted",
    source_generation_hash: "planning_generation_submitted",
    scope: {
      kind: "all_eligible",
      key_count: 5,
      preview_keys: ["PN-100@MIA", "PN-200@MIA", "PN-300@MIA"],
      preview_truncated: true,
    },
    key_count: 5,
    budget: "100000",
    horizon_days: 60,
    currency: "USD",
    model_profile: {
      tenant_policy_version: "policy-v1",
      forecast_version: "forecast-v1",
      repair_model_version: "repair-return.v1",
      candidate_planner_version: "candidate-planner-v1",
      optimizer_version: "optimizer-v1",
      objective_weights: objectiveWeights,
    },
    advisory_only: true,
    progress_completed: terminal ? 5 : 0,
    progress_total: 5,
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
    submitted_by: "portfolio-planner",
    attempts: terminal ? 1 : 0,
    claimed_at: terminal ? "2026-07-28T12:00:01Z" : null,
    started_at: terminal ? "2026-07-28T12:00:01Z" : null,
    finished_at: terminal ? "2026-07-28T12:00:03Z" : null,
    created_at: "2026-07-28T12:00:00Z",
    updated_at: "2026-07-28T12:00:03Z",
    coverage,
    stale: false,
    current_source_snapshot_hash: "snapshot-current",
    current_source_generation_hash: "planning_generation_current",
    stale_reason: null,
    ...updates,
  };
}

const completedRun = planningRun(COMPLETED_RUN_ID, "completed", {
  summary: {
    currency: "USD",
    budget: "100000",
    selected_acquisition_cash: "75000",
    budget_slack: "25000",
    selected_key_count: 3,
    no_change_key_count: 2,
    selected_objective: "18.5",
    expected_shortage: "4",
    average_service_level: "0.93",
    maximum_aog_risk: "0.2",
  },
  solver: {
    implementation: "scipy.optimize.milp/highs",
    implementation_version: "1.16.0",
    optimizer_version: "optimizer-v1",
    termination: "not_proven",
    optimality_proven: false,
    objective: "18.5",
    objective_bound: "19",
    relative_gap: "0.026",
    duration_ms: "30000",
    node_count: 42,
    message: "Time limit reached with a feasible incumbent.",
  },
  warnings: {
    total: 1,
    counted_items: 1,
    by_code: [{ code: "low_repair_confidence", count: 1 }],
    code_list_truncated: false,
  },
  skipped_keys: {
    total: 2,
    counted_items: 2,
    by_code: [{ code: "missing_candidate_frontier", count: 2 }],
    code_list_truncated: false,
  },
  stale: true,
  stale_reason:
    "This run used an older source generation. Review current evidence before relying on it.",
});

const infeasibleRun = planningRun(INFEASIBLE_RUN_ID, "infeasible", {
  budget: "1000",
  infeasibility: {
    minimum_budget_required: "5000",
    budget_shortfall: "4000",
    infeasible_key_count: 1,
    infeasible_key_sample: ["PN-CRITICAL@MIA"],
    infeasible_floor_count: 1,
    infeasible_floor_sample: ["critical-service-floor"],
  },
  solver: {
    implementation: "scipy.optimize.milp/highs",
    implementation_version: "1.16.0",
    optimizer_version: "optimizer-v1",
    termination: "infeasible",
    optimality_proven: false,
    objective: null,
    objective_bound: null,
    relative_gap: null,
    duration_ms: "120",
    node_count: 0,
    message: "No feasible portfolio satisfies every hard constraint.",
  },
  created_at: "2026-07-27T12:00:00Z",
  updated_at: "2026-07-27T12:00:03Z",
  claimed_at: "2026-07-27T12:00:01Z",
  started_at: "2026-07-27T12:00:01Z",
  finished_at: "2026-07-27T12:00:03Z",
});

const objective = {
  currency: "USD",
  criticality_weight: "5",
  shortage_reduction: "2",
  aog_risk_reduction: "0.1",
  incremental_holding_cost: "0",
  incremental_ordering_cost: "0",
  shortage_value: "10",
  aog_value: "0.5",
  holding_penalty: "0",
  ordering_penalty: "0",
  total: "10.5",
};

const currentChoice = {
  candidate_id: "candidate-current",
  label: "Current levels",
  candidate_kind: "no_change",
  acquisition_cash: "0",
  expected_shortage: "4",
  expected_service_level: "0.8",
  expected_aog_risk: "0.4",
  objective,
  confidence: "0.9",
  feasible: true,
  infeasibility_reasons: [],
  hard_constraint_ids: [],
  mandatory_floor_ids: [],
};

const selectedChoice = {
  ...currentChoice,
  candidate_id: "candidate-selected",
  label: "Repair-aware target",
  candidate_kind: "adjust_min_max",
  acquisition_cash: "25000",
  expected_shortage: "2",
  expected_service_level: "0.95",
  expected_aog_risk: "0.3",
};

const selection: PlanningRunSelectionRecord = {
  decision_key: "PN-100@MIA",
  current_candidate_id: currentChoice.candidate_id,
  selected_candidate_id: selectedChoice.candidate_id,
  selected_is_no_change: false,
  acquisition_cash: "25000",
  objective: "10.5",
  selection: {
    decision_key: "PN-100@MIA",
    current_candidate_id: currentChoice.candidate_id,
    selected_candidate_id: selectedChoice.candidate_id,
    selected_is_no_change: false,
    acquisition_cash: "25000",
    expected_shortage: "2",
    expected_service_level: "0.95",
    expected_aog_risk: "0.3",
    objective,
    floor_states: [],
  },
  detail: {
    decision_key: "PN-100@MIA",
    current: currentChoice,
    selected: selectedChoice,
    selected_reason:
      "Selected because it improves shortage and AOG risk within the hard budget.",
    rejected_alternatives: [],
  },
};

const replayMetrics: ReplayMetrics = {
  currency: "USD",
  outcome_manifest_sha256: "a".repeat(64),
  demanded_units: "0",
  filled_units: "0",
  backordered_units: "0",
  shortage_unit_days: "0",
  ending_inventory_units: "0",
  inventory_investment: "0",
  holding_cost: "0",
  ordering_cost: "0",
  acquisition_cash: "0",
  aog_risk_proxy_events: "0",
  decision_count: 0,
  fill_rate: "0",
};

const replayScorecard: ReplayScorecardHeader = {
  contract_version: "replay.v1",
  tenant_id: TENANT,
  currency: "USD",
  universe_id: "historical-approved-q2",
  universe_sha256: "b".repeat(64),
  current_policy_label: "Current policy",
  challenger_policy_label: "Repair-aware policy",
  comparison_rule: "matched_budget",
  comparison_rule_definition:
    "Compare policies at equal aggregate acquisition cash within tolerance.",
  match_tolerance: "0",
  advisory_only: true,
  observation_count: 0,
  total_observation_count: 1,
  excluded_observation_count: 1,
  coverage_rate: "0",
  exclusions_by_reason: [{ reason_code: "incomplete_horizon", count: 1 }],
  current: replayMetrics,
  challenger: replayMetrics,
  delta: {
    fill_rate: "0",
    backordered_units: "0",
    shortage_unit_days: "0",
    inventory_investment: "0",
    holding_cost: "0",
    ordering_cost: "0",
    acquisition_cash: "0",
    aog_risk_proxy_events: "0",
  },
  metric_definitions: [
    {
      metric: "fill_rate",
      unit: "ratio",
      denominator: "realized demanded units in completed matched horizons",
      exclusions: "decisions excluded by the approved universe manifest",
    },
  ],
  universe_decision_count: 1,
  cohort_count: 0,
  lineage_count: 0,
  source_snapshot_hash_count: 0,
  planning_fingerprint_count: 0,
  universe_decisions_sha256: "1".repeat(64),
  exclusions_sha256: "2".repeat(64),
  observation_lineage_sha256: "3".repeat(64),
  cohorts_sha256: "4".repeat(64),
  source_snapshot_hashes_sha256: "5".repeat(64),
  planning_fingerprints_sha256: "6".repeat(64),
};

const replayRun: ReplayRun = {
  replay_id: REPLAY_ID,
  replay_fingerprint: `replay_${"c".repeat(64)}`,
  input_sha256: "c".repeat(64),
  contract_version: "replay.v1",
  status: "completed",
  universe_ref: "approved-q2",
  universe_id: replayScorecard.universe_id,
  universe_sha256: replayScorecard.universe_sha256,
  comparison_rule: "matched_budget",
  expected_decision_count: 1,
  advisory_only: true,
  scorecard: replayScorecard,
  coverage_rate: "0",
  detail: {
    writeback_capability: "none",
    review_package: {
      input_sha256: "c".repeat(64),
      universe_sha256: replayScorecard.universe_sha256,
      trusted_input_sha256: "d".repeat(64),
      lineage_count: 0,
      exclusion_count: 1,
      cohort_count: 0,
    },
  },
  submitted_by: "portfolio-planner",
  attempts: 1,
  claimed_at: "2026-07-28T13:00:01Z",
  started_at: "2026-07-28T13:00:01Z",
  finished_at: "2026-07-28T13:00:02Z",
  created_at: "2026-07-28T13:00:00Z",
  updated_at: "2026-07-28T13:00:02Z",
};

const replayExclusion: ReplayExclusionRecord = {
  observation_id: "historical-approved-q2-excluded-1",
  decision_key: "PN-HISTORICAL@MIA",
  as_of: "2026-04-01T00:00:00Z",
  horizon_end: "2026-05-01T00:00:00Z",
  reason_code: "incomplete_horizon",
  exclusion: {
    observation_id: "historical-approved-q2-excluded-1",
    tenant_id: TENANT,
    decision_key: "PN-HISTORICAL@MIA",
    as_of: "2026-04-01T00:00:00Z",
    horizon_end: "2026-05-01T00:00:00Z",
    reason_code: "incomplete_horizon",
    detail: "The realized evaluation horizon is incomplete.",
  },
};

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

test("portfolio history, explanations, infeasibility, skips, and replay remain keyboard-accessible and advisory", async ({
  page,
}) => {
  const unexpectedApiRequests: string[] = [];
  const mutatingApiRequests: string[] = [];
  const writebackApiRequests: string[] = [];

  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!url.pathname.startsWith("/v1/")) return;
    const label = `${request.method()} ${url.pathname}`;
    if (!["GET", "HEAD", "OPTIONS"].includes(request.method())) {
      mutatingApiRequests.push(label);
    }
    if (
      /(writeback|commit|approve|rollback|decisions|recommendations|killswitch)/i.test(
        url.pathname,
      )
    ) {
      writebackApiRequests.push(label);
    }
  });

  await page.route("**/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const planningBase = `/v1/tenants/${TENANT}/planning-runs`;
    const replayBase = `/v1/tenants/${TENANT}/replay-runs`;

    if (method === "GET" && path === `${planningBase}/capabilities`) {
      await fulfillJson(route, {
        contract_version: "planning-capability.v1",
        enabled: true,
        advisory_only: true,
        can_read: true,
        can_submit: true,
        reason_code: "enabled",
      });
      return;
    }
    if (method === "GET" && path === planningBase) {
      await fulfillJson(route, [completedRun, infeasibleRun]);
      return;
    }
    if (
      method === "GET" &&
      path === `${planningBase}/${COMPLETED_RUN_ID}/selections`
    ) {
      await fulfillJson(route, {
        items: [selection],
        total: 1,
        limit: 25,
        offset: 0,
      });
      return;
    }
    if (method === "GET" && path === `${planningBase}/${COMPLETED_RUN_ID}`) {
      await fulfillJson(route, completedRun);
      return;
    }
    if (method === "GET" && path === `${planningBase}/${INFEASIBLE_RUN_ID}`) {
      await fulfillJson(route, infeasibleRun);
      return;
    }
    if (method === "GET" && path === `${replayBase}/universes`) {
      await fulfillJson(route, {
        items: [
          {
            universe_ref: replayRun.universe_ref,
            universe_id: replayRun.universe_id,
            universe_sha256: replayRun.universe_sha256,
            contract_version: "replay.v1",
            currency: "USD",
            expected_decision_count: 1,
            observation_count: 0,
            exclusion_count: 1,
            created_at: "2026-07-28T11:00:00Z",
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      });
      return;
    }
    if (method === "GET" && path === replayBase) {
      await fulfillJson(route, [replayRun]);
      return;
    }
    if (
      method === "GET" &&
      path === `${replayBase}/${REPLAY_ID}/exclusions`
    ) {
      const page: ReplayEvidencePage<ReplayExclusionRecord> = {
        items: [replayExclusion],
        total: 1,
        limit: 25,
        offset: 0,
      };
      await fulfillJson(route, page);
      return;
    }
    if (
      method === "GET" &&
      (path === `${replayBase}/${REPLAY_ID}/lineage` ||
        path === `${replayBase}/${REPLAY_ID}/cohorts`)
    ) {
      await fulfillJson(route, {
        items: [],
        total: 0,
        limit: 25,
        offset: 0,
      });
      return;
    }

    unexpectedApiRequests.push(`${method} ${path}`);
    await fulfillJson(
      route,
      {
        detail: {
          code: "unexpected_e2e_request",
          message: "The E2E fixture did not declare this API request.",
          retryable: false,
        },
      },
      404,
    );
  });

  await page.goto("/#/portfolio");

  await expect(
    page.getByRole("heading", { name: "Portfolio optimization" }),
  ).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Run history" })).toBeVisible();

  const completedHistoryButton = page.getByRole("button", {
    name: /aaaaaaaa.*stale inputs.*completed/i,
  });
  const infeasibleHistoryButton = page.getByRole("button", {
    name: /bbbbbbbb.*infeasible/i,
  });
  await expect(completedHistoryButton).toHaveAttribute("aria-pressed", "true");
  await expect(infeasibleHistoryButton).toHaveAttribute("aria-pressed", "false");

  await completedHistoryButton.focus();
  await expect(completedHistoryButton).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Run aaaaaaaa" }),
  ).toBeVisible();
  await expect(
    page.getByRole("meter", { name: "Acquisition budget used" }),
  ).toHaveAttribute("aria-valuenow", "75000");
  await expect(
    page.getByRole("group", { name: "Selection filters" }),
  ).toBeVisible();
  await expect(
    page.getByRole("table", {
      name: "Selected candidate and objective contribution for each planning key",
    }),
  ).toBeVisible();
  await expect(
    page
      .locator('[role="status"][aria-live="polite"]')
      .filter({ hasText: "1 matching selections" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "older source generation" }),
  ).toBeVisible();
  await expect(page.getByText("missing_candidate_frontier (2)")).toBeVisible();
  await expect(
    page.getByText(/2 authoritative keys were excluded/i),
  ).toBeVisible();

  const decisionDisclosure = page.getByText("PN-100@MIA", { exact: true });
  await decisionDisclosure.focus();
  await expect(decisionDisclosure).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    page.getByText(
      /selected because it improves shortage and AOG risk within the hard budget/i,
    ),
  ).toBeVisible();

  await infeasibleHistoryButton.focus();
  await expect(infeasibleHistoryButton).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Run bbbbbbbb" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "No feasible portfolio under these hard constraints",
    }),
  ).toBeVisible();
  await expect(page.getByText("PN-CRITICAL@MIA")).toBeVisible();
  await expect(page.getByText("critical-service-floor")).toBeVisible();
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "No actionable selections were produced" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", {
      name: "Key selections and objective ledger",
    }),
  ).toHaveCount(0);

  await expect(
    page.getByRole("combobox", { name: "Trusted replay universe" }),
  ).toHaveValue("approved-q2");
  await expect(
    page.getByRole("heading", { name: "Historical shadow validation" }),
  ).toBeVisible();
  await expect(
    page
      .locator('[aria-live="polite"]')
      .filter({ hasText: "Advisory only · no writeback" }),
  ).toBeVisible();
  await expect(
    page.getByText(/not a causal guarantee of future performance/i),
  ).toBeVisible();
  await expect(
    page.getByRole("table", {
      name: "Current and challenger historical outcomes",
    }),
  ).toBeVisible();
  await expect(
    page.getByText("The realized evaluation horizon is incomplete."),
  ).toBeVisible();

  expect(unexpectedApiRequests).toEqual([]);
  expect(mutatingApiRequests).toEqual([]);
  expect(writebackApiRequests).toEqual([]);
});
