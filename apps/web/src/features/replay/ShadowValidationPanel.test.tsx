import type { ReactElement } from "react";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShadowValidationPanel } from "@/features/replay/ShadowValidationPanel";
import {
  replayEvidenceQueryKey,
  replayRunsPollInterval,
  replayRunsQueryKey,
  replayUniversesQueryKey,
  type ReplayCohortRecord,
  type ReplayEvidencePage,
  type ReplayExclusionRecord,
  type ReplayLineageRecord,
  type ReplayRun,
  type ReplayScorecardHeader,
  type ReplayUniversePage,
} from "@/lib/api/replay";

const metrics = {
  currency: "USD",
  outcome_manifest_sha256: "a".repeat(64),
  demanded_units: "10",
  filled_units: "8",
  backordered_units: "2",
  shortage_unit_days: "6",
  ending_inventory_units: "3",
  inventory_investment: "300",
  holding_cost: "12",
  ordering_cost: "5",
  acquisition_cash: "100",
  aog_risk_proxy_events: "2",
  decision_count: 1,
  fill_rate: "0.8",
};

const challengerMetrics = {
  ...metrics,
  filled_units: "9",
  backordered_units: "1",
  shortage_unit_days: "3",
  inventory_investment: "350",
  holding_cost: "14",
  aog_risk_proxy_events: "1",
  fill_rate: "0.9",
};

const definitions = [
  {
    metric: "fill_rate",
    unit: "ratio",
    denominator: "realized demanded units in completed matched horizons",
    exclusions: "historical decisions excluded by the universe manifest",
  },
];

const scorecard: ReplayScorecardHeader = {
  contract_version: "replay.v1",
  tenant_id: "tenant-a",
  currency: "USD",
  universe_id: "historical-2026q1",
  universe_sha256: "b".repeat(64),
  current_policy_label: "Current policy",
  challenger_policy_label: "Repair-aware",
  comparison_rule: "matched_budget",
  comparison_rule_definition:
    "Compare policies at equal aggregate acquisition cash within tolerance.",
  match_tolerance: "0",
  advisory_only: true,
  observation_count: 1,
  total_observation_count: 2,
  excluded_observation_count: 1,
  coverage_rate: "0.5",
  exclusions_by_reason: [{ reason_code: "missing_price", count: 1 }],
  current: metrics,
  challenger: challengerMetrics,
  delta: {
    fill_rate: "0.1",
    backordered_units: "-1",
    shortage_unit_days: "-3",
    inventory_investment: "50",
    holding_cost: "2",
    ordering_cost: "0",
    acquisition_cash: "0",
    aog_risk_proxy_events: "-1",
  },
  metric_definitions: definitions,
  universe_decision_count: 2,
  cohort_count: 1,
  lineage_count: 1,
  source_snapshot_hash_count: 1,
  planning_fingerprint_count: 2,
  universe_decisions_sha256: "2".repeat(64),
  exclusions_sha256: "3".repeat(64),
  observation_lineage_sha256: "4".repeat(64),
  cohorts_sha256: "5".repeat(64),
  source_snapshot_hashes_sha256: "6".repeat(64),
  planning_fingerprints_sha256: "7".repeat(64),
};

const cohortRecord: ReplayCohortRecord = {
  cohort_id:
    "criticality:1|demand:intermittent|repairability:rotable|location:MIA|repair-confidence:observed",
  observation_count: 1,
  cohort: {
    cohort_id:
      "criticality:1|demand:intermittent|repairability:rotable|location:MIA|repair-confidence:observed",
    cohort: {
      criticality_tier: 1,
      demand_regime: "intermittent",
      repairability: "rotable",
      location_code: "MIA",
      repair_data_confidence: "observed",
      evidence_artifact_id: "part-attributes-1",
    },
    observation_count: 1,
    current: metrics,
    challenger: challengerMetrics,
    delta: {
      fill_rate: "0.1",
      backordered_units: "-1",
      shortage_unit_days: "-3",
      inventory_investment: "50",
      holding_cost: "2",
      ordering_cost: "0",
      acquisition_cash: "0",
      aog_risk_proxy_events: "-1",
    },
  },
};

