import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PartDrillDown } from "@/features/part/PartDrillDown";
import { bffClient } from "@/lib/api/client";
import type { HistoryEntry, PartContext } from "@/lib/api/types";

const samplePartContext: PartContext = {
  pn: "19000-231-3",
  location: "YYC",
  attributes: {
    description: "WATER TANK HEATER BLANKET",
    ata_chapter: "38",
    part_class: "CONSUMABLE",
    shelf_life_days: null,
    hazardous_material: false,
    tool_control_item: false,
    criticality_tier: 2,
  },
  stock: {
    on_hand: 4,
    serviceable: 3,
    in_repair: 1,
    allocated: 0,
    rental: 0,
    loan: 0,
  },
  current_policy: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
  proposed_policy: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
  lead_time: { promised_days: 30, realized_mean_days: 34.2, n_observations: 12 },
  procurement_lead_time: {
    condition: "NEW",
    status: "observed",
    mean_days: 34.2,
    p50_days: 30,
    p90_days: 45,
    p99_days: 52,
    n_observations: 12,
    source: "order_plan_closed_orders",
    grouping_level: "part_condition",
    confidence: "medium",
    data_cutoff: "2026-07-27",
    model_version: "supply-cycle-v1",
    classification_source: "explicit_order_type",
    proxy_definition: null,
    proxy_label: null,
    unavailable_reason: null,
  },
  repair_cycle_time: {
    condition: "REP",
    status: "observed",
    mean_days: 61,
    p50_days: 58,
    p90_days: 80,
    p99_days: 95,
    n_observations: 9,
    source: "order_plan_closed_orders",
    grouping_level: "part_vendor_condition",
    confidence: "low",
    data_cutoff: "2026-07-26",
    model_version: "supply-cycle-v1",
    classification_source: "legacy_order_id_prefix",
    proxy_definition: "order_creation_to_last_receipt",
    proxy_label: "RO cycle-time proxy",
    unavailable_reason: null,
  },
  open_orders: [
    { order_id: "PO-1", order_type: "PURCHASE", vendor: "ACME", qty_open: 2, expected_rcv_date: "2026-08-01" },
  ],
  total_open_qty: 2,
  open_orders_status: "available",
  demand: {
    total_24mo: 18,
    points: [{ period_start: "2026-06-01", removals: 1, issues: 0, total: 1 }],
  },
  unit_cost: 245.5,
};

const fullPlanningTrace: NonNullable<PartContext["planning_trace"]> = {
  calculation_source: "served_calculation",
  as_of: "2026-07-28",
  horizon_end: "2026-10-26",
  observation_start: "2023-01-01",
  observation_end: "2025-12-31",
  exposure_days: 1_096,
  bucket: "month",
  observed_periods: 36,
  zero_filled_periods: 29,
  demand_event_count: 7,
  event_count_source: "observed",
  demanded_units: 14,
  historical_per_day: 0.0127737,
  horizon_days: 90,
  projection_kind: "NBD",
  served_historical_per_day: 0.02,
  projected_historical_demand: 1.8,
  scheduled_demand_status: "available",
  scheduled_demand_undated_lines: 0,
  scheduled_demand_undated_units: 0,
  scheduled_demand_due: 3,
  projected_demand: 4.8,
  dispatchable_available: 4,
  open_receipts_status: "available",
  open_receipts_undated_lines: 0,
  open_receipts_undated_units: 0,
  open_receipts_due: 4,
  overdue_open_receipts_due: 2,
  repair_receipts_due: 0,
  expected_receipts_due: 4,
  net_position: 3.2,
  shortage_before_action: 0,
  pooled_group_id: null,
  pooling_scope: "single_key",
  excluded_member_keys: [],
  members: [
    {
      pn: "19000-231-3",
      location: "YYC",
      projection_kind: "NBD",
      projected_historical_demand: 1.8,
      scheduled_demand_status: "available",
      scheduled_demand_undated_lines: 0,
      scheduled_demand_undated_units: 0,
      scheduled_demand_due: 3,
      projected_demand: 4.8,
      dispatchable_available: 4,
      open_receipts_status: "available",
      open_receipts_undated_lines: 0,
      open_receipts_undated_units: 0,
      open_receipts_due: 4,
      overdue_open_receipts_due: 2,
      repair_receipts_due: 0,
      expected_receipts_due: 4,
      net_position: 3.2,
    },
  ],
  constraints: [
    {
      name: "Shelf-life ceiling",
      value: "12 units",
      binding: true,
      source: "eMRO Part Master",
      scope: "policy",
    },
    {
      name: "minimum_order_quantity_action",
      value: "5 units",
      binding: true,
      source: "Vendor economics",
      scope: "action",
    },
  ],
  warnings: [],
};

const fullTracePartContext: PartContext = {
  ...samplePartContext,
  planning_trace: fullPlanningTrace,
};

