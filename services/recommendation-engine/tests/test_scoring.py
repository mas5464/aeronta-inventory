from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from trax_io_feature_store import InMemoryFeatureStore, TenantContext

from tests.fixtures.builders import seed_part
from trax_io_reco.arbitration import arbitrate
from trax_io_reco.confidence import confidence_score
from trax_io_reco.contracts.context import NetPosition, RepairTat
from trax_io_reco.contracts.enums import (
    AogRiskLevel,
    AutonomyTier,
    EvidenceKind,
    RecommendationType,
    Regime,
)
from trax_io_reco.contracts.recommendation import Evidence, Recommendation
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.ranking import rank, suggest_tier
from trax_io_reco.risk.aog import AogRiskScorer, _recovery_time_days

TENANT = TenantContext(tenant_id="acme")
NOW = datetime(2026, 4, 17, 9, 0, 0)
AS_OF = date(2026, 4, 17)


def _rec(rtype, *, shortage=0.0, qty=0.0, cost="0", aog=AogRiskLevel.NONE, part="P", loc="L"):
    return Recommendation(
        recommendation_id="x", tenant_id="acme", type=rtype, part_number=part, description="W",
        current_location=loc, recommended_location=("YOW" if rtype == RecommendationType.TRANSFER else None),
        current_stock=0, projected_demand=10.0, shortage_quantity=shortage, recommended_quantity=qty,
        estimated_cost_impact=Decimal(cost), aog_risk_level=aog, reason="r",
        supporting_evidence=(Evidence(kind=EvidenceKind.DEMAND_HISTORY, ref_id="e", detail="d"),),
        confidence_score=1.0, horizon_days=30, suggested_autonomy_tier=AutonomyTier.BOUNDED,
        generated_at=NOW, input_snapshot_hash="h",
    )


def _net(shortage: float) -> NetPosition:
    return NetPosition(pn="P", location="L", group_id=None, window_days=30, available=0.0,
                       expected_receipts_in_window=0.0, projected_demand=shortage,
                       net=-shortage, shortage=shortage)


# ---- Arbitration (spec §7.5) ---- #
def test_arbitration_transfer_fully_covers_drops_purchase() -> None:
    recs = [_rec(RecommendationType.TRANSFER, shortage=5, qty=5),
            _rec(RecommendationType.PURCHASE, shortage=5, qty=5)]
    out = arbitrate(recs, net=_net(5))
    assert [r.type for r in out] == [RecommendationType.TRANSFER]


def test_arbitration_partial_transfer_leaves_residual_purchase() -> None:
    recs = [_rec(RecommendationType.TRANSFER, shortage=5, qty=3),
            _rec(RecommendationType.PURCHASE, shortage=5, qty=5)]
    out = arbitrate(recs, net=_net(5))
    purchase = [r for r in out if r.type == RecommendationType.PURCHASE][0]
    assert purchase.recommended_quantity == 2.0


def test_arbitration_shortage_drops_reduce_sell() -> None:
    recs = [_rec(RecommendationType.SELL, qty=10), _rec(RecommendationType.PURCHASE, shortage=5, qty=5)]
    out = arbitrate(recs, net=_net(5))
    assert all(r.type != RecommendationType.SELL for r in out)


# ---- AOG (spec §7.7) ---- #
def _ctx(**kw):
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", **kw)
    return ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )


def test_aog_rotable_long_tat_is_high_or_critical() -> None:
    ctx = _ctx(monthly_units=[0, 1], tier=2, part_class="rotable",
               repair_tat=RepairTat(mean_days=40.0, p90_days=60.0, n_observations=8))
    rec = _rec(RecommendationType.PURCHASE, shortage=3, qty=3)
    scored = AogRiskScorer().score(rec, context=ctx, net=_net(3))
    assert scored.aog_risk_level >= AogRiskLevel.HIGH
    assert "EXPEDITE" in scored.reason


def test_aog_recovery_time_by_part_class() -> None:
    rotable = _ctx(monthly_units=[1], part_class="rotable",
                   repair_tat=RepairTat(mean_days=40.0, p90_days=60.0, n_observations=8),
                   lead_mean_days=21.0)
    expendable = _ctx(monthly_units=[1], part_class="expendable",
                      repair_tat=RepairTat(mean_days=40.0, p90_days=60.0, n_observations=8),
                      lead_mean_days=21.0)
    assert _recovery_time_days(rotable) == 60.0          # repair TAT p90
    assert _recovery_time_days(expendable) == 21.0       # procurement lead, ignores repair TAT


def test_aog_active_forces_advisor() -> None:
    from trax_io_reco.contracts.context import AogSignal
    ctx = _ctx(monthly_units=[1], tier=4, aog=AogSignal(active=True))
    scored = AogRiskScorer().score(_rec(RecommendationType.PURCHASE, shortage=2, qty=2),
                                   context=ctx, net=_net(2))
    assert scored.suggested_autonomy_tier == AutonomyTier.ADVISOR
    assert "active_aog" in scored.guardrail_flags


# ---- Confidence (spec §7.9) ---- #
def test_confidence_stub_lower_than_real() -> None:
    real = confidence_score(events_24mo=12, regime=Regime.INTERMITTENT, used_stub_inputs=set())
    stub = confidence_score(events_24mo=12, regime=Regime.INTERMITTENT,
                            used_stub_inputs={"aog", "repair_tat"})
    assert stub < real
    assert 0.0 <= stub <= 1.0


# ---- Ranking (spec §7.9) ---- #
def test_suggest_tier_thresholds() -> None:
    assert suggest_tier(criticality=1, unit_cost=100, delta_pct=0.0, active_aog=False) == AutonomyTier.ADVISOR
    assert suggest_tier(criticality=5, unit_cost=100, delta_pct=0.1, active_aog=False) == AutonomyTier.AUTONOMOUS
    assert suggest_tier(criticality=3, unit_cost=100, delta_pct=0.1, active_aog=False) == AutonomyTier.BOUNDED


def test_ranking_is_deterministic_total_order() -> None:
    a = _rec(RecommendationType.PURCHASE, cost="100", aog=AogRiskLevel.HIGH, part="A")
    b = _rec(RecommendationType.PURCHASE, cost="100", aog=AogRiskLevel.HIGH, part="B")
    # Equal score -> tie-break by part_number; order independent of input order.
    assert [r.part_number for r in rank([b, a])] == ["A", "B"]
    assert [r.part_number for r in rank([a, b])] == ["A", "B"]
