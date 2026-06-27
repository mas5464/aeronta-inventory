from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier

from tests.conftest import make_current, make_policy
from trax_io_spine.contracts import GuardrailStatus
from trax_io_spine.guardrail.enforce import GuardrailEnforcer


def test_approves_autonomous_low_criticality_in_band(make_rec) -> None:
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=4,
        policy=make_policy(max_stock=23), current_policy=make_current(max_stock=20),  # +15%
    )
    out = GuardrailEnforcer().enforce(rec)
    assert out.status is GuardrailStatus.APPROVED_FOR_WRITE
    assert out.approval_task is None


def test_rejects_when_delta_exceeds_cap(make_rec) -> None:
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=5,
        policy=make_policy(rop=10, safety_stock=4, eoq=5, max_stock=60),  # 20 -> 60 = +200%
        current_policy=make_current(max_stock=20),
    )
    out = GuardrailEnforcer().enforce(rec)
    assert out.status is GuardrailStatus.REJECTED_HARD_GUARDRAIL
    assert "delta_exceeds_100pct" in out.reasons


def test_aog_forces_queue_even_when_in_band(make_rec) -> None:
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=4,
        aog_risk_level=AogRiskLevel.CRITICAL,
        policy=make_policy(max_stock=21), current_policy=make_current(max_stock=20),
    )
    out = GuardrailEnforcer().enforce(rec)
    assert out.status is GuardrailStatus.QUEUED_FOR_APPROVAL
    assert out.tier is AutonomyTier.ADVISOR
    assert out.approval_task is not None


def test_non_policy_recommendation_queues(make_rec) -> None:
    rec = make_rec(policy=None, current_policy=None)
    out = GuardrailEnforcer().enforce(rec)
    assert out.status is GuardrailStatus.QUEUED_FOR_APPROVAL
    assert "non_policy_recommendation" in out.reasons
