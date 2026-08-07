from decimal import Decimal
from math import inf, nan

import pytest
from pydantic import ValidationError
from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType

from trax_io_spine.bff.models import (
    BulkApproveFilter,
    KillSwitchState,
    PartContext,
    PlanningMemberTraceView,
    PlanningTraceView,
    QueueRow,
    RejectReason,
    RejectRequest,
    SupplyCycleLaneView,
    TaskStatus,
)


def test_queue_row_round_trips():
    row = QueueRow(
        recommendation_id="r1", pn="P1", location="YYZ", type=RecommendationType.PURCHASE,
        criticality_tier=2, aog_risk_level=AogRiskLevel.LOW, confidence_score=0.8,
        recommended_quantity=4.0, estimated_cost_impact=Decimal("1200.50"),
        tier=AutonomyTier.BOUNDED, priority_score=12.5, status=TaskStatus.PENDING,
        reason="queued: cost delta exceeds band", approvable=True,
        description="Hydraulic pump assembly", current_stock=5, shortage_quantity=2.5,
        recommended_location="LAX", horizon_days=30,
    )
    assert QueueRow.model_validate_json(row.model_dump_json()) == row
    assert row.approvable is True


def test_reject_request_defaults_and_enum():
    r = RejectRequest(reason=RejectReason.WRONG_FOR_FLEET)
    assert r.detail == ""
    assert r.reason.value == "wrong_for_fleet"


def test_bulk_filter_all_optional():
    f = BulkApproveFilter()
    assert f.tiers is None and f.max_delta_pct is None and f.criticality_min is None


def test_kill_switch_state():
    assert KillSwitchState(engaged=True).engaged is True


def test_part_context_accepts_legacy_payload_without_planning_trace():
    """The additive trace must not invalidate already-persisted PG/snapshot JSON."""
    legacy = {
        "pn": "P1",
        "location": "YYZ",
        "attributes": {
            "description": "Legacy part",
            "ata_chapter": None,
            "part_class": None,
            "shelf_life_days": None,
            "hazardous_material": False,
            "tool_control_item": False,
            "criticality_tier": None,
        },
        "stock": None,
        "current_policy": None,
        "proposed_policy": None,
        "lead_time": None,
        "open_orders": [],
        "total_open_qty": 0,
        "demand": None,
        "unit_cost": None,
    }

    context = PartContext.model_validate(legacy)

    assert context.planning_trace.event_count_source == "unavailable"
    assert context.planning_trace.demand_event_count is None
    assert context.planning_trace.warnings
    assert context.candidate_frontier is None
    assert context.procurement_lead_time.condition == "NEW"
    assert context.procurement_lead_time.status == "unavailable"
    assert context.procurement_lead_time.mean_days is None
    assert context.procurement_lead_time.unavailable_reason
    assert context.repair_cycle_time.condition == "REP"
    assert context.repair_cycle_time.status == "unavailable"
    assert context.repair_cycle_time.mean_days is None
    assert context.repair_cycle_time.unavailable_reason
    assert context.repair_pipeline is None
    assert context.repair_return_profile is None


def test_supply_cycle_lane_rejects_metrics_disguised_as_unavailable():
    with pytest.raises(ValidationError, match="cannot carry metrics"):
        SupplyCycleLaneView(
            condition="NEW",
            status="unavailable",
            mean_days=10,
            unavailable_reason="missing",
        )


def test_supply_cycle_lane_pins_observed_repair_proxy_label():
    with pytest.raises(ValidationError, match="RO cycle-time proxy"):
        SupplyCycleLaneView(
            condition="REP",
            status="observed",
            mean_days=10,
            p50_days=9,
            p90_days=14,
            p99_days=18,
            n_observations=4,
            source="order_plan_closed_orders",
            grouping_level="part_condition",
            confidence="medium",
            data_cutoff="2026-04-01",
            model_version="supply-cycle-v1",
            classification_source="explicit_order_type",
            proxy_definition="order_creation_to_last_receipt",
            proxy_label="Configured repair promise",
        )


def test_planning_trace_bucket_is_the_shared_wire_literal():
    assert PlanningTraceView(bucket="month").bucket == "month"
    with pytest.raises(ValidationError):
        PlanningTraceView(bucket="quarter")


@pytest.mark.parametrize(
    "values",
    [
        {"open_receipts_due": 1, "overdue_open_receipts_due": 2},
        {
            "observation_start": "2026-01-02",
            "observation_end": "2026-01-01",
            "exposure_days": 0,
        },
        {
            "observation_start": "2026-01-01",
            "observation_end": "2026-01-02",
            "exposure_days": 1,
        },
        {
            "as_of": "2026-04-02",
            "horizon_end": "2026-04-01",
            "horizon_days": 0,
        },
        {"observation_start": "2026-01-01"},
        {"observation_end": "2026-01-01"},
        {"as_of": "2026-04-01"},
        {"horizon_end": "2026-04-01"},
        {"as_of": "2026-04-01T12:00:00", "horizon_end": "2026-04-01"},
        {"horizon_days": -1},
    ],
)
def test_planning_trace_rejects_inconsistent_wire_invariants(values):
    with pytest.raises(ValidationError):
        PlanningTraceView(**values)


