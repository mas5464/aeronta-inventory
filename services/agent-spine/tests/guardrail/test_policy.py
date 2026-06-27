from trax_io_reco.contracts.enums import AutonomyTier

from trax_io_spine.contracts import GuardrailStatus
from trax_io_spine.guardrail.policy import AutonomyConfig, BandAutonomyPolicy


def test_advisor_always_queues() -> None:
    p = BandAutonomyPolicy()
    assert p.authorize(tier=AutonomyTier.ADVISOR, delta_pct=0.0, criticality_tier=5) == (
        GuardrailStatus.QUEUED_FOR_APPROVAL
    )


def test_autonomous_within_band_and_low_criticality_approves() -> None:
    p = BandAutonomyPolicy()
    assert p.authorize(tier=AutonomyTier.AUTONOMOUS, delta_pct=0.5, criticality_tier=4) == (
        GuardrailStatus.APPROVED_FOR_WRITE
    )


def test_autonomous_critical_part_queues() -> None:
    p = BandAutonomyPolicy()  # criticality 3 < min 4
    assert p.authorize(tier=AutonomyTier.AUTONOMOUS, delta_pct=0.1, criticality_tier=3) == (
        GuardrailStatus.QUEUED_FOR_APPROVAL
    )


def test_bounded_band_is_tighter_than_autonomous() -> None:
    p = BandAutonomyPolicy(AutonomyConfig(bounded_max_delta_pct=0.25, autonomous_max_delta_pct=1.0))
    # delta 0.4 is inside autonomous band but outside bounded band
    assert p.authorize(tier=AutonomyTier.BOUNDED, delta_pct=0.4, criticality_tier=5) == (
        GuardrailStatus.QUEUED_FOR_APPROVAL
    )
    assert p.authorize(tier=AutonomyTier.AUTONOMOUS, delta_pct=0.4, criticality_tier=5) == (
        GuardrailStatus.APPROVED_FOR_WRITE
    )
