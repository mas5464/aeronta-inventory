from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

from trax_io_feature_store import InMemoryFeatureStore, TenantContext

from tests.fixtures.builders import seed_part
from trax_io_reco.contracts.context import ScheduledDemandItem
from trax_io_reco.contracts.enums import EvidenceKind, RecommendationType, Regime
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.demand.projection import HistoricalScheduledProjector
from trax_io_reco.policy.mini_engine import MiniPolicyEngine
from trax_io_reco.position.net_position import net_position
from trax_io_reco.recommenders.adjust_min_max import AdjustMinMaxRecommender
from trax_io_reco.recommenders.base import DonorOption, RecommenderInput
from trax_io_reco.recommenders.purchase import PurchaseRecommender
from trax_io_reco.recommenders.reduce_sell import ReduceSellRecommender
from trax_io_reco.recommenders.transfer import TransferRecommender

TENANT = TenantContext(tenant_id="acme")
NOW = datetime(2026, 4, 17, 9, 0, 0)
AS_OF = date(2026, 4, 17)


def make_input(*, regime, donors=None, reporting=30, **seed_kw) -> RecommenderInput:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", **seed_kw)
    ctx = ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )
    proj = HistoricalScheduledProjector().project(context=ctx, regime=regime)
    policy = MiniPolicyEngine().recommend(context=ctx, regime=regime, projection=proj)
    assert isinstance(policy, PolicyRecommendation)
    return RecommenderInput(
        context=ctx,
        projection=proj,
        policy=policy,
        now=NOW,
        as_of=AS_OF,
        input_snapshot_hash="h",
        reporting_horizon_days=reporting,
        net_position=lambda w: net_position(
            context=ctx, projection=proj, window_days=w, as_of=AS_OF
        ),
        donor_lookup=lambda pn, gid, mwh: donors or [],
    )


def assert_structural_constraints_preserved(rec, policy) -> None:
    by_name = {constraint.name: constraint for constraint in rec.applied_constraints}
    for constraint in policy.applied_constraints:
        if constraint.name == "open_order_deferral":
            continue
        assert by_name[constraint.name] == constraint
        assert by_name[constraint.name].scope == "policy"
    if rec.type == RecommendationType.ADJUST_MIN_MAX:
        assert by_name["open_order_deferral"].scope == "action"
    else:
        assert "open_order_deferral" not in by_name
        assert "open_order_deferral" not in rec.guardrail_flags


def test_scenario1_demand_exceeds_stock_purchase() -> None:
    inp = make_input(
        regime=Regime.HIGH_VOLUME,
        monthly_units=[20] * 12,
        serviceable=2,
        lead_mean_days=90.0,
        current_policy=(5, 5, 2, 40),
    )
    recs = PurchaseRecommender().propose(inp)
    assert len(recs) == 1 and recs[0].type == RecommendationType.PURCHASE
    assert recs[0].shortage_quantity > 0 and recs[0].recommended_quantity > 0
    assert recs[0].horizon_days == 90  # protection period, not the 30d reporting window
    assert_structural_constraints_preserved(recs[0], inp.policy)


def test_transfer_action_constraints_are_traceable() -> None:
    inp = make_input(
        regime=Regime.HIGH_VOLUME,
        monthly_units=[20] * 12,
        serviceable=0,
        lead_mean_days=60.0,
        current_policy=(5, 5, 2, 40),
        donors=[
            DonorOption(
                location="DONOR",
                serviceable_excess=2,
                lead_days=3.0,
                cost=0.0,
            )
        ],
    )

    recommendation = TransferRecommender().propose(inp)[0]
    constraints = {item.name: item for item in recommendation.applied_constraints}

    assert constraints["donor_dispatchable_excess_limit"].scope == "action"
    assert constraints["donor_dispatchable_excess_limit"].value == "2"
    assert constraints["donor_dispatchable_excess_limit"].binding is True
    assert constraints["transfer_lead_not_slower_than_purchase"].scope == "action"
    assert constraints["transfer_lead_not_slower_than_purchase"].binding is False