def test_planning_trace_accepts_inclusive_date_bounds():
    trace = PlanningTraceView(
        observation_start="2026-01-01",
        observation_end="2026-01-02",
        exposure_days=2,
        as_of="2026-04-01",
        horizon_end="2026-04-01",
        horizon_days=0,
        open_receipts_due=2,
        overdue_open_receipts_due=2,
    )
    assert trace.exposure_days == 2


@pytest.mark.parametrize("value", [nan, inf, -inf])
def test_planning_trace_rejects_non_finite_numbers(value):
    with pytest.raises(ValidationError):
        PlanningTraceView(historical_per_day=value)

    with pytest.raises(ValidationError):
        PlanningMemberTraceView(
            pn="P1",
            location="YYZ",
            projection_kind="NORMAL",
            projected_historical_demand=0,
            scheduled_demand_due=0,
            projected_demand=0,
            dispatchable_available=0,
            open_receipts_due=0,
            overdue_open_receipts_due=0,
            repair_receipts_due=0,
            expected_receipts_due=0,
            net_position=value,
        )


def test_planning_trace_rejects_served_rate_projection_mismatch():
    member = PlanningMemberTraceView(
        pn="P1",
        location="YYZ",
        projection_kind="NORMAL",
        projected_historical_demand=2,
        scheduled_demand_due=0,
        projected_demand=2,
        dispatchable_available=0,
        open_receipts_due=2,
        overdue_open_receipts_due=0,
        repair_receipts_due=0,
        expected_receipts_due=2,
        net_position=0,
    )

    with pytest.raises(
        ValidationError,
        match="served rate times horizon_days",
    ):
        PlanningTraceView(
            calculation_source="served_calculation",
            as_of="2026-04-01",
            horizon_end="2026-04-04",
            horizon_days=3,
            projection_kind="NORMAL",
            served_historical_per_day=1,
            projected_historical_demand=2,
            scheduled_demand_due=0,
            projected_demand=2,
            dispatchable_available=0,
            open_receipts_due=2,
            overdue_open_receipts_due=0,
            repair_receipts_due=0,
            expected_receipts_due=2,
            net_position=0,
            shortage_before_action=0,
            members=(member,),
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"pooled_group_id": "G"},
            "single_key pooling requires exactly one member",
        ),
        (
            {"pooling_scope": "worklist_partial", "pooled_group_id": "G"},
            "worklist_partial pooling requires excluded_member_keys",
        ),
        (
            {
                "pooling_scope": "worklist_partial",
                "pooled_group_id": "G",
                "excluded_member_keys": ("P1@YYZ",),
            },
            "included and excluded member keys must be disjoint",
        ),
        (
            {
                "pooling_scope": "worklist_partial",
                "pooled_group_id": "G",
                "excluded_member_keys": ("P2@YUL", "P2@YUL"),
            },
            "excluded_member_keys must be unique",
        ),
        (
            {"pooling_scope": "complete_group", "pooled_group_id": "G"},
            "complete_group pooling requires multiple member contributions",
        ),
    ],
)
def test_planning_trace_mirrors_structural_pool_invariants(overrides, message):
    member = PlanningMemberTraceView(
        pn="P1",
        location="YYZ",
        projection_kind="NORMAL",
        projected_historical_demand=1,
        scheduled_demand_due=0,
        projected_demand=1,
        dispatchable_available=1,
        open_receipts_due=0,
        overdue_open_receipts_due=0,
        repair_receipts_due=0,
        expected_receipts_due=0,
        net_position=0,
    )
    values = {
        "calculation_source": "served_calculation",
        "as_of": "2026-04-01",
        "horizon_end": "2026-04-02",
        "horizon_days": 1,
        "projection_kind": "NORMAL",
        "served_historical_per_day": 1,
        "projected_historical_demand": 1,
        "scheduled_demand_due": 0,
        "projected_demand": 1,
        "dispatchable_available": 1,
        "open_receipts_due": 0,
        "overdue_open_receipts_due": 0,
        "repair_receipts_due": 0,
        "expected_receipts_due": 0,
        "net_position": 0,
        "shortage_before_action": 0,
        "members": (member,),
        **overrides,
    }

    with pytest.raises(ValidationError, match=message):
        PlanningTraceView(**values)


def test_planning_trace_rejects_duplicate_included_member_keys():
    member = PlanningMemberTraceView(
        pn="P1",
        location="YYZ",
        projection_kind="NORMAL",
        projected_historical_demand=1,
        scheduled_demand_due=0,
        projected_demand=1,
        dispatchable_available=1,
        open_receipts_due=0,
        overdue_open_receipts_due=0,
        repair_receipts_due=0,
        expected_receipts_due=0,
        net_position=0,
    )

    with pytest.raises(ValidationError, match="member keys must be unique"):
        PlanningTraceView(
            calculation_source="served_calculation",
            as_of="2026-04-01",
            horizon_end="2026-04-02",
            horizon_days=1,
            projection_kind="POOLED",
            served_historical_per_day=2,
            projected_historical_demand=2,
            scheduled_demand_due=0,
            projected_demand=2,
            dispatchable_available=2,
            open_receipts_due=0,
            overdue_open_receipts_due=0,
            repair_receipts_due=0,
            expected_receipts_due=0,
            net_position=0,
            shortage_before_action=0,
            pooled_group_id="G",
            pooling_scope="complete_group",
            members=(member, member),
        )
