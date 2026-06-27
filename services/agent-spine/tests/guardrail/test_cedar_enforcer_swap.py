"""GuardrailEnforcer with CedarAutonomyPolicy injected — proves the Protocol swap (real cedarpy)."""

from __future__ import annotations

import pytest

pytest.importorskip("cedarpy")

from trax_io_reco.contracts.enums import AutonomyTier  # noqa: E402

from tests.conftest import make_current, make_policy  # noqa: E402
from trax_io_spine.contracts import GuardrailStatus  # noqa: E402
from trax_io_spine.guardrail.cedar import CedarAutonomyPolicy  # noqa: E402
from trax_io_spine.guardrail.enforce import GuardrailEnforcer  # noqa: E402


def _enforcer() -> GuardrailEnforcer:
    return GuardrailEnforcer(policy=CedarAutonomyPolicy())


def test_cedar_enforcer_approves_autonomous_in_band(make_rec) -> None:
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=4,
        policy=make_policy(max_stock=23), current_policy=make_current(max_stock=20),  # +15%
    )
    out = _enforcer().enforce(rec)
    assert out.status is GuardrailStatus.APPROVED_FOR_WRITE


def test_cedar_enforcer_queues_critical_part(make_rec) -> None:
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=2,  # < 4 floor
        policy=make_policy(max_stock=22), current_policy=make_current(max_stock=20),  # +10%
    )
    out = _enforcer().enforce(rec)
    assert out.status is GuardrailStatus.QUEUED_FOR_APPROVAL
    assert out.approval_task is not None


def test_cedar_enforcer_rejects_hard_floor_breach(make_rec) -> None:
    # delta > 100% is rejected by the code-level hard guardrail BEFORE Cedar is consulted.
    rec = make_rec(
        suggested_autonomy_tier=AutonomyTier.AUTONOMOUS, criticality_tier=5,
        policy=make_policy(rop=10, eoq=5, safety_stock=4, max_stock=60),  # 20 -> 60 = +200%
        current_policy=make_current(max_stock=20),
    )
    out = _enforcer().enforce(rec)
    assert out.status is GuardrailStatus.REJECTED_HARD_GUARDRAIL
