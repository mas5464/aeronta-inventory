from datetime import UTC, date, datetime
from types import SimpleNamespace

from trax_io_feature_store.schemas import (
    DemandHistory,
    DemandObservation,
    OpenOrder,
    OpenOrdersSnapshot,
)
from trax_io_reco.contracts.context import ScheduledDemandItem
from trax_io_reco.contracts.enums import EvidenceKind
from trax_io_reco.contracts.recommendation import (
    CalculationEvidence,
    CalculationMemberEvidence,
)

from trax_io_spine.bff.planning_trace import build_planning_trace


def test_trace_uses_inclusive_horizon_and_discloses_overdue_open_receipts():
    history = DemandHistory(
        tenant_id="acme",
        pn="P1",
        location="YYZ",
        observation_start=date(2026, 1, 1),
        observation_end=date(2026, 1, 10),
        bucket="day",
        event_count_source="observed",
        observations=[
            DemandObservation(
                bucket="day",
                period_start=date(2026, 1, 1),
                removals=5,
                removal_events=1,
                issue_events=0,
            )
        ],
        extract_date=date(2026, 4, 1),
    )
    scheduled = (
        ScheduledDemandItem(
            due_date=date(2026, 3, 31),
            qty=50,
            source_ref="before",
            source_kind=EvidenceKind.WORK_ORDER,
        ),
        ScheduledDemandItem(
            due_date=date(2026, 4, 1),
            qty=2,
            source_ref="start",
            source_kind=EvidenceKind.WORK_ORDER,
        ),
        ScheduledDemandItem(
            due_date=date(2026, 4, 11),
            qty=3,
            source_ref="end",
            source_kind=EvidenceKind.WORK_ORDER,
        ),
        ScheduledDemandItem(
            due_date=date(2026, 4, 12),
            qty=50,
            source_ref="after",
            source_kind=EvidenceKind.WORK_ORDER,
        ),
    )
    open_orders = OpenOrdersSnapshot(
        tenant_id="acme",
        pn="P1",
        location="YYZ",
        snapshot_at=datetime(2026, 4, 1, tzinfo=UTC),
        orders=[
            OpenOrder(
                order_id="overdue",
                order_type="PO",
                qty_open=2,
                expected_rcv_date=date(2026, 3, 31),
            ),
            OpenOrder(
                order_id="start",
                order_type="PO",
                qty_open=3,
                expected_rcv_date=date(2026, 4, 1),
            ),
            OpenOrder(
                order_id="end",
                order_type="PO",
                qty_open=4,
                expected_rcv_date=date(2026, 4, 11),
            ),
            OpenOrder(
                order_id="after",
                order_type="PO",
                qty_open=50,
                expected_rcv_date=date(2026, 4, 12),
            ),
            OpenOrder(order_id="undated", order_type="RO", qty_open=7),
        ],
        total_open_qty=66,
        extract_date=date(2026, 4, 1),
    )
    recommendation = SimpleNamespace(
        generated_at=datetime(2026, 4, 1, tzinfo=UTC),
        horizon_days=10,
        projected_demand=10.0,
        policy=SimpleNamespace(),
        applied_constraints=(
            SimpleNamespace(
                name="Shelf-life ceiling",
                value="12 units",
                binding=True,
                source="eMRO Part Master",
            ),
            SimpleNamespace(
                name="minimum_order_quantity_action",
                value="5 units",
                binding=True,
                source="Vendor economics",
                scope="action",
            ),
        ),
    )

    trace = build_planning_trace(
        demand_history=history,
        recommendation=recommendation,
        scheduled_demand=scheduled,
        open_orders=open_orders,
    )

    assert trace.as_of == "2026-04-01"
    assert trace.horizon_end == "2026-04-11"
    assert trace.exposure_days == 10
    assert trace.demand_event_count == 1
    assert trace.demanded_units == 5
    assert trace.historical_per_day == 0.5
    assert trace.projected_historical_demand == 5.0
    assert trace.scheduled_demand_due == 5
    assert trace.open_receipts_due == 9
    assert trace.overdue_open_receipts_due == 2
    assert trace.constraints[0].binding is True
    assert trace.constraints[0].scope == "policy"
    assert trace.constraints[1].scope == "action"
    assert trace.calculation_source == "legacy_reconstructed"
    assert any("overdue" in warning.lower() for warning in trace.warnings)
    assert any("expected receipt date" in warning.lower() for warning in trace.warnings)