def test_scenario6_open_po_covers_suppresses_purchase() -> None:
    inp = make_input(
        regime=Regime.HIGH_VOLUME,
        monthly_units=[20] * 12,
        serviceable=2,
        lead_mean_days=90.0,
        current_policy=(5, 5, 2, 40),
        open_qty=80,
        open_rcv_date=date(2026, 5, 1),
    )
    assert PurchaseRecommender().propose(inp) == []


def test_scenario2_transfer_preferred() -> None:
    donors = [DonorOption(location="YOW", serviceable_excess=10, lead_days=3.0, cost=0.0)]
    inp = make_input(
        regime=Regime.HIGH_VOLUME,
        monthly_units=[20] * 12,
        serviceable=0,
        lead_mean_days=90.0,
        current_policy=(5, 5, 2, 40),
        donors=donors,
    )
    recs = TransferRecommender().propose(inp)
    assert len(recs) == 1 and recs[0].type == RecommendationType.TRANSFER
    assert recs[0].recommended_location == "YOW"
    assert_structural_constraints_preserved(recs[0], inp.policy)


def test_scenario3_high_value_unused_reduce_or_sell() -> None:
    inp = make_input(
        regime=Regime.ULTRA_RARE,
        monthly_units=[0] * 12,
        serviceable=100,
        unit_cost="8000",
        current_policy=(2, 2, 1, 10),
        scheduled=[],
    )
    recs = ReduceSellRecommender().propose(inp)
    assert len(recs) == 1
    assert recs[0].type in (RecommendationType.SELL, RecommendationType.REDUCE_STOCK)
    assert recs[0].estimated_cost_impact < 0  # holding released = savings
    assert_structural_constraints_preserved(recs[0], inp.policy)


def test_reduce_sell_does_not_treat_unavailable_history_as_zero_usage() -> None:
    inp = make_input(
        regime=Regime.ULTRA_RARE,
        monthly_units=[],
        serviceable=100,
        unit_cost="8000",
        current_policy=(2, 2, 1, 10),
    )

    assert ReduceSellRecommender().propose(inp) == []


def test_reduce_sell_suppresses_disposal_when_scheduled_demand_is_unavailable() -> None:
    inp = make_input(
        regime=Regime.ULTRA_RARE,
        monthly_units=[0] * 12,
        serviceable=100,
        unit_cost="8000",
        current_policy=(2, 2, 1, 10),
    )

    assert inp.context.scheduled_demand_status == "unavailable"
    assert ReduceSellRecommender().propose(inp) == []


def test_reduce_sell_suppresses_disposal_when_scheduled_demand_is_partial() -> None:
    inp = make_input(
        regime=Regime.ULTRA_RARE,
        monthly_units=[0] * 12,
        serviceable=100,
        unit_cost="8000",
        current_policy=(2, 2, 1, 10),
        scheduled=[],
    )
    partial = RecommenderInput(
        **{
            **inp.__dict__,
            "context": inp.context.model_copy(
                update={"scheduled_demand_status": "partial"}
            ),
        }
    )

    assert ReduceSellRecommender().propose(partial) == []


def test_scenario4_adjust_min_max() -> None:
    inp = make_input(
        regime=Regime.HIGH_VOLUME,
        monthly_units=[30] * 12,
        serviceable=5,
        current_policy=(1, 1, 0, 2),
    )
    recs = AdjustMinMaxRecommender().propose(inp)
    assert len(recs) == 1 and recs[0].type == RecommendationType.ADJUST_MIN_MAX
    assert recs[0].policy is not None and recs[0].current_policy is not None
    assert recs[0].policy.max_stock != recs[0].current_policy.max_stock
    assert_structural_constraints_preserved(recs[0], inp.policy)


