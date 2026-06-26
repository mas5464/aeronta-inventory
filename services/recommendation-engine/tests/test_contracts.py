from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trax_io_reco.contracts.enums import (
    AogRiskLevel,
    AutonomyTier,
    CanonicalCriticality,
    EvidenceKind,
    ForecastHorizon,
    PolicyKind,
    RecommendationType,
    Regime,
)
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.contracts.recommendation import Evidence, Recommendation


# --------------------------------------------------------------------------- #
# Enum mirror pins (spec §5.1)
# --------------------------------------------------------------------------- #
def test_regime_values() -> None:
    assert [r.value for r in Regime] == [
        "ultra_rare",
        "intermittent",
        "moderate",
        "high_volume",
    ]


def test_forecast_horizon_named_members() -> None:
    assert ForecastHorizon.DAYS_30 == 30
    assert ForecastHorizon.DAYS_180 == 180
    assert {m.name: m.value for m in ForecastHorizon} == {
        "DAYS_30": 30,
        "DAYS_60": 60,
        "DAYS_90": 90,
        "DAYS_180": 180,
    }


def test_criticality_ordered() -> None:
    assert CanonicalCriticality.TIER_1 < CanonicalCriticality.TIER_2
    assert CanonicalCriticality.TIER_4 <= CanonicalCriticality.TIER_5


def test_policy_kind_values() -> None:
    assert {k.value for k in PolicyKind} == {"base_stock", "s_S", "R_Q"}


def test_autonomy_tier_values() -> None:
    assert AutonomyTier.ADVISOR == 1 and AutonomyTier.AUTONOMOUS == 3


def test_recommendation_type_count() -> None:
    assert len(RecommendationType) == 5


# --------------------------------------------------------------------------- #
# PolicyRecommendation mirror + validator (spec §5.1 / §6.2)
# --------------------------------------------------------------------------- #
def _pr(**kw: object) -> PolicyRecommendation:
    base: dict[str, object] = dict(
        tenant_id="t",
        pn="P",
        location="L",
        rop=10,
        eoq=5,
        safety_stock=8,
        max_stock=15,
        policy_kind=PolicyKind.S_S,
        provenance_id="prov",
    )
    base.update(kw)
    return PolicyRecommendation(**base)  # type: ignore[arg-type]


def test_policy_default_model_id_is_stub() -> None:
    assert _pr().model_id == "stub"


def test_policy_validator_rop_ge_ss() -> None:
    with pytest.raises(ValidationError):
        _pr(rop=5, safety_stock=8)


def test_policy_validator_max_ge_rop_plus_eoq() -> None:
    with pytest.raises(ValidationError):
        _pr(rop=10, eoq=5, max_stock=14)


def test_policy_is_frozen() -> None:
    pr = _pr()
    with pytest.raises(ValidationError):
        pr.rop = 99  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Recommendation contract (spec §5.2)
# --------------------------------------------------------------------------- #
def test_recommendation_requires_description_and_evidence() -> None:
    rec = Recommendation(
        recommendation_id="01J",
        tenant_id="t",
        type=RecommendationType.PURCHASE,
        part_number="P",
        description="WIDGET",
        current_location="L",
        recommended_location=None,
        current_stock=0,
        projected_demand=5.0,
        shortage_quantity=5.0,
        recommended_quantity=5.0,
        estimated_cost_impact=Decimal("500"),
        aog_risk_level=AogRiskLevel.LOW,
        reason="net<0",
        confidence_score=0.7,
        supporting_evidence=(
            Evidence(kind=EvidenceKind.OPEN_ORDER, ref_id="O1", detail="short", as_of=None),
        ),
        horizon_days=90,
        suggested_autonomy_tier=AutonomyTier.BOUNDED,
        guardrail_flags=(),
        generated_at=datetime(2026, 4, 17),
        input_snapshot_hash="h",
        policy=None,
        current_policy=None,
    )
    assert rec.description == "WIDGET"
    assert rec.supporting_evidence


def test_recommendation_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        Recommendation(
            recommendation_id="01J",
            tenant_id="t",
            type=RecommendationType.PURCHASE,
            part_number="P",
            description="W",
            current_location="L",
            current_stock=0,
            projected_demand=5.0,
            shortage_quantity=5.0,
            recommended_quantity=5.0,
            estimated_cost_impact=Decimal("500"),
            aog_risk_level=AogRiskLevel.LOW,
            reason="r",
            confidence_score=1.5,
            supporting_evidence=(
                Evidence(kind=EvidenceKind.OPEN_ORDER, ref_id="O1", detail="d"),
            ),
            horizon_days=90,
            suggested_autonomy_tier=AutonomyTier.BOUNDED,
            generated_at=datetime(2026, 4, 17),
            input_snapshot_hash="h",
        )