def test_trace_warns_when_legacy_exposure_is_inferred_from_observed_span():
    history = DemandHistory(
        tenant_id="acme",
        pn="P1",
        location="YYZ",
        observations=[
            DemandObservation(
                bucket="month",
                period_start=date(2026, 1, 1),
                removals=2,
            )
        ],
        extract_date=date(2026, 2, 1),
    )
    recommendation = SimpleNamespace(
        generated_at=datetime(2026, 2, 1, tzinfo=UTC),
        horizon_days=1,
        projected_demand=2 / 31,
        policy=None,
        applied_constraints=(),
    )
    open_orders = OpenOrdersSnapshot(
        tenant_id="acme",
        pn="P1",
        location="YYZ",
        snapshot_at=datetime(2026, 2, 1, tzinfo=UTC),
        orders=[],
        total_open_qty=0,
        extract_date=date(2026, 2, 1),
    )

    trace = build_planning_trace(
        demand_history=history,
        recommendation=recommendation,
        scheduled_demand=(),
        open_orders=open_orders,
    )

    assert trace.observation_start == "2026-01-01"
    assert trace.observation_end == "2026-01-31"
    assert trace.exposure_days == 31
    assert trace.event_count_source == "bucket_fallback"
    assert any(
        "configured observation bounds" in warning.lower()
        and "inferred" in warning.lower()
        for warning in trace.warnings
    )


def test_legacy_constraint_inputs_do_not_invent_a_non_binding_state():
    history = DemandHistory(
        tenant_id="acme",
        pn="P1",
        location="YYZ",
        observation_start=date(2026, 1, 1),
        observation_end=date(2026, 1, 31),
        bucket="month",
        event_count_source="observed",
        observations=[],
        extract_date=date(2026, 2, 1),
    )
    recommendation = SimpleNamespace(
        generated_at=datetime(2026, 2, 1, tzinfo=UTC),
        horizon_days=30,
        projected_demand=0.0,
        policy=SimpleNamespace(
            rop=2,
            eoq=4,
            safety_stock=1,
            max_stock=6,
        ),
    )

    trace = build_planning_trace(
        demand_history=history,
        recommendation=recommendation,
        scheduled_demand=(),
        open_orders=OpenOrdersSnapshot(
            tenant_id="acme",
            pn="P1",
            location="YYZ",
            snapshot_at=datetime(2026, 2, 1, tzinfo=UTC),
            orders=[],
            total_open_qty=0,
            extract_date=date(2026, 2, 1),
        ),
        vendor_economics=SimpleNamespace(minimum_order_qty=5),
    )

    assert trace.constraints == ()
    assert any(
        "no applied or binding state was inferred" in warning.lower()
        for warning in trace.warnings
    )


