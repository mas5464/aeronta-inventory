from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from trax_io_reco.candidate.integration import (
    candidate_from_finalized_recommendations,
    no_change_from_finalized_recommendation,
)
from trax_io_reco.contracts.candidate import CandidateTargetLevels
from trax_io_reco.contracts.enums import (
    AogRiskLevel,
    AutonomyTier,
    EvidenceKind,
    PolicyKind,
    RecommendationType,
)
from trax_io_reco.contracts.policy import AppliedConstraint, PolicyRecommendation
from trax_io_reco.contracts.recommendation import (
    CalculationEvidence,
    CalculationMemberEvidence,
    Evidence,
    Recommendation,
)

_FRONTIER_ID = "frontier_" + ("a" * 64)


def _trace() -> CalculationEvidence:
    member = CalculationMemberEvidence(
        pn="PN-1",
        location="MIA",
        projection_kind="COMPOUND_POISSON",
        projected_historical_demand=6,
        scheduled_demand_due=0,
        projected_demand=6,
        dispatchable_available=0,
        open_receipts_due=0,
        overdue_open_receipts_due=0,
        repair_receipts_due=0,
        expected_receipts_due=0,
        net_position=-6,
    )
    return CalculationEvidence(
        as_of=date(2026, 1, 31),
        horizon_days=30,
        projection_kind="COMPOUND_POISSON",
        served_historical_per_day=0.2,
        projected_historical_demand=6,
        scheduled_demand_due=0,
        projected_demand=6,
        dispatchable_available=0,
        open_receipts_due=0,
        overdue_open_receipts_due=0,
        repair_receipts_due=0,
        expected_receipts_due=0,
        net_position=-6,
        shortage_before_action=6,
        members=(member,),
    )


def _recommendation(
    *,
    recommendation_id: str = "rec-purchase",
    recommendation_type: RecommendationType = RecommendationType.PURCHASE,
    quantity: float = 4,
    recommended_location: str | None = None,
    constraints: tuple[AppliedConstraint, ...] = (),
    policy: PolicyRecommendation | None = None,
) -> Recommendation:
    return Recommendation(
        recommendation_id=recommendation_id,
        tenant_id="tenant-a",
        type=recommendation_type,
        part_number="PN-1",
        description="Part",
        current_location="MIA",
        recommended_location=recommended_location,
        current_stock=0,
        projected_demand=6,
        shortage_quantity=6,
        recommended_quantity=quantity,
        estimated_cost_impact=Decimal("9999"),
        aog_risk_level=AogRiskLevel.HIGH,
        criticality_tier=1,
        reason=f"Finalized {recommendation_type.value} option",
        supporting_evidence=(
            Evidence(
                kind=EvidenceKind.DEMAND_HISTORY,
                ref_id="PN-1@MIA",
                detail="Six units projected",
            ),
        ),
        confidence_score=0.8,
        horizon_days=30,
        suggested_autonomy_tier=AutonomyTier.ADVISOR,
        generated_at=datetime(2026, 1, 31, 12, 0),
        input_snapshot_hash="snapshot-exact-trace",
        policy=policy,
        applied_constraints=constraints,
        calculation_evidence=_trace(),
    )


def test_purchase_candidate_recomputes_post_arbitration_economics(
    current_levels,
    economics,
    model_identity,
) -> None:
    recommendation = _recommendation(
        constraints=(
            AppliedConstraint(
                name="minimum_order_quantity_action",
                value="3",
                binding=False,
                source="vendor_economics.minimum_order_qty",
                scope="action",
            ),
        )
    )
    candidate = candidate_from_finalized_recommendations(
        frontier_id=_FRONTIER_ID,
        recommendations=(recommendation,),
        model_identity=model_identity,
        current_levels=current_levels,
        target_levels=current_levels,
        economics=economics,
        expected_aog_risk=Decimal("0.4"),
        purchase_unit_cost=Decimal("12"),
    )

    assert candidate.candidate_kind == "purchase"
    assert candidate.reconciliation.purchase_quantity == 4
    assert candidate.lifecycle_costs.acquisition_cash == Decimal("48")
    assert candidate.lifecycle_costs.acquisition_cash != recommendation.estimated_cost_impact
    assert candidate.outcome.expected_shortage == 2
    action_constraint = next(
        item
        for item in candidate.constraints
        if item.constraint_id == "minimum_order_quantity_action"
    )
    assert action_constraint.scope == "action"


