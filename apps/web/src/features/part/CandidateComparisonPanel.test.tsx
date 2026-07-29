import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CandidateComparisonPanel } from "@/features/part/CandidateComparisonPanel";
import { PartDrillDown } from "@/features/part/PartDrillDown";
import type {
  CandidateFrontier,
  CandidateModelIdentity,
  CandidateTargetLevels,
  PartContext,
  PolicyCandidate,
} from "@/lib/api/types";

const contractVersion = "candidate.v1" as const;
const baselineLevels: CandidateTargetLevels = {
  contract_version: contractVersion,
  rop: 2,
  eoq: 4,
  safety_stock: 1,
  max_stock: 6,
};
const proposedLevels: CandidateTargetLevels = {
  contract_version: contractVersion,
  rop: 4,
  eoq: 6,
  safety_stock: 2,
  max_stock: 10,
};
const modelIdentity: CandidateModelIdentity = {
  contract_version: contractVersion,
  forecast_model: "CrostonSBA",
  forecast_version: "statsforecast-2.0.1",
  policy_model: "deterministic-minmax",
  policy_version: "policy-3.4.0",
  repair_model: "weibull-repair-tat",
  repair_version: "repair-1.2.0",
  member_forecasts: [
    {
      contract_version: contractVersion,
      decision_key: "PN-1@MIA",
      forecast_model: "CrostonSBA",
      forecast_version: "statsforecast-2.0.1",
    },
  ],
};

const baseline: PolicyCandidate = {
  contract_version: contractVersion,
  candidate_id: `cand_${"a".repeat(64)}`,
  tenant_id: "acme",
  pn: "PN-1",
  location: "MIA",
  decision_key: "PN-1@MIA",
  member_keys: ["PN-1@MIA"],
  candidate_kind: "no_change",
  label: "Keep current plan",
  is_no_change: true,
  feasible: true,
  infeasibility_reasons: [],
  model_identity: modelIdentity,
  current_levels: baselineLevels,
  target_levels: baselineLevels,
  actions: [
    {
      contract_version: contractVersion,
      line_id: "baseline",
      kind: "no_change",
      quantity: "0",
      currency: "USD",
      unit_acquisition_cash: "0",
      source_location: null,
      destination_location: null,
      source_reference: null,
    },
  ],
  action_quantity: "0",
  lifecycle_costs: {
    contract_version: contractVersion,
    currency: "USD",
    acquisition_cash: "0",
    holding_cost: "10.25",
    ordering_cost: "0",
    shortage_cost: "89.75",
    other_cost: "0",
    total_lifecycle_cost: "100.00",
  },
  outcome: {
    contract_version: contractVersion,
    projected_demand: "10",
    available_before: "4",
    expected_receipts_before: "2",
    inbound_quantity: "0",
    outbound_quantity: "0",
    ending_net_position: "-4",
    expected_shortage: "4",
    expected_excess: "0",
    expected_service_level: "0.6",
    expected_aog_risk: "0.75",
  },
  confidence: "0.82",
  constraints: [],
  evidence: [
    {
      contract_version: contractVersion,
      kind: "demand_history",
      source: "served-calculation",
      detail: "Exact demand and receipt trace",
      reference_id: "trace-1",
    },
  ],
  reconciliation: {
    contract_version: contractVersion,
    currency: "USD",
    available_before: "4",
    expected_receipts_before: "2",
    projected_demand: "10",
    transfer_in_quantity: "0",
    purchase_quantity: "0",
    outbound_quantity: "0",
    total_inbound_quantity: "0",
    action_quantity: "0",
    ending_net_position: "-4",
    expected_shortage: "4",
    acquisition_cash: "0",
  },
};

const feasibleAlternative: PolicyCandidate = {
  ...baseline,
  candidate_id: `cand_${"b".repeat(64)}`,
  candidate_kind: "transfer_purchase",
  label: "Transfer 2 and purchase 4",
  is_no_change: false,
  target_levels: proposedLevels,
  actions: [
    {
      contract_version: contractVersion,
      line_id: "transfer",
      kind: "transfer_in",
      quantity: "2",
      currency: "USD",
      unit_acquisition_cash: "0",
      source_location: "JFK",
      destination_location: "MIA",
      source_reference: "transfer-rec-1",
    },
    {
      contract_version: contractVersion,
      line_id: "purchase",
      kind: "purchase",
      quantity: "4",
      currency: "USD",
      unit_acquisition_cash: "12",
      source_location: null,
      destination_location: "MIA",
      source_reference: "purchase-rec-1",
    },
  ],
  action_quantity: "6",
  lifecycle_costs: {
    contract_version: contractVersion,
    currency: "USD",
    acquisition_cash: "48",
    holding_cost: "2.50",
    ordering_cost: "5",
    shortage_cost: "0",
    other_cost: "0.50",
    total_lifecycle_cost: "56.00",
  },
  outcome: {
    contract_version: contractVersion,
    projected_demand: "10",
    available_before: "4",
    expected_receipts_before: "2",
    inbound_quantity: "6",
    outbound_quantity: "0",
    ending_net_position: "2",
    expected_shortage: "0",
    expected_excess: "2",
    expected_service_level: "1",
    expected_aog_risk: "0.125",
  },
  confidence: "0.875",
  constraints: [
    {
      contract_version: contractVersion,
      constraint_id: "donor_dispatchable_excess_limit",
      source: "donor-stock:JFK",
      value: "2 units",
      scope: "action",
      hard: true,
      satisfied: true,
      binding: true,
      detail: "Transfer is capped by dispatchable donor excess.",
    },
    {
      contract_version: contractVersion,
      constraint_id: "minimum_order_quantity_action",
      source: "vendor-economics",
      value: "4 units",
      scope: "action",
      hard: true,
      satisfied: true,
      binding: false,
      detail: null,
    },
  ],
  evidence: [
    {
      contract_version: contractVersion,
      kind: "transfer_source",
      source: "finalized-recommendations",
      detail: "JFK donor has two dispatchable excess units",
      reference_id: "transfer-rec-1",
    },
  ],
  reconciliation: {
    contract_version: contractVersion,
    currency: "USD",
    available_before: "4",
    expected_receipts_before: "2",
    projected_demand: "10",
    transfer_in_quantity: "2",
    purchase_quantity: "4",
    outbound_quantity: "0",
    total_inbound_quantity: "6",
    action_quantity: "6",
    ending_net_position: "2",
    expected_shortage: "0",
    acquisition_cash: "48",
  },
};

