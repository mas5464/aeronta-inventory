"""Shared fixtures: a tenant + a Recommendation factory with sensible defaults."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from trax_io_feature_store import TenantContext
from trax_io_feature_store.schemas import CurrentPolicy
from trax_io_reco.contracts.context import CurrentPolicy as RecoCurrentPolicy
from trax_io_reco.contracts.enums import (
    AogRiskLevel,
    AutonomyTier,
    PolicyKind,
    RecommendationType,
)
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.contracts.recommendation import Recommendation

from trax_io_spine.bff.models import TaskStatus
from trax_io_spine.contracts import GuardrailStatus
from trax_io_spine.guardrail.enforce import GuardrailEnforcer


@pytest.fixture
def tenant() -> TenantContext:
    return TenantContext(tenant_id="acme")


def make_policy(**over: object) -> PolicyRecommendation:
    base: dict[str, object] = dict(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        rop=10, eoq=5, safety_stock=4, max_stock=20,
        policy_kind=PolicyKind.S_S, provenance_id="prov-1", model_id="deterministic-v1",
    )
    base.update(over)
    return PolicyRecommendation(**base)  # type: ignore[arg-type]


def make_current(**over: object) -> CurrentPolicy:
    from datetime import date

    base: dict[str, object] = dict(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        rop=10, eoq=5, safety_stock=4, max_stock=20,
        replenishment_lead_days=21.0, extract_date=date(2026, 4, 1),
    )
    base.update(over)
    return CurrentPolicy(**base)  # type: ignore[arg-type]


@pytest.fixture
def make_rec():
    def _make(**over: object) -> Recommendation:
        policy = over.pop("policy", make_policy())
        fs_current = over.pop("current_policy", make_current())
        # Recommendation.current_policy expects trax_io_reco.contracts.context.CurrentPolicy,
        # not the feature-store variant. Bridge by copying the shared fields.
        reco_current: RecoCurrentPolicy | None = (
            RecoCurrentPolicy(
                rop=fs_current.rop,
                eoq=fs_current.eoq,
                safety_stock=fs_current.safety_stock,
                max_stock=fs_current.max_stock,
                replenishment_lead_days=fs_current.replenishment_lead_days,
            )
            if fs_current is not None
            else None
        )
        base: dict[str, object] = dict(
            recommendation_id="r-1", tenant_id="acme", type=RecommendationType.ADJUST_MIN_MAX,
            part_number="PN-A", description="widget", current_location="LOC-1",
            current_stock=12, projected_demand=3.0, shortage_quantity=0.0,
            recommended_quantity=0.0, estimated_cost_impact=0, aog_risk_level=AogRiskLevel.NONE,
            criticality_tier=4, reason="test", supporting_evidence=(),
            confidence_score=0.8, horizon_days=30, suggested_autonomy_tier=AutonomyTier.AUTONOMOUS,
            guardrail_flags=(), generated_at=datetime.now(UTC), input_snapshot_hash="hash",
            policy=policy, current_policy=reco_current,
        )
        base.update(over)
        return Recommendation(**base)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def seed_pending_recommendations(make_rec):
    """Insert deterministic, policy-bearing pending rows for queue/action tests.

    The committed extract truthfully contains recommendations deferred by the
    open-order guardrail. Tests for pagination, sorting, approval, and writeback
    should not weaken that product behavior merely to manufacture pending rows.
    Instead they can opt into these explicit guardrail-safe fixtures.
    """

    def _seed(store, *, count: int = 3) -> tuple[str, ...]:
        specs = (
            {
                "recommendation_id": "fixture-pending-1",
                "aog_risk_level": AogRiskLevel.NONE,
                "confidence_score": 0.2,
                "criticality_tier": 5,
                "estimated_cost_impact": 100,
                "recommended_quantity": 1.0,
            },
            {
                "recommendation_id": "fixture-pending-2",
                "aog_risk_level": AogRiskLevel.HIGH,
                "confidence_score": 0.7,
                "criticality_tier": 2,
                "estimated_cost_impact": 400,
                "recommended_quantity": 4.0,
            },
            {
                "recommendation_id": "fixture-pending-3",
                "aog_risk_level": AogRiskLevel.LOW,
                "confidence_score": 0.9,
                "criticality_tier": 4,
                "estimated_cost_impact": 50,
                "recommended_quantity": 2.0,
            },
        )
        if not 1 <= count <= len(specs):
            raise ValueError(f"count must be between 1 and {len(specs)}")

        ids: list[str] = []
        for spec in specs[:count]:
            rec = make_rec(
                **spec,
                suggested_autonomy_tier=AutonomyTier.ADVISOR,
            )
            outcome = GuardrailEnforcer().enforce(rec)
            assert outcome.status is GuardrailStatus.QUEUED_FOR_APPROVAL
            store._ingest(rec, outcome)
            assert store.detail(rec.recommendation_id).status is TaskStatus.PENDING
            ids.append(rec.recommendation_id)
        return tuple(ids)

    return _seed