def test_adjust_and_reduce_recommendations_include_scheduled_boundary_units() -> None:
    adjust_scheduled = [
        ScheduledDemandItem(
            due_date=date(2026, 5, 8),  # inclusive 21-day lead horizon
            qty=7,
            source_ref="ADJUST-BOUNDARY",
            source_kind=EvidenceKind.TASK_CARD,
        )
    ]
    adjust_inp = make_input(
        regime=Regime.HIGH_VOLUME,
        monthly_units=[30] * 12,
        serviceable=5,
        current_policy=(1, 1, 0, 2),
        scheduled=adjust_scheduled,
    )
    adjust = AdjustMinMaxRecommender().propose(adjust_inp)[0]
    assert adjust.projected_demand == (
        adjust_inp.projection.historical_component * adjust.horizon_days + 7
    )

    reduce_scheduled = [
        ScheduledDemandItem(
            due_date=date(2026, 5, 17),  # inclusive 30-day reporting horizon
            qty=3,
            source_ref="REDUCE-BOUNDARY",
            source_kind=EvidenceKind.TASK_CARD,
        )
    ]
    reduce_inp = make_input(
        regime=Regime.ULTRA_RARE,
        monthly_units=[0] * 12,
        serviceable=100,
        unit_cost="8000",
        current_policy=(2, 2, 1, 10),
        scheduled=reduce_scheduled,
    )
    reduce = ReduceSellRecommender().propose(reduce_inp)[0]
    assert reduce.projected_demand == 3.0


def test_open_order_deferral_uses_actual_action_horizon() -> None:
    inp = make_input(
        regime=Regime.HIGH_VOLUME,
        monthly_units=[30] * 12,
        serviceable=5,
        current_policy=(1, 1, 0, 2),
        lead_mean_days=90.0,
        open_qty=1000,
        open_rcv_date=date(2026, 6, 16),  # outside reporting 30d; inside action 90d
        reporting=30,
    )

    recommendation = AdjustMinMaxRecommender().propose(inp)[0]
    deferral = next(
        constraint
        for constraint in recommendation.applied_constraints
        if constraint.name == "open_order_deferral"
    )

    assert recommendation.horizon_days == 90
    assert deferral.value == "1005"
    assert deferral.binding is True
    assert "open_order_deferral" in recommendation.guardrail_flags


def test_adjust_horizon_is_never_shorter_than_reporting_horizon() -> None:
    inp = make_input(
        regime=Regime.HIGH_VOLUME,
        monthly_units=[30] * 12,
        serviceable=5,
        current_policy=(1, 1, 0, 2),
        lead_mean_days=21.0,
        reporting=60,
    )

    recommendation = AdjustMinMaxRecommender().propose(inp)[0]

    assert recommendation.horizon_days == 60
    assert recommendation.calculation_evidence is not None
    assert recommendation.calculation_evidence.horizon_days == 60


def test_missing_open_order_coverage_conservatively_defers_policy_write() -> None:
    inp = make_input(
        regime=Regime.HIGH_VOLUME,
        monthly_units=[30] * 12,
        serviceable=20,
        current_policy=(1, 1, 0, 2),
    )
    context = inp.context.model_copy(update={"open_orders": None})
    missing_orders = replace(
        inp,
        context=context,
        net_position=lambda window: net_position(
            context=context,
            projection=inp.projection,
            window_days=window,
            as_of=AS_OF,
        ),
    )

    recommendation = AdjustMinMaxRecommender().propose(missing_orders)[0]
    constraint = next(
        item
        for item in recommendation.applied_constraints
        if item.name == "open_order_deferral"
    )

    assert constraint.scope == "action"
    assert constraint.value is None
    assert constraint.binding is True
    assert constraint.source == "open_orders_snapshot:unavailable"
    assert "open_order_deferral" in recommendation.guardrail_flags


