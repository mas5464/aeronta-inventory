from decimal import Decimal

from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType

from trax_io_spine.bff.models import (
    BulkApproveFilter,
    KillSwitchState,
    QueueRow,
    RejectReason,
    RejectRequest,
    TaskStatus,
)


def test_queue_row_round_trips():
    row = QueueRow(
        recommendation_id="r1", pn="P1", location="YYZ", type=RecommendationType.PURCHASE,
        criticality_tier=2, aog_risk_level=AogRiskLevel.LOW, confidence_score=0.8,
        recommended_quantity=4.0, estimated_cost_impact=Decimal("1200.50"),
        tier=AutonomyTier.BOUNDED, priority_score=12.5, status=TaskStatus.PENDING,
        reason="queued: cost delta exceeds band",
    )
    assert QueueRow.model_validate_json(row.model_dump_json()) == row


def test_reject_request_defaults_and_enum():
    r = RejectRequest(reason=RejectReason.WRONG_FOR_FLEET)
    assert r.detail == ""
    assert r.reason.value == "wrong_for_fleet"


def test_bulk_filter_all_optional():
    f = BulkApproveFilter()
    assert f.tiers is None and f.max_delta_pct is None and f.criticality_min is None


def test_kill_switch_state():
    assert KillSwitchState(engaged=True).engaged is True
