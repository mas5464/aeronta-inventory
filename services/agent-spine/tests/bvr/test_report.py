"""Assembly tests: posture, governance, forward look, determinism (spec §1, §6)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trax_io_reco.contracts.enums import (
    AogRiskLevel,
    AutonomyTier,
    RecommendationType,
)
from trax_io_reco.contracts.recommendation import Recommendation

from trax_io_spine.bvr.report import TIER_TARGETS, KeyFacts, RecState, build_bvr_report
from trax_io_spine.contracts import HistoryEntry, WritebackStatus

_NOW = datetime(2026, 4, 1, tzinfo=UTC)


def _rec(pn: str, impact: str, rec_id: str) -> Recommendation:
    return Recommendation(
        recommendation_id=rec_id, tenant_id="acme",
        type=RecommendationType.PURCHASE, part_number=pn, description="Widget",
        current_location="YYZ",
        current_stock=1, projected_demand=2.0, shortage_quantity=1.0,
        recommended_quantity=3.0, estimated_cost_impact=Decimal(impact),
        aog_risk_level=AogRiskLevel.LOW, criticality_tier=2, reason="low stock",
        confidence_score=0.5,
        horizon_days=90, suggested_autonomy_tier=AutonomyTier.BOUNDED,
        supporting_evidence=(), generated_at=_NOW, input_snapshot_hash="hashA",
        policy=None, current_policy=None,
    )


def _entry(pn: str, status: WritebackStatus, *, prov: str = "prov-1",
           tier: AutonomyTier | None = AutonomyTier.BOUNDED,
           at: datetime = datetime(2024, 4, 10, tzinfo=UTC)) -> HistoryEntry:
    return HistoryEntry(
        tenant_id="acme", pn=pn, location="YYZ", version=1, status=status,
        old_values={"rop": 3, "eoq": 10, "safety_stock": 2, "max_stock": 20},
        new_values={"rop": 8, "eoq": 20, "safety_stock": 4, "max_stock": 30},
        provenance_id=prov, tier=tier, agent_version="spine-0.1.0",
        changed_by_principal="agent", idempotency_key=None, parent_version=None,
        changed_at=at,
    )


def _facts() -> list[KeyFacts]:
    return [
        # tier 1: rop 10 >= ltd 5 (0.5/day * 10d) -> at posture
        KeyFacts(pn="PN1", location="YYZ", criticality_tier=1, rop=10,
                 mean_per_day=0.5, lead_mean=10.0, unit_cost=100.0),
        # tier 1: rop 2 < ltd 5 -> below posture
        KeyFacts(pn="PN2", location="YYZ", criticality_tier=1, rop=2,
                 mean_per_day=0.5, lead_mean=10.0, unit_cost=50.0),
        # tier 3: zero demand -> ltd 0, rop 0 >= 0 -> at posture
        KeyFacts(pn="PN3", location="YUL", criticality_tier=3, rop=0,
                 mean_per_day=0.0, lead_mean=7.0, unit_cost=None),
    ]


def _states() -> list[RecState]:
    return [
        RecState(rec=_rec("PN1", "100.00", "01AAA"), status="pending"),
        RecState(rec=_rec("PN2", "250.00", "01BBB"), status="pending"),
        RecState(rec=_rec("PN3", "40.00", "01CCC"), status="approved"),
        RecState(rec=_rec("PN1", "10.00", "01DDD"), status="rejected"),
    ]


def _build(**overrides):
    kwargs = dict(
        tenant_id="acme", extract_date="2024-04-01", generated_at=_NOW,
        key_facts=_facts(), rec_states=_states(),
        ledger=(
            _entry("PN1", WritebackStatus.WRITTEN),
            _entry("PN2", WritebackStatus.SHADOWED,
                   at=datetime(2024, 4, 20, tzinfo=UTC)),
            _entry("PN3", WritebackStatus.WRITTEN, prov="rollback:prov-1", tier=None),
        ),
        baseline_for=lambda e: e.old_values,
        kill_switch=False,
    )
    kwargs.update(overrides)
    return build_bvr_report(**kwargs)


def test_service_posture_per_tier():
    r = _build()
    tiers = {t.tier: t for t in r.service_posture.tiers}
    assert set(tiers) == {1, 3}  # only tiers with keys are reported
    assert tiers[1].keys == 2 and tiers[1].keys_at_posture == 1
    assert tiers[1].posture_rate == 0.5
    assert tiers[1].target_fill_rate == TIER_TARGETS[1] == 0.995
    assert tiers[3].posture_rate == 1.0
    assert "not realized" in r.service_posture.note


def test_governance_counts_rates_and_rollbacks():
    g = _build().governance
    assert (g.recommendations_total, g.pending, g.approved, g.rejected, g.deferred) == (
        4, 2, 1, 1, 0,
    )
    assert g.approval_rate == 0.5  # 1 approved of 2 decided
    assert g.override_rate == 0.5
    assert g.writes_written == 2 and g.writes_shadowed == 1
    assert g.rollbacks == 1  # provenance_id startswith "rollback:"
    assert g.tier_mix == {"A": 0, "B": 2, "C": 0}
    assert g.kill_switch_engaged is False


def test_forward_look_ranks_pending_by_impact():
    f = _build().forward_look
    assert f.open_pipeline_value == Decimal("350.00")  # 100 + 250 (pending only)
    assert [o.pn for o in f.top_opportunities] == ["PN2", "PN1"]  # impact desc


def test_period_window_from_ledger_and_exec_summary():
    r = _build()
    assert r.period.extract_date == "2024-04-01"
    assert r.period.decision_window_start == datetime(2024, 4, 10, tzinfo=UTC)
    assert r.period.decision_window_end == datetime(2024, 4, 20, tzinfo=UTC)
    assert r.executive_summary.changes_applied == 2
    assert r.executive_summary.changes_shadowed == 1
    assert r.executive_summary.keys_under_management == 3
    assert "tiers at target posture" in r.executive_summary.service_headline
    assert r.methodology.input_snapshot_hashes == ("hashA",)


def test_report_is_deterministic_modulo_generated_at():
    a = _build().model_dump(exclude={"period": {"generated_at"}})
    b = _build().model_dump(exclude={"period": {"generated_at"}})
    assert a == b


def test_no_writes_gives_empty_window_and_zero_savings():
    r = _build(ledger=())
    assert r.period.decision_window_start is None
    assert r.savings.total_projected == Decimal("0.00")
    assert r.savings.changes_total == 0


def test_methodology_caps_snapshot_hash_sample():
    # Ops finding (58.9K live deploy): listing every distinct rec hash made the report
    # 2.6MB and a 2-minute PDF. Methodology carries a bounded sample + the full count.
    states = [
        RecState(rec=_rec("PN1", "1.00", f"01X{i:02d}").model_copy(
            update={"input_snapshot_hash": f"hash{i:02d}"}), status="pending")
        for i in range(20)
    ]
    r = _build(rec_states=states)
    assert r.methodology.input_snapshot_hash_count == 20
    assert len(r.methodology.input_snapshot_hashes) == 12
    expected = tuple(sorted(f"hash{i:02d}" for i in range(20))[:12])
    assert r.methodology.input_snapshot_hashes == expected