def test_purchase_evidence_does_not_present_unavailable_receipts_as_observed_zero() -> None:
    inp = make_input(
        regime=Regime.HIGH_VOLUME,
        monthly_units=[20] * 12,
        serviceable=0,
        lead_mean_days=60.0,
        current_policy=(5, 5, 2, 40),
    )
    context = inp.context.model_copy(update={"open_orders": None})
    missing_orders = replace(
        inp,
        context=context,
        net_position=lambda window: net_position(
            context=context,
            projection=inp.projection,
            window_days=window,
            as_of=AS_OF,
        ),
    )

    recommendation = PurchaseRecommender().propose(missing_orders)[0]
    order_evidence = next(
        item
        for item in recommendation.supporting_evidence
        if item.kind == EvidenceKind.OPEN_ORDER
    )

    assert order_evidence.ref_id == "coverage=unavailable"
    assert "not presented as an observed absence" in order_evidence.detail


def test_calculation_evidence_uses_exact_single_key_boundary_arithmetic() -> None:
    scheduled = [
        ScheduledDemandItem(
            due_date=AS_OF,
            qty=2,
            source_ref="TODAY",
            source_kind=EvidenceKind.TASK_CARD,
        ),
        ScheduledDemandItem(
            due_date=date(2026, 5, 17),
            qty=3,
            source_ref="HORIZON-END",
            source_kind=EvidenceKind.TASK_CARD,
        ),
        ScheduledDemandItem(
            due_date=date(2026, 5, 18),
            qty=100,
            source_ref="OUTSIDE",
            source_kind=EvidenceKind.TASK_CARD,
        ),
    ]
    inp = make_input(
        regime=Regime.HIGH_VOLUME,
        monthly_units=[30] * 12,
        serviceable=4,
        allocated=1,
        in_repair=50,
        current_policy=(5, 5, 2, 40),
        lead_mean_days=30.0,
        open_qty=2,
        open_rcv_date=date(2026, 5, 17),
        scheduled=scheduled,
    )

    recommendation = PurchaseRecommender().propose(inp)[0]
    evidence = recommendation.calculation_evidence

    assert evidence is not None
    assert evidence.as_of == AS_OF
    assert evidence.horizon_days == 30
    assert evidence.projection_kind == inp.projection.dist_kind
    assert evidence.projected_historical_demand == (inp.projection.historical_component * 30)
    assert evidence.scheduled_demand_due == 5.0
    assert evidence.projected_demand == (
        evidence.projected_historical_demand + evidence.scheduled_demand_due
    )
    assert evidence.dispatchable_available == 3.0
    assert evidence.open_receipts_due == 2.0
    assert evidence.overdue_open_receipts_due == 0.0
    assert evidence.repair_receipts_due == 0.0
    assert evidence.expected_receipts_due == 2.0
    assert evidence.net_position == (
        evidence.dispatchable_available + evidence.expected_receipts_due - evidence.projected_demand
    )
    assert evidence.shortage_before_action == max(0.0, -evidence.net_position)
    assert evidence.pooled_group_id is None
    assert len(evidence.members) == 1
    assert evidence.members[0].pn == "P"
    assert evidence.members[0].location == "L"
    assert evidence.members[0].model_dump() == {
        "pn": "P",
        "location": "L",
        "projection_kind": inp.projection.dist_kind,
        "projected_historical_demand": evidence.projected_historical_demand,
        "scheduled_demand_due": 5.0,
        "projected_demand": evidence.projected_demand,
        "dispatchable_available": 3.0,
        "open_receipts_due": 2.0,
        "overdue_open_receipts_due": 0.0,
        "repair_receipts_due": 0.0,
        "expected_receipts_due": 2.0,
        "net_position": evidence.net_position,
        "scheduled_demand_status": "available",
        "scheduled_demand_undated_lines": 0,
        "scheduled_demand_undated_units": 0,
        "open_receipts_status": "available",
        "open_receipts_undated_lines": 0,
        "open_receipts_undated_units": 0,
    }
