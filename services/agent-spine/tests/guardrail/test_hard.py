from trax_io_reco.contracts.enums import AogRiskLevel

from tests.conftest import make_current, make_policy
from trax_io_spine.guardrail.hard import (
    aog_forces_advisor,
    compute_delta_pct,
    hard_guardrail_violations,
)


def test_delta_pct_zero_when_no_current() -> None:
    assert compute_delta_pct(make_policy(), None) == 0.0


def test_delta_pct_is_max_relative_change() -> None:
    policy = make_policy(rop=10, eoq=5, safety_stock=4, max_stock=30)  # max 20 -> 30 = +50%
    delta = compute_delta_pct(policy, make_current(max_stock=20))
    assert delta == 0.5


def test_violation_when_delta_exceeds_100pct(make_rec) -> None:
    rec = make_rec()
    assert hard_guardrail_violations(rec, delta_pct=1.5) == ("delta_exceeds_100pct",)
    assert hard_guardrail_violations(rec, delta_pct=0.9) == ()


def test_aog_high_forces_advisor(make_rec) -> None:
    assert aog_forces_advisor(make_rec(aog_risk_level=AogRiskLevel.HIGH)) is True
    assert aog_forces_advisor(make_rec(aog_risk_level=AogRiskLevel.LOW)) is False