const exclusionRecord: ReplayExclusionRecord = {
  observation_id: "obs-2",
  decision_key: "PN-2@MIA",
  as_of: "2026-01-01T00:00:00Z",
  horizon_end: "2026-01-31T00:00:00Z",
  reason_code: "missing_price",
  exclusion: {
    observation_id: "obs-2",
    tenant_id: "tenant-a",
    decision_key: "PN-2@MIA",
    as_of: "2026-01-01T00:00:00Z",
    horizon_end: "2026-01-31T00:00:00Z",
    reason_code: "missing_price",
    detail: "No historically effective price was available.",
  },
};

const lineageRecord: ReplayLineageRecord = {
  observation_id: "obs-1",
  decision_key: "PN-1@MIA",
  as_of: "2026-01-01T00:00:00Z",
  horizon_end: "2026-01-31T00:00:00Z",
  cohort_id: cohortRecord.cohort_id,
  lineage: {
    reference: {
      observation_id: "obs-1",
      decision_key: "PN-1@MIA",
      as_of: "2026-01-01T00:00:00Z",
      horizon_end: "2026-01-31T00:00:00Z",
      cohort_id: cohortRecord.cohort_id,
      source_snapshot_hash: "snapshot-as-of-2026-01-01",
      outcome_manifest_sha256: "c".repeat(64),
      current_planning_fingerprint: `planning_${"d".repeat(64)}`,
      challenger_planning_fingerprint: `planning_${"e".repeat(64)}`,
      current_request_sha256: "f".repeat(64),
      challenger_request_sha256: "0".repeat(64),
    },
    current: {
      tenant_id: "tenant-a",
      as_of: "2026-01-01T00:00:00+00:00",
      source_snapshot_hash: "snapshot-as-of-2026-01-01",
      planning_fingerprint: `planning_${"d".repeat(64)}`,
      planning_request_sha256: "f".repeat(64),
      forecast_version: "forecast-v4",
      repair_model_version: "repair-return-v1",
      tenant_policy_version: "tenant-policy-v3",
      candidate_planner_version: "candidate-planner-v2",
      objective_version: "objective-v2",
      solver: { termination: "optimal" },
    },
    challenger: {
      tenant_id: "tenant-a",
      as_of: "2026-01-01T00:00:00+00:00",
      source_snapshot_hash: "snapshot-as-of-2026-01-01",
      planning_fingerprint: `planning_${"e".repeat(64)}`,
      planning_request_sha256: "0".repeat(64),
      forecast_version: "forecast-v4",
      repair_model_version: "repair-return-v2",
      tenant_policy_version: "tenant-policy-v3",
      candidate_planner_version: "candidate-planner-v2",
      objective_version: "objective-v2",
      solver: { termination: "optimal" },
    },
    outcome: { manifest_sha256: "c".repeat(64) },
  },
};

function replayRun(
  status: ReplayRun["status"] = "completed",
): ReplayRun {
  return {
    replay_id: "11111111-1111-1111-1111-111111111111",
    replay_fingerprint: `replay_${"1".repeat(64)}`,
    input_sha256: "1".repeat(64),
    contract_version: "replay.v1",
    status,
    universe_ref: "historical-2026q1",
    universe_id: scorecard.universe_id,
    universe_sha256: scorecard.universe_sha256,
    comparison_rule: "matched_budget",
    expected_decision_count: 2,
    advisory_only: true,
    scorecard: status === "completed" ? scorecard : null,
    coverage_rate: status === "completed" ? "0.5" : null,
    detail:
      status === "failed"
        ? {
            error_code: "replay_worker_failed",
            guidance: "Review the replay manifest and retry as a new immutable run.",
          }
        : {
            writeback_capability: "none",
            review_package: {
              input_sha256: "1".repeat(64),
              universe_sha256: scorecard.universe_sha256,
              trusted_input_sha256: "8".repeat(64),
              lineage_count: 1,
              exclusion_count: 1,
              cohort_count: 1,
            },
          },
    submitted_by: "planner-user",
    attempts: status === "queued" ? 0 : 1,
    claimed_at: status === "queued" ? null : "2026-07-28T12:00:01Z",
    started_at: status === "queued" ? null : "2026-07-28T12:00:01Z",
    finished_at:
      status === "completed" || status === "failed"
        ? "2026-07-28T12:00:02Z"
        : null,
    created_at: "2026-07-28T12:00:00Z",
    updated_at: "2026-07-28T12:00:02Z",
  };
}