const infeasibleAlternative: PolicyCandidate = {
  ...feasibleAlternative,
  candidate_id: `cand_${"c".repeat(64)}`,
  label: "Infeasible audit choice",
  feasible: false,
  infeasibility_reasons: ["Budget guardrail"],
};

const frontier: CandidateFrontier = {
  contract_version: contractVersion,
  frontier_fingerprint: `frontier_${"d".repeat(64)}`,
  output_digest: `output_${"e".repeat(64)}`,
  planner_version: "candidate-planner-v1",
  tenant_id: "acme",
  decision_key: "PN-1@MIA",
  member_keys: ["PN-1@MIA"],
  currency: "USD",
  candidates: [baseline, feasibleAlternative, infeasibleAlternative],
  total_options_considered: 4,
  dominated_options_removed: 1,
};

function renderPart(ui: ReactElement, initialPath: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/parts/:pn/:location" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("CandidateComparisonPanel", () => {
  it("shows the current/no-change baseline and feasible alternatives without presenting infeasible audit choices", () => {
    render(<CandidateComparisonPanel frontier={frontier} />);

    const panel = screen.getByRole("region", { name: "Candidate comparison" });
    expect(within(panel).getByText("Current / no change")).toBeInTheDocument();
    expect(within(panel).getByText("Transfer 2 and purchase 4")).toBeInTheDocument();
    expect(within(panel).queryByText("Infeasible audit choice")).not.toBeInTheDocument();
    expect(within(panel).getByText("JFK → MIA")).toBeInTheDocument();
    expect(within(panel).getByText("donor_dispatchable_excess_limit")).toBeInTheDocument();
    expect(within(panel).getByText(/JFK donor has two dispatchable excess units/)).toBeInTheDocument();

    const alternative = within(panel).getByTestId("candidate-alternative");
    expect(
      within(alternative).getByRole("row", {
        name: "Reorder point 2 4",
      }),
    ).toBeInTheDocument();
    expect(alternative).toHaveTextContent("Action quantity6");
    expect(alternative).toHaveTextContent("Acquisition cashUSD 48");
    expect(alternative).toHaveTextContent("Service level100%");
    expect(alternative).toHaveTextContent("Expected AOG risk12.5%");
    expect(alternative).toHaveTextContent("Confidence87.5%");
  });

  it("reports the actual served forecast, policy, and repair model identities", () => {
    render(<CandidateComparisonPanel frontier={frontier} />);

    const alternative = screen.getByTestId("candidate-alternative");
    expect(alternative).toHaveTextContent("CrostonSBA");
    expect(alternative).toHaveTextContent("statsforecast-2.0.1");
    expect(alternative).toHaveTextContent("deterministic-minmax");
    expect(alternative).toHaveTextContent("policy-3.4.0");
    expect(alternative).toHaveTextContent("weibull-repair-tat");
    expect(alternative).toHaveTextContent("repair-1.2.0");
  });

  it("renders the exact lifecycle and quantity reconciliation ledgers", () => {
    render(<CandidateComparisonPanel frontier={frontier} />);

    const alternative = screen.getByTestId("candidate-alternative");
    expect(within(alternative).getByTestId("lifecycle-reconciliation")).toHaveTextContent(
      "USD 56.00 = 48 + 2.50 + 5 + 0 + 0.50",
    );
    expect(within(alternative).getByTestId("quantity-reconciliation")).toHaveTextContent(
      "4 + 2 + 2 + 4 − 0 − 10 = 2",
    );
    expect(alternative).toHaveTextContent("Reconciled action quantity6");
    expect(alternative).toHaveTextContent("Reconciled acquisition cashUSD 48");
  });

  it("shows the stable fingerprint and reconciled dominated-option count", () => {
    render(<CandidateComparisonPanel frontier={frontier} />);

    expect(screen.getByTestId("frontier-fingerprint")).toHaveTextContent(
      frontier.frontier_fingerprint,
    );
    expect(screen.getByTestId("dominated-options-count")).toHaveTextContent(
      "1 dominated removed",
    );
    expect(screen.getByText("4 options considered")).toBeInTheDocument();
  });
});

describe("PartDrillDown candidate-preview compatibility", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps the candidate comparison absent for a legacy context without a frontier", async () => {
    const legacyContext: PartContext = {
      pn: "PN-1",
      location: "MIA",
      attributes: {
        description: "Legacy part",
        ata_chapter: null,
        part_class: null,
        shelf_life_days: null,
        hazardous_material: false,
        tool_control_item: false,
        criticality_tier: null,
      },
      stock: null,
      current_policy: null,
      proposed_policy: null,
      lead_time: null,
      open_orders: [],
      total_open_qty: 0,
      demand: null,
      unit_cost: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve(url.includes("/history") ? [] : legacyContext),
        }),
      ),
    );

    renderPart(<PartDrillDown />, "/parts/PN-1/MIA");

    expect(await screen.findByRole("heading", { name: "PN-1" })).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Candidate comparison" }),
    ).not.toBeInTheDocument();
  });
});