def test_exact_carrier_preserves_statistical_served_projection_not_raw_rate():
    history = DemandHistory(
        tenant_id="acme",
        pn="P1",
        location="YYZ",
        observation_start=date(2026, 1, 1),
        observation_end=date(2026, 1, 10),
        bucket="day",
        event_count_source="observed",
        observations=[
            DemandObservation(
                bucket="day",
                period_start=date(2026, 1, 1),
                removals=5,
                removal_events=1,
                issue_events=0,
            )
        ],
        extract_date=date(2026, 4, 1),
    )
    member = CalculationMemberEvidence(
        pn="P1",
        location="YYZ",
        projection_kind="NBD",
        projected_historical_demand=6.0,
        scheduled_demand_status="available",
        scheduled_demand_due=2.0,
        projected_demand=8.0,
        dispatchable_available=4.0,
        open_receipts_status="available",
        open_receipts_due=5.0,
        overdue_open_receipts_due=2.0,
        repair_receipts_due=1.0,
        expected_receipts_due=6.0,
        net_position=2.0,
    )
    evidence = CalculationEvidence(
        as_of=date(2026, 4, 1),
        horizon_days=3,
        projection_kind="NBD",
        served_historical_per_day=2.0,
        projected_historical_demand=6.0,
        scheduled_demand_status="available",
        scheduled_demand_due=2.0,
        projected_demand=8.0,
        dispatchable_available=4.0,
        open_receipts_status="available",
        open_receipts_due=5.0,
        overdue_open_receipts_due=2.0,
        repair_receipts_due=1.0,
        expected_receipts_due=6.0,
        net_position=2.0,
        shortage_before_action=0.0,
        members=(member,),
    )
    recommendation = SimpleNamespace(
        generated_at=datetime(2099, 1, 1, tzinfo=UTC),
        horizon_days=999,
        projected_demand=999.0,
        policy=None,
        applied_constraints=(),
        calculation_evidence=evidence,
    )

    trace = build_planning_trace(
        demand_history=history,
        recommendation=recommendation,
        # Deliberately different sources prove that exact served quantities are
        # copied from CalculationEvidence instead of reconstructed downstream.
        scheduled_demand=(),
        open_orders=None,
    )

    assert trace.calculation_source == "served_calculation"
    assert trace.as_of == "2026-04-01"
    assert trace.horizon_days == 3
    assert trace.horizon_end == "2026-04-04"
    assert trace.historical_per_day == 0.5
    assert trace.served_historical_per_day == 2.0
    assert trace.projection_kind == "NBD"
    assert trace.projected_historical_demand == 6.0
    assert trace.scheduled_demand_due == 2.0
    assert trace.projected_demand == 8.0
    assert trace.open_receipts_due == 5.0
    assert trace.repair_receipts_due == 1.0
    assert trace.expected_receipts_due == 6.0
    assert trace.net_position == 2.0
    assert trace.shortage_before_action == 0.0
    assert len(trace.members) == 1
    assert not any("does not reconcile" in warning.lower() for warning in trace.warnings)


def test_exact_carrier_exposes_pooled_member_contributions_and_group_arithmetic():
    members = (
        CalculationMemberEvidence(
            pn="P1",
            location="YYZ",
            projection_kind="NORMAL",
            projected_historical_demand=3.0,
            scheduled_demand_status="available",
            scheduled_demand_due=1.0,
            projected_demand=4.0,
            dispatchable_available=2.0,
            open_receipts_status="available",
            open_receipts_due=1.0,
            overdue_open_receipts_due=0.0,
            repair_receipts_due=1.0,
            expected_receipts_due=2.0,
            net_position=0.0,
        ),
        CalculationMemberEvidence(
            pn="P2",
            location="YUL",
            projection_kind="COMPOUND_POISSON",
            projected_historical_demand=2.0,
            scheduled_demand_status="partial",
            scheduled_demand_undated_lines=1,
            scheduled_demand_undated_units=2,
            scheduled_demand_due=0.0,
            projected_demand=2.0,
            dispatchable_available=1.0,
            open_receipts_status="partial",
            open_receipts_undated_lines=1,
            open_receipts_undated_units=4,
            open_receipts_due=0.0,
            overdue_open_receipts_due=0.0,
            repair_receipts_due=0.0,
            expected_receipts_due=0.0,
            net_position=-1.0,
        ),
    )
    evidence = CalculationEvidence(
        as_of=date(2026, 7, 28),
        horizon_days=30,
        projection_kind="POOLED",
        served_historical_per_day=5 / 30,
        projected_historical_demand=5.0,
        scheduled_demand_status="partial",
        scheduled_demand_undated_lines=1,
        scheduled_demand_undated_units=2,
        scheduled_demand_due=1.0,
        projected_demand=6.0,
        dispatchable_available=3.0,
        open_receipts_status="partial",
        open_receipts_undated_lines=1,
        open_receipts_undated_units=4,
        open_receipts_due=1.0,
        overdue_open_receipts_due=0.0,
        repair_receipts_due=1.0,
        expected_receipts_due=2.0,
        net_position=-1.0,
        shortage_before_action=1.0,
        pooled_group_id="INT-GROUP-7",
        pooling_scope="worklist_partial",
        excluded_member_keys=("P3@YVR",),
        members=members,
    )

    trace = build_planning_trace(
        demand_history=None,
        recommendation=SimpleNamespace(
            calculation_evidence=evidence,
            applied_constraints=(),
            policy=None,
        ),
        scheduled_demand=None,
        open_orders=None,
    )

    assert trace.pooled_group_id == "INT-GROUP-7"
    assert [(member.pn, member.location) for member in trace.members] == [
        ("P1", "YYZ"),
        ("P2", "YUL"),
    ]
    assert sum(member.projected_demand for member in trace.members) == trace.projected_demand
    assert (
        sum(member.expected_receipts_due for member in trace.members)
        == trace.expected_receipts_due
    )
    assert sum(member.net_position for member in trace.members) == trace.net_position
    assert trace.scheduled_demand_status == "partial"
    assert trace.scheduled_demand_undated_lines == 1
    assert trace.scheduled_demand_undated_units == 2
    assert trace.open_receipts_status == "partial"
    assert trace.open_receipts_undated_lines == 1
    assert trace.open_receipts_undated_units == 4
    assert trace.pooling_scope == "worklist_partial"
    assert trace.excluded_member_keys == ("P3@YVR",)
    assert any(
        "scheduled-demand evidence is partial" in warning.lower()
        for warning in trace.warnings
    )
    assert any("open-receipt evidence is partial" in warning.lower() for warning in trace.warnings)
    assert any("limited to the current worklist" in warning.lower() for warning in trace.warnings)