function renderPanel(
  ui: ReactElement,
  runs: ReplayRun[],
  universes?: ReplayUniversePage,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  client.setQueryData(replayRunsQueryKey("tenant-a"), runs);
  if (universes) {
    client.setQueryData(replayUniversesQueryKey("tenant-a"), universes);
  }
  const completed = runs.find((run) => run.status === "completed");
  if (completed) {
    const lineagePage: ReplayEvidencePage<ReplayLineageRecord> = {
      items: [lineageRecord],
      total: 1,
      limit: 25,
      offset: 0,
    };
    const exclusionPage: ReplayEvidencePage<ReplayExclusionRecord> = {
      items: [exclusionRecord],
      total: 1,
      limit: 25,
      offset: 0,
    };
    const cohortPage: ReplayEvidencePage<ReplayCohortRecord> = {
      items: [cohortRecord],
      total: 1,
      limit: 25,
      offset: 0,
    };
    client.setQueryData(
      replayEvidenceQueryKey(
        "tenant-a",
        completed.replay_id,
        "lineage",
        { limit: 25, offset: 0 },
      ),
      lineagePage,
    );
    client.setQueryData(
      replayEvidenceQueryKey(
        "tenant-a",
        completed.replay_id,
        "exclusions",
        { limit: 25, offset: 0 },
      ),
      exclusionPage,
    );
    client.setQueryData(
      replayEvidenceQueryKey(
        "tenant-a",
        completed.replay_id,
        "cohorts",
        { limit: 25, offset: 0 },
      ),
      cohortPage,
    );
  }
  const rendered = render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
  return { ...rendered, client };
}