const repairPipelinePartContext: PartContext = {
  ...samplePartContext,
  open_orders: [
    {
      order_id: "PO-1",
      order_type: "PO",
      vendor: "PROCUREMENT-VENDOR",
      qty_open: 2,
      expected_rcv_date: "2026-04-10",
    },
    {
      order_id: "RO-VALID",
      order_line_id: "20",
      order_type: "RO",
      vendor: "REPAIR-VENDOR",
      shop: "SHOP-YYC",
      qty_open: 4,
      expected_rcv_date: null,
      opened_at: "2026-03-05T00:00:00+00:00",
      status: "IN_PROGRESS",
      serial_number: "SN-100",
      location: "YYC",
    },
  ],
  total_open_qty: 6,
  open_orders_status: "partial",
  repair_pipeline: {
    contract_version: "repair-pipeline.v1",
    tenant_id: "acme",
    part_number: "19000-231-3",
    location_code: "YYC",
    as_of: "2026-04-01",
    status: "partial",
    aggregate_wip_quantity: 5,
    identified_open_quantity: 7,
    unidentified_source_quantity: 0,
    eligible_quantity: 2,
    excluded_identifiable_quantity: 5,
    aggregate_residual_quantity: 0,
    source_overflow_quantity: 2,
    time_phased_credit_quantity: 0,
    included: [
      {
        work_item: {
          contract_version: "repair-work-item.v1",
          tenant_id: "acme",
          repair_order_id: "RO-VALID",
          repair_line_id: "20",
          part_number: "19000-231-3",
          quantity: 4,
          location_code: "YYC",
          opened_at: "2026-03-05T00:00:00+00:00",
          status: "in_progress",
          shop_code: "SHOP-YYC",
          vendor_code: "REPAIR-VENDOR",
          serial_number: "SN-100",
        },
        eligible_quantity: 2,
        age_days: 27,
      },
    ],
    exclusions: [
      {
        repair_order_id: "RO-AMBIG",
        repair_line_id: "10",
        serial_number: null,
        quantity: 3,
        reason: "missing_opened_at",
        detail: "opened_at is required to establish repair age",
      },
      {
        repair_order_id: "RO-VALID",
        repair_line_id: "20",
        serial_number: "SN-100",
        quantity: 2,
        reason: "aggregate_wip_cap",
        detail: "identified work exceeds aggregate in-repair WIP",
      },
    ],
    warning_codes: [
      "repair_age_missing",
      "repair_wip_mismatch",
      "repair_work_excluded",
    ],
    evidence_source: "open_orders_snapshot+stock_position",
  },
  repair_return_profile: {
    contract_version: "repair-return-profile.v1",
    tenant_id: "acme",
    part_number: "19000-231-3",
    location_code: "YYC",
    as_of: "2026-04-01",
    status: "partial",
    eligible_quantity: 2,
    excluded_quantity: 5,
    aggregate_residual_quantity: 0,
    horizons: [
      {
        horizon_days: 30,
        eligible_quantity: 2,
        expected_units: 0.5278,
        variance_units: 0.3885,
        p10_units: 0,
        p90_units: 1.3264,
        mean_serviceable_probability: 0.2639,
        item_probabilities: [
          {
            repair_order_id: "RO-VALID",
            repair_line_id: "20",
            serial_number: "SN-100",
            quantity: 2,
            age_days: 27,
            return_probability: 0.2639,
            serviceable_probability: 0.2639,
            expected_serviceable_units: 0.5278,
          },
        ],
      },
      {
        horizon_days: 60,
        eligible_quantity: 2,
        expected_units: 1.1254,
        variance_units: 0.4921,
        p10_units: 0.226,
        p90_units: 2,
        mean_serviceable_probability: 0.5627,
        item_probabilities: [
          {
            repair_order_id: "RO-VALID",
            repair_line_id: "20",
            serial_number: "SN-100",
            quantity: 2,
            age_days: 27,
            return_probability: 0.5627,
            serviceable_probability: 0.5627,
            expected_serviceable_units: 1.1254,
          },
        ],
      },
      {
        horizon_days: 90,
        eligible_quantity: 2,
        expected_units: 1.6032,
        variance_units: 0.318,
        p10_units: 0.8803,
        p90_units: 2,
        mean_serviceable_probability: 0.8016,
        item_probabilities: [
          {
            repair_order_id: "RO-VALID",
            repair_line_id: "20",
            serial_number: "SN-100",
            quantity: 2,
            age_days: 27,
            return_probability: 0.8016,
            serviceable_probability: 0.8016,
            expected_serviceable_units: 1.6032,
          },
        ],
      },
    ],
    exclusions: [
      {
        repair_order_id: "RO-AMBIG",
        repair_line_id: "10",
        serial_number: null,
        quantity: 3,
        reason: "missing_opened_at",
        detail: "opened_at is required to establish repair age",
      },
      {
        repair_order_id: "RO-VALID",
        repair_line_id: "20",
        serial_number: "SN-100",
        quantity: 2,
        reason: "aggregate_wip_cap",
        detail: "identified work exceeds aggregate in-repair WIP",
      },
    ],
    evidence: {
      method: "lognormal_quantile",
      completed_observations: 12,
      right_censored_observations: 0,
      serviceable_yield: 1,
      tat_multiplier: 1,
      source: "order_plan_closed_orders",
      confidence: "medium",
      data_cutoff: "2026-03-31",
      model_version: "repair-return.v1+supply-cycle-v1",
      proxy_definition: "order_creation_to_last_receipt",
    },
    warning_codes: [
      "repair_age_missing",
      "repair_return_right_censoring_not_fitted",
      "repair_wip_mismatch",
      "repair_work_excluded",
    ],
  },
};

function stubFetch(history: HistoryEntry[] = [], partContext: PartContext = samplePartContext) {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    if (url.includes("/history")) return Promise.resolve({ ok: true, json: () => Promise.resolve(history) });
    return Promise.resolve({ ok: true, json: () => Promise.resolve(partContext) });
  });
  vi.stubGlobal(
    "fetch",
    fetchMock,
  );
  return fetchMock;
}