def test_exact_carrier_qualifies_unavailable_zero_source_quantities():
    member = CalculationMemberEvidence(
        pn="P1",
        location="YYZ",
        projection_kind="NORMAL",
        projected_historical_demand=1,
        scheduled_demand_status="unavailable",
        scheduled_demand_due=0,
        projected_demand=1,
        dispatchable_available=1,
        open_receipts_status="unavailable",
        open_receipts_due=0,
        overdue_open_receipts_due=0,
        repair_receipts_due=0,
        expected_receipts_due=0,
        net_position=0,
    )
    evidence = CalculationEvidence(
        as_of=date(2026, 7, 28),
        horizon_days=1,
        projection_kind="NORMAL",
        served_historical_per_day=1,
        projected_historical_demand=1,
        scheduled_demand_status="unavailable",
        scheduled_demand_due=0,
        projected_demand=1,
        dispatchable_available=1,
        open_receipts_status="unavailable",
        open_receipts_due=0,
        overdue_open_receipts_due=0,
        repair_receipts_due=0,
        expected_receipts_due=0,
        net_position=0,
        shortage_before_action=0,
        members=(member,),
    )

    trace = build_planning_trace(
        demand_history=None,
        recommendation=SimpleNamespace(
            calculation_evidence=evidence,
            applied_constraints=(),
            policy=None,
        ),
    )

    assert trace.scheduled_demand_status == "unavailable"
    assert trace.open_receipts_status == "unavailable"
    assert any(
        "scheduled_demand_due=0 is an unavailable placeholder" in warning
        for warning in trace.warnings
    )
    assert any(
        "open_receipts_due=0 is an unavailable placeholder" in warning
        for warning in trace.warnings
    )


def test_carrierless_recommendation_is_explicitly_legacy_reconstructed():
    recommendation = SimpleNamespace(
        generated_at=datetime(2026, 4, 1, tzinfo=UTC),
        horizon_days=10,
        projected_demand=7.0,
        policy=None,
        applied_constraints=(),
    )

    trace = build_planning_trace(
        demand_history=None,
        recommendation=recommendation,
        scheduled_demand=None,
        open_orders=None,
    )

    assert trace.calculation_source == "legacy_reconstructed"
    assert trace.projection_kind is None
    assert trace.served_historical_per_day is None
    assert trace.dispatchable_available is None
    assert trace.repair_receipts_due is None
    assert trace.expected_receipts_due is None
    assert trace.net_position is None
    assert trace.members == ()
    assert any("legacy_reconstructed" in warning for warning in trace.warnings)