describe("ShadowValidationPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders advisory caveat, matched outcomes, cohort coverage, methodology, and lineage", () => {
    renderPanel(
      <ShadowValidationPanel tenant="tenant-a" />,
      [replayRun()],
    );

    expect(
      screen.getByRole("heading", { name: /historical shadow validation/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/advisory only · no writeback/i)).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument();
    expect(screen.getByText(/1 evaluated · 1 excluded · 2 declared/i)).toBeInTheDocument();
    expect(screen.getByText("Tier 1")).toBeInTheDocument();
    expect(screen.getByText("intermittent")).toBeInTheDocument();
    expect(
      screen.getByText(/snapshot-set digest/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/no historically effective price/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("table", {
        name: /units, denominators, and exclusions/i,
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("forecast-v4")).toHaveLength(2);
    expect(screen.getByText("repair-return-v2")).toBeInTheDocument();
    expect(screen.getAllByText("objective-v2")).toHaveLength(2);
    expect(screen.getAllByText("optimal")).toHaveLength(2);
    expect(
      screen.getByText(/not a causal guarantee of future performance/i),
    ).toBeInTheDocument();
  });

  it("does not show partial metrics while queued or running", () => {
    renderPanel(
      <ShadowValidationPanel tenant="tenant-a" />,
      [replayRun("running")],
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      /no partial or actionable-looking result/i,
    );
    expect(screen.queryByText("Portfolio comparison")).not.toBeInTheDocument();
  });

  it("renders a safe non-actionable failure", () => {
    renderPanel(
      <ShadowValidationPanel tenant="tenant-a" />,
      [replayRun("failed")],
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      /stopped without a scorecard/i,
    );
    expect(screen.getByText(/replay_worker_failed/i)).toBeInTheDocument();
    expect(screen.queryByText("Portfolio comparison")).not.toBeInTheDocument();
  });

  it("explains the advisory boundary when no replay exists", () => {
    renderPanel(<ShadowValidationPanel tenant="tenant-a" />, []);

    expect(screen.getByText(/no historical replay has been submitted/i)).toBeInTheDocument();
    expect(
      screen.getByText(
        /cannot create purchases, transfers, repair routes, or policy writebacks/i,
      ),
    ).toBeInTheDocument();
  });

  it("lets planners submit only opaque trusted universe metadata and activates polling", async () => {
    const queued = replayRun("queued");
    const fetchMock = vi.fn().mockImplementation(
      (_url: string, init?: RequestInit) =>
        Promise.resolve({
          ok: true,
          status: init?.method === "POST" ? 201 : 200,
          statusText: "OK",
          json: () =>
            Promise.resolve(
              init?.method === "POST"
                ? { run: queued, created: true }
                : [queued],
            ),
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const universes: ReplayUniversePage = {
      items: [
        {
          universe_ref: "trusted-q1",
          universe_id: "Historical Q1",
          universe_sha256: "9".repeat(64),
          contract_version: "replay.v1",
          currency: "USD",
          expected_decision_count: 25,
          observation_count: 20,
          exclusion_count: 5,
          created_at: "2026-07-27T00:00:00Z",
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    };
    const { client } = renderPanel(
      <ShadowValidationPanel tenant="tenant-a" canSubmit />,
      [],
      universes,
    );

    expect(
      await screen.findByRole("combobox", {
        name: /trusted replay universe/i,
      }),
    ).toHaveValue("trusted-q1");
    expect(
      screen.getByRole("group", { name: /comparison rule/i }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/historical observations/i)).toBeNull();
    await user.clear(screen.getByLabelText(/match tolerance/i));
    await user.click(
      screen.getByRole("button", { name: /submit historical replay/i }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      /non-negative decimal/i,
    );
    expect(
      fetchMock.mock.calls.some(
        ([, init]) => (init as RequestInit | undefined)?.method === "POST",
      ),
    ).toBe(false);
    await user.type(screen.getByLabelText(/match tolerance/i), "0x10");
    await user.click(
      screen.getByRole("button", { name: /submit historical replay/i }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      /non-negative decimal/i,
    );
    await user.click(
      screen.getByRole("radio", { name: /matched service level/i }),
    );
    await user.clear(screen.getByLabelText(/match tolerance/i));
    await user.type(screen.getByLabelText(/match tolerance/i), "0.05");
    await user.click(
      screen.getByRole("button", { name: /submit historical replay/i }),
    );

    await waitFor(() => {
      expect(
        screen.getByText(/status polling is active/i),
      ).toBeInTheDocument();
    });
    const post = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "POST",
    );
    expect(post?.[0]).toContain(
      "/v1/tenants/tenant-a/replay-runs",
    );
    expect(JSON.parse((post?.[1] as RequestInit).body as string)).toEqual({
      universe_ref: "trusted-q1",
      currency: "USD",
      current_policy_label: "Current policy",
      challenger_policy_label: "Repair-aware policy",
      comparison_rule: "matched_service",
      match_tolerance: "0.05",
    });
    expect(
      client.getQueryData<ReplayRun[]>(replayRunsQueryKey("tenant-a"))?.[0]
        .status,
    ).toBe("queued");
    expect(
      replayRunsPollInterval(
        client.getQueryData<ReplayRun[]>(replayRunsQueryKey("tenant-a")),
      ),
    ).toBe(2_000);
  });

  it("shows launch dependencies for planners but no selector for viewers", () => {
    const empty: ReplayUniversePage = {
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    };
    const { unmount } = renderPanel(
      <ShadowValidationPanel tenant="tenant-a" canSubmit />,
      [],
      empty,
    );

    expect(
      screen.getByText(/no trusted replay universes available/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/historical facts cannot be entered or uploaded/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /submit historical replay/i }),
    ).not.toBeInTheDocument();

    unmount();
    renderPanel(<ShadowValidationPanel tenant="tenant-a" />, [], empty);
    expect(screen.getByText(/read-only replay evidence/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("combobox", {
        name: /trusted replay universe/i,
      }),
    ).not.toBeInTheDocument();
  });

  it("reports an idempotent completed replay without claiming polling is active", async () => {
    const completed = replayRun("completed");
    const fetchMock = vi.fn().mockImplementation(
      (_url: string, init?: RequestInit) =>
        Promise.resolve({
          ok: true,
          status: init?.method === "POST" ? 201 : 200,
          statusText: "OK",
          json: () =>
            Promise.resolve(
              init?.method === "POST"
                ? { run: completed, created: false }
                : [completed],
            ),
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const universes: ReplayUniversePage = {
      items: [
        {
          universe_ref: "historical-2026q1",
          universe_id: "Historical Q1",
          universe_sha256: "9".repeat(64),
          contract_version: "replay.v1",
          currency: "USD",
          expected_decision_count: 2,
          observation_count: 1,
          exclusion_count: 1,
          created_at: "2026-07-27T00:00:00Z",
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    };
    renderPanel(
      <ShadowValidationPanel tenant="tenant-a" canSubmit />,
      [completed],
      universes,
    );

    await user.click(
      await screen.findByRole("button", {
        name: /submit historical replay/i,
      }),
    );

    expect(
      await screen.findByText(/is already completed.*no duplicate run/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/status polling is active/i),
    ).not.toBeInTheDocument();
  });
});