def test_transfer_and_residual_purchase_are_one_selectable_candidate(
    current_levels,
    economics,
    model_identity,
) -> None:
    purchase = _recommendation(quantity=4)
    transfer = _recommendation(
        recommendation_id="rec-transfer",
        recommendation_type=RecommendationType.TRANSFER,
        quantity=2,
        recommended_location="JFK",
        constraints=(
            AppliedConstraint(
                name="donor_dispatchable_excess_limit",
                value="2",
                binding=True,
                source="donor_stock:JFK",
                scope="action",
            ),
        ),
    )
    candidate = candidate_from_finalized_recommendations(
        frontier_id=_FRONTIER_ID,
        recommendations=(purchase, transfer),
        model_identity=model_identity,
        current_levels=current_levels,
        target_levels=current_levels,
        economics=economics,
        expected_aog_risk=Decimal("0.1"),
        purchase_unit_cost=Decimal("12"),
    )

    assert candidate.candidate_kind == "transfer_purchase"
    assert candidate.reconciliation.transfer_in_quantity == 2
    assert candidate.reconciliation.purchase_quantity == 4
    assert candidate.outcome.ending_net_position == 0
    assert candidate.lifecycle_costs.acquisition_cash == Decimal("48")


def test_no_change_uses_trace_but_not_action_constraints(
    current_levels,
    economics,
    model_identity,
) -> None:
    recommendation = _recommendation(
        constraints=(
            AppliedConstraint(
                name="minimum_order_quantity_action",
                value="3",
                binding=True,
                source="vendor_economics.minimum_order_qty",
                scope="action",
            ),
        )
    )
    baseline = no_change_from_finalized_recommendation(
        frontier_id=_FRONTIER_ID,
        recommendation=recommendation,
        model_identity=model_identity,
        current_levels=current_levels,
        economics=economics,
        expected_aog_risk=Decimal("0.8"),
    )

    assert baseline.is_no_change
    assert baseline.outcome.expected_shortage == 6
    assert baseline.lifecycle_costs.acquisition_cash == 0
    assert "minimum_order_quantity_action" not in {
        item.constraint_id for item in baseline.constraints
    }


def test_adjustment_target_must_match_finalized_policy(
    current_levels,
    economics,
    model_identity,
) -> None:
    policy = PolicyRecommendation(
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        rop=5,
        eoq=2,
        safety_stock=2,
        max_stock=7,
        policy_kind=PolicyKind.S_S,
        provenance_id="policy-id",
        model_id="deterministic-v1",
    )
    adjustment = _recommendation(
        recommendation_type=RecommendationType.ADJUST_MIN_MAX,
        quantity=7,
        policy=policy,
    )
    with pytest.raises(ValueError, match="target_levels"):
        candidate_from_finalized_recommendations(
            frontier_id=_FRONTIER_ID,
            recommendations=(adjustment,),
            model_identity=model_identity,
            current_levels=current_levels,
            target_levels=current_levels,
            economics=economics,
            expected_aog_risk=Decimal("0.5"),
        )

    target = CandidateTargetLevels(rop=5, eoq=2, safety_stock=2, max_stock=7)
    candidate = candidate_from_finalized_recommendations(
        frontier_id=_FRONTIER_ID,
        recommendations=(adjustment,),
        model_identity=model_identity,
        current_levels=current_levels,
        target_levels=target,
        economics=economics,
        expected_aog_risk=Decimal("0.5"),
    )
    assert candidate.candidate_kind == "adjust_policy"
    assert candidate.target_levels == target
    assert candidate.action_quantity == 0


def test_random_recommendation_metadata_does_not_change_candidate_identity(
    current_levels,
    economics,
    model_identity,
) -> None:
    first = _recommendation(recommendation_id="random-one")
    second = _recommendation(recommendation_id="random-two").model_copy(
        update={"generated_at": datetime(2030, 1, 1, 1, 2, 3)}
    )
    kwargs = {
        "frontier_id": _FRONTIER_ID,
        "model_identity": model_identity,
        "current_levels": current_levels,
        "target_levels": current_levels,
        "economics": economics,
        "expected_aog_risk": Decimal("0.4"),
        "purchase_unit_cost": Decimal("12"),
    }
    one = candidate_from_finalized_recommendations(
        recommendations=(first,),
        **kwargs,
    )
    two = candidate_from_finalized_recommendations(
        recommendations=(second,),
        **kwargs,
    )
    assert one == two
