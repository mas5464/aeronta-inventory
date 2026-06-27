"""CedarAutonomyPolicy band matrix — real cedarpy (skips without the `cedar` extra)."""

from __future__ import annotations

import pytest

pytest.importorskip("cedarpy")

from trax_io_reco.contracts.enums import AutonomyTier  # noqa: E402

from trax_io_spine.contracts import GuardrailStatus  # noqa: E402
from trax_io_spine.guardrail.cedar import CedarAutonomyPolicy  # noqa: E402

APPROVED = GuardrailStatus.APPROVED_FOR_WRITE
QUEUED = GuardrailStatus.QUEUED_FOR_APPROVAL


@pytest.fixture
def policy() -> CedarAutonomyPolicy:
    return CedarAutonomyPolicy()  # loads the packaged autonomy_bands.cedar


def test_advisor_always_queues(policy: CedarAutonomyPolicy) -> None:
    assert policy.authorize(tier=AutonomyTier.ADVISOR, delta_pct=0.0, criticality_tier=5) == QUEUED


def test_autonomous_in_band_low_criticality_approves(policy: CedarAutonomyPolicy) -> None:
    # crit 4 (>=4) and 20% (<=40%) -> approved
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=0.20, criticality_tier=4
    ) == APPROVED


def test_autonomous_critical_part_queues(policy: CedarAutonomyPolicy) -> None:
    # crit 3 fails the >=4 floor -> queued
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=0.10, criticality_tier=3
    ) == QUEUED


def test_autonomous_out_of_band_queues(policy: CedarAutonomyPolicy) -> None:
    # 60% exceeds the 40% band -> queued
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=0.60, criticality_tier=5
    ) == QUEUED


def test_autonomous_exact_band_edge_approves(policy: CedarAutonomyPolicy) -> None:
    # 40% -> 4000 bps == ceiling -> approved (<=)
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=0.40, criticality_tier=4
    ) == APPROVED


def test_bounded_band_is_tighter(policy: CedarAutonomyPolicy) -> None:
    # crit 3, 10% (<=15%) -> approved under bounded; 20% (>15%) -> queued
    assert policy.authorize(
        tier=AutonomyTier.BOUNDED, delta_pct=0.10, criticality_tier=3
    ) == APPROVED
    assert policy.authorize(
        tier=AutonomyTier.BOUNDED, delta_pct=0.20, criticality_tier=3
    ) == QUEUED


def test_tier1_flight_safety_never_autowrites(policy: CedarAutonomyPolicy) -> None:
    # criticality_tier 1 matches no permit on either action -> queued
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=0.0, criticality_tier=1
    ) == QUEUED
    assert policy.authorize(
        tier=AutonomyTier.BOUNDED, delta_pct=0.0, criticality_tier=1
    ) == QUEUED


def test_over_100pct_is_forbidden(policy: CedarAutonomyPolicy) -> None:
    # 150% -> 15000 bps > 10000 -> forbid overrides any permit -> queued
    assert policy.authorize(
        tier=AutonomyTier.AUTONOMOUS, delta_pct=1.50, criticality_tier=5
    ) == QUEUED