function renderWithProviders(ui: ReactElement, initialPath: string) {
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

describe("PartDrillDown", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the full planning calculation trace with distinct events, units, windows, and horizon inputs", async () => {
    stubFetch([], fullTracePartContext);

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const trace = await screen.findByRole("region", { name: "Planning calculation trace" });
    expect(trace).toHaveTextContent("Jan 1, 2023 – Dec 31, 2025 (inclusive)");
    expect(trace).toHaveTextContent("Jul 28, 2026 – Oct 26, 2026 (inclusive)");
    expect(trace).toHaveTextContent("1,096 days");
    expect(trace).toHaveTextContent("7 events");
    expect(trace).toHaveTextContent("14 units");
    expect(trace).toHaveTextContent("29");
    expect(trace).toHaveTextContent("Raw observed historical demand rate");
    expect(trace).toHaveTextContent("Served historical forecast rate");
    expect(trace).toHaveTextContent("Served distribution");
    expect(trace).toHaveTextContent("Served historical demand over 90 days");
    expect(trace).toHaveTextContent("Scheduled demand due in horizon");
    expect(trace).toHaveTextContent("Open receipts due by horizon");
    expect(trace).toHaveTextContent("2 overdue open receipt units");
    expect(trace).toHaveTextContent("Expected receipts due");
    expect(trace).toHaveTextContent("Net position before action");
    expect(trace).toHaveTextContent(/not guaranteed/i);
    expect(trace).toHaveTextContent("Shelf-life ceiling");
    expect(trace).toHaveTextContent("Binding");
    expect(
      within(trace).getByLabelText("Shelf-life ceiling scope: Policy"),
    ).toBeInTheDocument();
    expect(
      within(trace).getByLabelText(
        "minimum_order_quantity_action scope: Action",
      ),
    ).toBeInTheDocument();
    expect(
      within(trace).getByLabelText(
        "Calculation evidence source: Exact served calculation",
      ),
    ).toBeInTheDocument();
    expect(
      within(trace).getByLabelText("Scheduled demand evidence status: Available"),
    ).toBeInTheDocument();
    expect(
      within(trace).getByLabelText("Open receipts evidence status: Available"),
    ).toBeInTheDocument();
    expect(
      within(trace).getByLabelText("Exact projected-demand reconciliation"),
    ).toHaveTextContent(
      "1.8 units projected historical + 3 units scheduled = 4.8 units projected demand",
    );
    expect(
      within(trace).getByLabelText("Exact net-position reconciliation"),
    ).toHaveTextContent(
      "4 units dispatchable + 4 units expected receipts − 4.8 units projected demand = 3.2 units net position",
    );
    expect(
      within(trace).getByRole("note", { name: "Repair receipt methodology" }),
    ).toHaveTextContent(/zero repair-receipt value is deliberate/i);
    expect(trace.querySelectorAll('[data-testid="prov-chip"]').length).toBeGreaterThanOrEqual(10);
  });

  it("keeps a legacy response without planning_trace usable", async () => {
    stubFetch();

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    expect(await screen.findByText("WATER TANK HEATER BLANKET")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Planning calculation trace" }),
    ).not.toBeInTheDocument();
  });

  it("renders the reconciled open-repair pipeline with exact evidence and zero credit", async () => {
    stubFetch([], repairPipelinePartContext);

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const pipeline = await screen.findByRole("region", {
      name: "Open repair pipeline",
    });
    expect(
      within(pipeline).getByLabelText(
        "Repair pipeline evidence status: Partial",
      ),
    ).toBeInTheDocument();

    const quantity = (label: string) =>
      within(pipeline).getByText(label).closest("div");
    expect(quantity("Aggregate WIP")).toHaveTextContent("5");
    expect(quantity("Identified open")).toHaveTextContent("7");
    expect(quantity("Missing-identity source")).toHaveTextContent("0");
    expect(quantity("Eligible for future modeling")).toHaveTextContent("2");
    expect(quantity("Excluded identifiable")).toHaveTextContent("5");
    expect(quantity("Aggregate residual")).toHaveTextContent("0");
    expect(quantity("Source overflow")).toHaveTextContent("2");
    expect(quantity("Time-phased credit")).toHaveTextContent("0");

    const methodology = within(pipeline).getByRole("note", {
      name: "Conservative repair-credit methodology",
    });
    expect(methodology).toHaveTextContent(
      "Phase 5 grants zero time-phased repair credit.",
    );
    expect(methodology).toHaveTextContent(
      "PO lines remain procurement receipts",
    );
    expect(methodology).toHaveTextContent(
      "repair work is never counted as a generic open receipt",
    );

    const warnings = within(pipeline).getByRole("note", {
      name: "Repair pipeline data-quality warnings",
    });
    expect(warnings).toHaveTextContent("repair_age_missing");
    expect(warnings).toHaveTextContent("repair_wip_mismatch");
    expect(warnings).toHaveTextContent("repair_work_excluded");

    const included = within(pipeline).getByRole("table", {
      name: "Included open repair positions",
    });
    expect(included).toHaveTextContent("RO-VALID");
    expect(included).toHaveTextContent("Line 20");
    expect(included).toHaveTextContent("27 days");
    expect(included).toHaveTextContent("in_progress");
    expect(included).toHaveTextContent("SHOP-YYC");
    expect(included).toHaveTextContent("REPAIR-VENDOR");
    expect(included).toHaveTextContent("SN-100");
    expect(included).toHaveTextContent("YYC");

    const excluded = within(pipeline).getByRole("table", {
      name: "Excluded open repair positions",
    });
    expect(excluded).toHaveTextContent("RO-AMBIG");
    expect(excluded).toHaveTextContent("missing_opened_at");
    expect(excluded).toHaveTextContent(
      "opened_at is required to establish repair age",
    );
    expect(excluded).toHaveTextContent("aggregate_wip_cap");

    expect(pipeline).toHaveTextContent("open_orders_snapshot+stock_position");
    expect(pipeline).toHaveTextContent("2026-04-01");
    expect(pipeline).toHaveTextContent("repair-pipeline.v1");

    const openOrders = screen.getByRole("table", {
      name: "Open orders for 19000-231-3 / YYC",
    });
    expect(openOrders).toHaveTextContent("RO-VALID");
    expect(openOrders).toHaveTextContent("Line 20");
    expect(openOrders).toHaveTextContent("IN_PROGRESS");
    expect(openOrders).toHaveTextContent("SHOP-YYC");
    expect(openOrders).toHaveTextContent("SN-100");
  });

  it("renders bounded age-conditioned repair returns without false precision", async () => {
    stubFetch([], repairPipelinePartContext);

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const returns = await screen.findByRole("region", {
      name: "Projected repair returns",
    });
    expect(
      within(returns).getByLabelText("Repair return evidence status: Partial"),
    ).toBeInTheDocument();
    expect(returns).toHaveTextContent("Eligible open WIP2");
    expect(returns).toHaveTextContent("Excluded identifiable5");
    expect(returns).toHaveTextContent("Aggregate residual0");
    expect(
      within(returns).getByRole("note", {
        name: "Age-conditioned repair return methodology",
      }),
    ).toHaveTextContent("repair clock is not restarted at day zero");

    const horizons = within(returns).getByRole("table", {
      name: "Repair return horizon summary",
    });
    expect(horizons).toHaveTextContent("30 days");
    expect(horizons).toHaveTextContent("60 days");
    expect(horizons).toHaveTextContent("90 days");
    expect(horizons).toHaveTextContent("1.6");
    expect(horizons).toHaveTextContent("0.9–2");
    expect(horizons).toHaveTextContent("80.2%");
    expect(horizons).not.toHaveTextContent("1.6032");

    const items = within(returns).getByRole("table", {
      name: "90 day repair item probabilities",
    });
    expect(items).toHaveTextContent("RO-VALID");
    expect(items).toHaveTextContent("Line 20");
    expect(items).toHaveTextContent("SN-100");
    expect(items).toHaveTextContent("27 days");
    expect(items).toHaveTextContent("80.2%");
    expect(items).toHaveTextContent("1.6");

    const warnings = within(returns).getByRole("note", {
      name: "Repair return warnings",
    });
    expect(warnings).toHaveTextContent("repair_age_missing");
    expect(warnings).toHaveTextContent(
      "repair_return_right_censoring_not_fitted",
    );
    expect(warnings).toHaveTextContent("repair_wip_mismatch");
    expect(warnings).toHaveTextContent("repair_work_excluded");

    const exclusions = within(returns).getByRole("table", {
      name: "Repair return exclusions",
    });
    expect(exclusions).toHaveTextContent("missing_opened_at");
    expect(exclusions).toHaveTextContent("aggregate_wip_cap");

    expect(returns).toHaveTextContent(
      "Lognormal fit from REP quantiles · lognormal_quantile",
    );
    expect(returns).toHaveTextContent(
      "Right-censored observations used in fit0",
    );
    expect(returns).toHaveTextContent(
      "Open WIP ages condition each projection, but are not used to fit",
    );
    expect(returns).toHaveTextContent(
      "100% (model input; not an observed yield)",
    );
    expect(returns).toHaveTextContent("order_plan_closed_orders");
    expect(returns).toHaveTextContent("2026-03-31");
    expect(returns).toHaveTextContent("repair-return-profile.v1");
  });

  it("treats a legacy response without a repair pipeline as unknown, not zero", async () => {
    stubFetch();

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const pipeline = await screen.findByRole("region", {
      name: "Open repair pipeline",
    });
    expect(pipeline).toHaveTextContent(
      "Quantities are unavailable, not observed zeros.",
    );
    expect(
      within(pipeline).getByLabelText(
        "Repair pipeline evidence status: Unavailable",
      ),
    ).toBeInTheDocument();
    expect(within(pipeline).getAllByText("—")).toHaveLength(8);
    expect(
      within(pipeline).queryByRole("table", {
        name: "Included open repair positions",
      }),
    ).not.toBeInTheDocument();

    const returns = screen.getByRole("region", {
      name: "Projected repair returns",
    });
    expect(returns).toHaveTextContent(
      "Expected units and probabilities are unavailable, not observed zeros.",
    );
    expect(
      within(returns).getByLabelText(
        "Repair return evidence status: Unavailable",
      ),
    ).toBeInTheDocument();
    expect(
      within(returns).queryByRole("table", {
        name: "Repair return horizon summary",
      }),
    ).not.toBeInTheDocument();
  });

  it("labels censoring as fitted only for a raw-history Kaplan-Meier profile", async () => {
    const profile = repairPipelinePartContext.repair_return_profile;
    expect(profile).not.toBeNull();
    stubFetch([], {
      ...repairPipelinePartContext,
      repair_return_profile: {
        ...profile!,
        status: "available",
        evidence: {
          ...profile!.evidence,
          method: "kaplan_meier",
          right_censored_observations: 2,
          source:
            "order_plan_closed_orders+open_work_right_censoring",
          model_version:
            "repair-return.v1+supply-cycle-v2",
        },
        warning_codes: [],
      },
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const returns = await screen.findByRole("region", {
      name: "Projected repair returns",
    });
    expect(returns).toHaveTextContent(
      "Kaplan–Meier survival · kaplan_meier",
    );
    expect(returns).toHaveTextContent(
      "Right-censored observations used in fit2",
    );
    expect(returns).not.toHaveTextContent("Fallback censoring treatment");
  });

  it("renders independent NEW and REP distributions with wire provenance", async () => {
    stubFetch();

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const procurement = await screen.findByRole("region", {
      name: "Procurement lead time (NEW)",
    });
    const repair = screen.getByRole("region", {
      name: "Repair cycle time (REP)",
    });

    expect(
      within(procurement).getByLabelText(
        "Procurement lead time evidence status: Observed",
      ),
    ).toBeInTheDocument();
    expect(procurement).toHaveTextContent("34.2d");
    expect(procurement).toHaveTextContent("30.0d");
    expect(procurement).toHaveTextContent("45.0d");
    expect(procurement).toHaveTextContent("52.0d");
    expect(procurement).toHaveTextContent("12");
    expect(procurement).toHaveTextContent(
      "Closed orders · order_plan_closed_orders",
    );
    expect(procurement).toHaveTextContent("Part + condition");
    expect(procurement).toHaveTextContent("Medium");
    expect(procurement).toHaveTextContent("2026-07-27");
    expect(procurement).toHaveTextContent("supply-cycle-v1");
    expect(procurement).toHaveTextContent("Explicit order type");
    expect(procurement).toHaveTextContent("Not applicable");
    expect(procurement).not.toHaveTextContent("61.0d");
    expect(procurement).not.toHaveTextContent(/updated|% coverage/i);

    expect(
      within(repair).getByLabelText(
        "Repair cycle time evidence status: Observed",
      ),
    ).toBeInTheDocument();
    expect(
      within(repair).getByLabelText(
        "Repair cycle time proxy label: RO cycle-time proxy",
      ),
    ).toBeInTheDocument();
    expect(repair).toHaveTextContent("61.0d");
    expect(repair).toHaveTextContent("58.0d");
    expect(repair).toHaveTextContent("80.0d");
    expect(repair).toHaveTextContent("95.0d");
    expect(repair).toHaveTextContent("9");
    expect(repair).toHaveTextContent("Part + vendor + condition");
    expect(repair).toHaveTextContent("Low");
    expect(repair).toHaveTextContent("2026-07-26");
    expect(repair).toHaveTextContent("Legacy order-ID prefix");
    expect(repair).toHaveTextContent("Order creation to last receipt");
    expect(repair).toHaveTextContent(
      "Creation-to-last-receipt is descriptive repair TAT, not projected repair supply.",
    );
    expect(repair).not.toHaveTextContent("34.2d");
    expect(repair).not.toHaveTextContent(/updated|% coverage/i);
  });

  it("labels configured repair fallback separately from the RO proxy", async () => {
    stubFetch([], {
      ...samplePartContext,
      repair_cycle_time: {
        condition: "REP",
        status: "configured_fallback",
        mean_days: 70,
        p50_days: 70,
        p90_days: 70,
        p99_days: 70,
        n_observations: 0,
        source: "pn_vendor_price",
        grouping_level: "part_condition",
        confidence: "low",
        data_cutoff: "2026-07-25",
        model_version: "supply-cycle-v1",
        classification_source: "configured_condition",
        proxy_definition: "configured_repair_promise",
        proxy_label: "Configured repair promise",
        unavailable_reason: null,
      },
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const repair = await screen.findByRole("region", {
      name: "Repair cycle time (REP)",
    });
    expect(
      within(repair).getByLabelText(
        "Repair cycle time evidence status: Configured fallback",
      ),
    ).toBeInTheDocument();
    expect(
      within(repair).getByLabelText(
        "Repair cycle time proxy label: Configured repair promise",
      ),
    ).toBeInTheDocument();
    expect(repair).toHaveTextContent(
      "Configured repair promise; no observed repair-cycle distribution is available.",
    );
    expect(repair).toHaveTextContent("Configured promise · pn_vendor_price");
    expect(repair).toHaveTextContent("0");
    expect(repair).not.toHaveTextContent("RO cycle-time proxy");
    expect(repair).not.toHaveTextContent("not projected repair supply");
  });

  it("renders unavailable lanes without presenting null metrics as zero", async () => {
    stubFetch([], {
      ...samplePartContext,
      procurement_lead_time: {
        condition: "NEW",
        status: "unavailable",
        // Deliberately dirty unavailable payload: the UI must fail closed.
        mean_days: 999,
        p50_days: 998,
        p90_days: 1_000,
        p99_days: 1_001,
        n_observations: 99,
        source: "order_plan_closed_orders",
        grouping_level: "part_condition",
        confidence: "high",
        data_cutoff: "2099-01-01",
        model_version: "untrusted-model",
        classification_source: "explicit_order_type",
        proxy_definition: null,
        proxy_label: null,
        unavailable_reason: "No NEW procurement evidence is available.",
      },
      repair_cycle_time: {
        condition: "REP",
        status: "unavailable",
        mean_days: null,
        p50_days: null,
        p90_days: null,
        p99_days: null,
        n_observations: 0,
        source: null,
        grouping_level: null,
        confidence: "unknown",
        data_cutoff: null,
        model_version: null,
        classification_source: null,
        proxy_definition: null,
        proxy_label: null,
        unavailable_reason: "No REP repair-cycle evidence is available.",
      },
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    for (const [name, reason] of [
      ["Procurement lead time (NEW)", "No NEW procurement evidence is available."],
      ["Repair cycle time (REP)", "No REP repair-cycle evidence is available."],
    ] as const) {
      const card = await screen.findByRole("region", { name });
      expect(card).toHaveTextContent(reason);
      const statistics = within(card).getByLabelText(
        name.replace(/ \(.+\)$/, "") + " distribution statistics",
      );
      expect(statistics).not.toHaveTextContent(/\b0\b/);
      expect(statistics).toHaveTextContent("—");
      expect(card).toHaveTextContent("Unknown");
      expect(card).toHaveTextContent("Unavailable");
    }
    const procurement = screen.getByRole("region", {
      name: "Procurement lead time (NEW)",
    });
    expect(procurement).not.toHaveTextContent("999");
    expect(procurement).not.toHaveTextContent("order_plan_closed_orders");
    expect(procurement).not.toHaveTextContent("2099-01-01");
    expect(procurement).not.toHaveTextContent("untrusted-model");
  });

  it("keeps legacy lead_time in NEW only and never reuses it for REP", async () => {
    const {
      procurement_lead_time: _procurement,
      repair_cycle_time: _repair,
      ...legacyContext
    } = samplePartContext;
    stubFetch([], legacyContext);

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const procurement = await screen.findByRole("region", {
      name: "Procurement lead time (NEW)",
    });
    const repair = screen.getByRole("region", {
      name: "Repair cycle time (REP)",
    });
    expect(
      within(procurement).getByLabelText(
        "Procurement lead time evidence status: Legacy compatibility",
      ),
    ).toBeInTheDocument();
    expect(procurement).toHaveTextContent("34.2d");
    expect(procurement).toHaveTextContent("Legacy promised lead: 30.0d");
    expect(procurement).toHaveTextContent("ConfidenceUnknown");
    expect(procurement).not.toHaveTextContent("RO cycle-time proxy");

    expect(
      within(repair).getByLabelText(
        "Repair cycle time evidence status: Unavailable",
      ),
    ).toBeInTheDocument();
    expect(repair).not.toHaveTextContent("34.2d");
    expect(repair).not.toHaveTextContent("30.0d");
    expect(repair).toHaveTextContent(
      "Repair cycle time is absent from this legacy response.",
    );
  });

  it("withholds supply-cycle objects returned under the wrong condition", async () => {
    stubFetch([], {
      ...samplePartContext,
      procurement_lead_time: samplePartContext.repair_cycle_time,
      repair_cycle_time: samplePartContext.procurement_lead_time,
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const procurement = await screen.findByRole("region", {
      name: "Procurement lead time (NEW)",
    });
    const repair = screen.getByRole("region", {
      name: "Repair cycle time (REP)",
    });
    expect(procurement).toHaveTextContent(
      "The returned REP lane does not match NEW; its evidence was withheld.",
    );
    expect(repair).toHaveTextContent(
      "The returned NEW lane does not match REP; its evidence was withheld.",
    );
    expect(procurement).not.toHaveTextContent("61.0d");
    expect(repair).not.toHaveTextContent("34.2d");
  });

  it("requests the recommendation selected by the Workbench deep link", async () => {
    const fetchMock = stubFetch([], fullTracePartContext);

    renderWithProviders(
      <PartDrillDown />,
      "/parts/19000-231-3/YYC?recommendation_id=rec-action%2F1",
    );

    await screen.findByRole("region", { name: "Planning calculation trace" });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(
        /\/parts\/19000-231-3\/YYC\?recommendation_id=rec-action%2F1$/,
      ),
      expect.anything(),
    );
  });

  it("labels reconstructed legacy evidence and withholds an exact net equation", async () => {
    stubFetch([], {
      ...samplePartContext,
      planning_trace: {
        ...fullPlanningTrace,
        calculation_source: "legacy_reconstructed",
        projection_kind: null,
        served_historical_per_day: null,
        dispatchable_available: null,
        repair_receipts_due: null,
        expected_receipts_due: null,
        net_position: null,
        shortage_before_action: null,
        pooled_group_id: null,
        members: [],
        warnings: ["Legacy calculation evidence was reconstructed."],
      },
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const trace = await screen.findByRole("region", {
      name: "Planning calculation trace",
    });
    expect(
      within(trace).getByLabelText(
        "Calculation evidence source: Legacy reconstructed",
      ),
    ).toBeInTheDocument();
    expect(
      within(trace).getByRole("note", { name: "Legacy calculation limitation" }),
    ).toHaveTextContent(/exact statistical, pooled, repair-receipt/i);
    expect(
      within(trace).queryByLabelText("Exact net-position reconciliation"),
    ).not.toBeInTheDocument();
  });

  it("renders exact pooled member contributions with accessible table labels", async () => {
    stubFetch([], {
      ...samplePartContext,
      planning_trace: {
        ...fullPlanningTrace,
        served_historical_per_day: 5 / 90,
        projected_historical_demand: 5,
        scheduled_demand_status: "partial",
        scheduled_demand_undated_lines: 1,
        scheduled_demand_undated_units: 2,
        scheduled_demand_due: 1,
        projected_demand: 6,
        dispatchable_available: 3,
        open_receipts_status: "partial",
        open_receipts_undated_lines: 1,
        open_receipts_undated_units: 4,
        open_receipts_due: 1,
        overdue_open_receipts_due: 0,
        repair_receipts_due: 1,
        expected_receipts_due: 2,
        net_position: -1,
        shortage_before_action: 1,
        pooled_group_id: "INT-GROUP-7",
        pooling_scope: "worklist_partial",
        excluded_member_keys: ["P3@YVR"],
        members: [
          {
            pn: "P1",
            location: "YYZ",
            projection_kind: "NORMAL",
            projected_historical_demand: 3,
            scheduled_demand_status: "available",
            scheduled_demand_undated_lines: 0,
            scheduled_demand_undated_units: 0,
            scheduled_demand_due: 1,
            projected_demand: 4,
            dispatchable_available: 2,
            open_receipts_status: "available",
            open_receipts_undated_lines: 0,
            open_receipts_undated_units: 0,
            open_receipts_due: 1,
            overdue_open_receipts_due: 0,
            repair_receipts_due: 1,
            expected_receipts_due: 2,
            net_position: 0,
          },
          {
            pn: "P2",
            location: "YUL",
            projection_kind: "COMPOUND_POISSON",
            projected_historical_demand: 2,
            scheduled_demand_status: "partial",
            scheduled_demand_undated_lines: 1,
            scheduled_demand_undated_units: 2,
            scheduled_demand_due: 0,
            projected_demand: 2,
            dispatchable_available: 1,
            open_receipts_status: "unavailable",
            open_receipts_undated_lines: 1,
            open_receipts_undated_units: 4,
            open_receipts_due: 0,
            overdue_open_receipts_due: 0,
            repair_receipts_due: 0,
            expected_receipts_due: 0,
            net_position: -1,
          },
        ],
      },
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const trace = await screen.findByRole("region", {
      name: "Planning calculation trace",
    });
    expect(
      within(trace).getByRole("heading", { name: "Pooled member contributions" }),
    ).toBeInTheDocument();
    expect(trace).toHaveTextContent("INT-GROUP-7");
    const table = within(trace).getByRole("table", {
      name: "Pooled member calculation contributions",
    });
    expect(within(table).getByRole("rowheader", { name: "P1 · YYZ" })).toBeInTheDocument();
    expect(within(table).getByRole("rowheader", { name: "P2 · YUL" })).toBeInTheDocument();
    expect(table).toHaveTextContent("COMPOUND_POISSON");
    expect(table).toHaveTextContent("-1 units");
    expect(
      within(trace).getByLabelText("Pooling scope: Partial worklist pool"),
    ).toBeInTheDocument();
    expect(
      within(trace).getByLabelText("Scheduled demand evidence status: Partial"),
    ).toBeInTheDocument();
    expect(
      within(trace).getByLabelText("Open receipts evidence status: Partial"),
    ).toBeInTheDocument();
    expect(
      within(trace).getByRole("note", { name: "Excluded interchange members" }),
    ).toHaveTextContent("P3@YVR");
    expect(
      within(trace).getByLabelText("Undated scheduled demand excluded"),
    ).toHaveTextContent("1 undated line · 2 units excluded");
    expect(
      within(trace).getByLabelText("Undated open receipts excluded"),
    ).toHaveTextContent("1 undated line · 4 units excluded");
    const scheduledMetric = within(trace)
      .getByText("Scheduled demand due in horizon")
      .closest('[data-testid="metric"]');
    const openReceiptMetric = within(trace)
      .getByText("Open receipts due by horizon (not guaranteed)")
      .closest('[data-testid="metric"]');
    expect(scheduledMetric).not.toBeNull();
    expect(openReceiptMetric).not.toBeNull();
    expect(
      within(scheduledMetric as HTMLElement).getByTestId("prov-chip"),
    ).toHaveAccessibleName(/Reduced confidence.*65% coverage/);
    expect(
      within(openReceiptMetric as HTMLElement).getByTestId("prov-chip"),
    ).toHaveAccessibleName(/Reduced confidence.*65% coverage/);
    expect(
      within(table).getByLabelText(
        "P2 scheduled demand evidence status: Partial",
      ),
    ).toBeInTheDocument();
    expect(
      within(table).getByLabelText(
        "P2 open receipts evidence status: Unavailable",
      ),
    ).toBeInTheDocument();
  });

  it("pairs unavailable evidence badges with zero-coverage provenance chips", async () => {
    stubFetch([], {
      ...samplePartContext,
      planning_trace: {
        ...fullPlanningTrace,
        scheduled_demand_status: "unavailable",
        scheduled_demand_due: 0,
        projected_demand: 1.8,
        dispatchable_available: 2,
        open_receipts_status: "unavailable",
        open_receipts_due: 0,
        overdue_open_receipts_due: 0,
        repair_receipts_due: 0,
        expected_receipts_due: 0,
        net_position: 0.2,
        shortage_before_action: 0,
        members: [
          {
            ...fullPlanningTrace.members![0],
            scheduled_demand_status: "unavailable",
            scheduled_demand_due: 0,
            projected_demand: 1.8,
            dispatchable_available: 2,
            open_receipts_status: "unavailable",
            open_receipts_due: 0,
            overdue_open_receipts_due: 0,
            repair_receipts_due: 0,
            expected_receipts_due: 0,
            net_position: 0.2,
          },
        ],
        warnings: [
          "Scheduled-demand evidence is unavailable; scheduled_demand_due=0 is an unavailable placeholder.",
          "Open-receipt evidence is unavailable; open_receipts_due=0 is an unavailable placeholder.",
        ],
      },
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const trace = await screen.findByRole("region", {
      name: "Planning calculation trace",
    });
    expect(
      within(trace).getByLabelText(
        "Scheduled demand evidence status: Unavailable",
      ),
    ).toBeInTheDocument();
    expect(
      within(trace).getByLabelText(
        "Open receipts evidence status: Unavailable",
      ),
    ).toBeInTheDocument();
    for (const label of [
      "Scheduled demand due in horizon",
      "Open receipts due by horizon (not guaranteed)",
    ]) {
      const metric = within(trace)
        .getByText(label)
        .closest('[data-testid="metric"]');
      expect(metric).not.toBeNull();
      expect(
        within(metric as HTMLElement).getByTestId("prov-chip"),
      ).toHaveAccessibleName(/Low confidence.*0% coverage/);
    }
  });

  it("surfaces planning warnings and identifies a bucket fallback as estimated events", async () => {
    stubFetch([], {
      ...samplePartContext,
      planning_trace: {
        ...fullPlanningTrace,
        demand_event_count: 7,
        event_count_source: "bucket_fallback",
        warnings: [
          "Demand event count uses one event per non-zero bucket for this legacy snapshot.",
        ],
      },
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const warnings = await screen.findByRole("note", { name: "Planning warnings" });
    expect(warnings).toHaveTextContent(
      "Demand event count uses one event per non-zero bucket for this legacy snapshot.",
    );
    expect(
      screen.getByLabelText("Demand event count source: Bucket fallback estimate"),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Demand event count source: Observed source events"),
    ).not.toBeInTheDocument();
  });

  it("gives the planning trace and its evidence sections accessible names", async () => {
    stubFetch([], fullTracePartContext);

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const trace = await screen.findByRole("region", { name: "Planning calculation trace" });
    expect(
      within(trace).getByRole("heading", { name: "Observation and exposure" }),
    ).toBeInTheDocument();
    expect(
      within(trace).getByRole("heading", { name: "Horizon demand and supply" }),
    ).toBeInTheDocument();
    expect(
      within(trace).getByRole("heading", { name: "Applied and binding constraints" }),
    ).toBeInTheDocument();
    expect(
      within(trace).getByLabelText("Shelf-life ceiling: binding constraint"),
    ).toBeInTheDocument();
    expect(
      within(trace).getByLabelText(
        "minimum_order_quantity_action scope: Action",
      ),
    ).toBeInTheDocument();
    expect(
      within(trace).getByRole("note", { name: "Overdue receipt reliability warning" }),
    ).toBeInTheDocument();
    for (const chip of within(trace).getAllByTestId("prov-chip")) {
      expect(chip).toHaveAccessibleName(/Source .+, updated .+, \d+% coverage\./);
    }
  });

  it("shows a loading state, then renders header, stat metrics, and provenance chips", async () => {
    stubFetch();

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);

    await waitFor(() => expect(screen.getByText("19000-231-3")).toBeInTheDocument());

    // Header
    expect(screen.getByText("YYC")).toBeInTheDocument();
    expect(screen.getByText("WATER TANK HEATER BLANKET")).toBeInTheDocument();
    expect(screen.getByTestId("criticality-badge")).toHaveTextContent("Tier 2");
    expect(screen.getByText("ATA 38")).toBeInTheDocument();

    // Stat cards — every metric goes through Metric+ProvChip
    expect(screen.getByText("Stock position")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument(); // on-hand
    expect(screen.getByText("Policy — current vs proposed")).toBeInTheDocument();
    expect(screen.getByText("Demanded units (trailing 24 months)")).toBeInTheDocument();
    expect(screen.queryByText("Need / shortage")).not.toBeInTheDocument();
    expect(screen.getByText("Unit cost")).toBeInTheDocument();
    expect(screen.getByText("$245.50")).toBeInTheDocument();
    expect(screen.getByText("Procurement lead time")).toBeInTheDocument();
    expect(screen.getByText("Repair cycle time")).toBeInTheDocument();
    expect(screen.getByText("RO cycle-time proxy")).toBeInTheDocument();
    expect(screen.getByText("Open orders")).toBeInTheDocument();
    expect(screen.getByText("PO-1")).toBeInTheDocument();

    // Provenance invariant: every stat card carries a ProvChip.
    expect(screen.getAllByTestId("prov-chip").length).toBeGreaterThanOrEqual(7);
  });

  it("renders an error state when the BFF call fails (unknown part)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        statusText: "Not Found",
        json: () => Promise.resolve({ detail: "unknown-pn/nowhere" }),
      }),
    );

    renderWithProviders(<PartDrillDown />, "/parts/unknown-pn/nowhere");

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/failed to load part/i);
  });

  it("renders empty states gracefully when demand/open orders are absent", async () => {
    const emptyContext: PartContext = {
      ...samplePartContext,
      demand: null,
      open_orders: [],
      total_open_qty: 0,
      lead_time: null,
      procurement_lead_time: undefined,
      repair_cycle_time: undefined,
      unit_cost: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/history")) return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
        return Promise.resolve({ ok: true, json: () => Promise.resolve(emptyContext) });
      }),
    );

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    await waitFor(() => expect(screen.getByText("19000-231-3")).toBeInTheDocument());

    expect(screen.getByText("No demand history for this part.")).toBeInTheDocument();
    expect(screen.getByText("No open orders.")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Procurement lead time is absent from this legacy response.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Repair cycle time is absent from this legacy response."),
    ).toBeInTheDocument();
    expect(screen.getByText("No vendor economics on record.")).toBeInTheDocument();
  });

  it("does not present unavailable open-order placeholders as observed zeros", async () => {
    stubFetch([], {
      ...samplePartContext,
      open_orders: [],
      total_open_qty: 0,
      open_orders_status: "unavailable",
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    expect(
      await screen.findByText(
        "Open-order evidence is unavailable; counts are not observed zeros.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Open-order detail is unavailable for this key."),
    ).toBeInTheDocument();
    expect(screen.queryByText("No open orders.")).not.toBeInTheDocument();
  });

  it("downgrades all historical placeholders when demand evidence is unavailable", async () => {
    stubFetch([], {
      ...samplePartContext,
      planning_trace: {
        ...fullPlanningTrace,
        observation_start: null,
        observation_end: null,
        exposure_days: 0,
        bucket: null,
        observed_periods: 0,
        zero_filled_periods: 0,
        demand_event_count: null,
        event_count_source: "unavailable",
        demanded_units: 0,
        historical_per_day: 0,
        warnings: [
          "Demand-history evidence is unavailable; historical quantities are unavailable placeholders.",
        ],
      },
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");

    const trace = await screen.findByRole("region", {
      name: "Planning calculation trace",
    });
    for (const label of [
      "Historical exposure",
      "Demanded units",
      "Raw observed historical demand rate",
    ]) {
      const metric = within(trace)
        .getByText(label)
        .closest('[data-testid="metric"]');
      expect(metric).not.toBeNull();
      expect(
        within(metric as HTMLElement).getByTestId("prov-chip"),
      ).toHaveAccessibleName(/Low confidence.*0% coverage/);
    }
  });

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

  it("surfaces a non-rolled_back rollback result and keeps the dialog open", async () => {
    stubFetch([
      { tenant_id: "acme", pn: "19000-231-3", location: "YYC", version: 1, status: "written",
        old_values: { rop: 2, eoq: 4, safety_stock: 1, max_stock: 6 },
        new_values: { rop: 3, eoq: 5, safety_stock: 2, max_stock: 8 },
        provenance_id: "p", tier: 2, agent_version: "v1", changed_by_principal: "agent-spine",
        idempotency_key: null, parent_version: null, changed_at: "2026-06-20T00:00:00Z" },
    ]);
    // BFF accepts the request but refuses with nothing_to_revert + null error_message.
    vi.spyOn(bffClient, "rollback").mockResolvedValue({
      tenant_id: "acme", pn: "19000-231-3", location: "YYC", status: "nothing_to_revert",
      from_values: null, to_values: null, reverted_from_version: null, new_version: null,
      rolled_back_at: null, error_message: null,
    });

    renderWithProviders(<PartDrillDown />, "/parts/19000-231-3/YYC");
    await userEvent.click(await screen.findByRole("button", { name: /roll back/i }));
    await userEvent.type(screen.getByLabelText(/reason/i), "try");
    await userEvent.click(screen.getByRole("button", { name: /confirm rollback/i }));

    // The mapped message appears (not a silent open dialog) and the dialog stays open.
    expect(await screen.findByText(/nothing to roll back/i)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
