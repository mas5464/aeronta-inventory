from __future__ import annotations

from datetime import date

from trax_io_feature_store import InMemoryFeatureStore, TenantContext

from tests.fixtures.builders import seed_part
from trax_io_reco.contracts.enums import PolicyKind, Regime
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.demand.projection import HistoricalScheduledProjector
from trax_io_reco.policy.mini_engine import MiniPolicyEngine, PolicyConstraintViolation

TENANT = TenantContext(tenant_id="acme")


def _ctx(**kw):
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", **kw)
    return ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )


def _recommend(regime: Regime, ctx):
    proj = HistoricalScheduledProjector().project(context=ctx, regime=regime)
    return MiniPolicyEngine().recommend(context=ctx, regime=regime, projection=proj)


def test_ultra_rare_tier1_base_stock() -> None:
    ctx = _ctx(monthly_units=[0, 0, 1, 0, 0], tier=1)
    rec = _recommend(Regime.ULTRA_RARE, ctx)
    assert isinstance(rec, PolicyRecommendation)
    assert rec.policy_kind == PolicyKind.BASE_STOCK
    assert rec.model_id == "deterministic-v1"
    assert rec.service_level_target == 0.995


def test_ultra_rare_tier4_still_base_stock() -> None:
    # spec §6.2: ultra-rare tier 3-5 also routes to base-stock (not R_Q).
    ctx = _ctx(monthly_units=[0, 1, 0, 0], tier=4)
    rec = _recommend(Regime.ULTRA_RARE, ctx)
    assert isinstance(rec, PolicyRecommendation)
    assert rec.policy_kind == PolicyKind.BASE_STOCK


def test_intermittent_s_S() -> None:
    ctx = _ctx(monthly_units=[1, 0, 2, 0, 1, 1, 0, 1], tier=3)
    rec = _recommend(Regime.INTERMITTENT, ctx)
    assert isinstance(rec, PolicyRecommendation)
    assert rec.policy_kind == PolicyKind.S_S


def test_high_volume_R_Q() -> None:
    ctx = _ctx(monthly_units=[30] * 12, tier=4)
    rec = _recommend(Regime.HIGH_VOLUME, ctx)
    assert isinstance(rec, PolicyRecommendation)
    assert rec.policy_kind == PolicyKind.R_Q


def test_constraint_violation_returns_violation() -> None:
    # 1-day shelf life on a moving part -> shelf-life clamp breaks floors -> violation.
    ctx = _ctx(monthly_units=[30] * 12, tier=4, shelf_life_days=1)
    rec = _recommend(Regime.HIGH_VOLUME, ctx)
    assert isinstance(rec, PolicyConstraintViolation)


def test_policy_exposes_applicable_constraints_and_moq_binding() -> None:
    ctx = _ctx(
        monthly_units=[1] * 12,
        tier=4,
        min_oq=50,
        shelf_life_days=10000,
        current_policy=(2, 2, 1, 1000),
    )
    rec = _recommend(Regime.HIGH_VOLUME, ctx)
    assert isinstance(rec, PolicyRecommendation)

    by_name = {constraint.name: constraint for constraint in rec.applied_constraints}
    assert by_name["minimum_order_quantity"].value == "50"
    assert by_name["minimum_order_quantity"].binding is True
    assert by_name["minimum_order_quantity"].source == (
        "vendor_economics.minimum_order_qty"
    )
    assert "reorder_point_floor" in by_name
    assert "maximum_stock_floor" in by_name
    assert "shelf_life_cap" in by_name


def test_open_order_deferral_is_horizon_bound_and_traceable() -> None:
    ctx = _ctx(
        monthly_units=[1] * 12,
        tier=4,
        serviceable=100,
        open_qty=20,
        open_rcv_date=date(2026, 5, 17),
        current_policy=(2, 2, 1, 10),
    )
    projection = HistoricalScheduledProjector().project(
        context=ctx,
        regime=Regime.HIGH_VOLUME,
    )
    rec = MiniPolicyEngine().recommend(
        context=ctx,
        regime=Regime.HIGH_VOLUME,
        projection=projection,
        as_of=date(2026, 4, 17),
        horizon_days=30,
    )
    assert isinstance(rec, PolicyRecommendation)

    deferral = next(
        constraint
        for constraint in rec.applied_constraints
        if constraint.name == "open_order_deferral"
    )
    assert deferral.binding is True
    assert "open_order_deferral" in rec.constraint_flags


def test_constraint_evidence_changes_policy_provenance_even_when_values_do_not() -> None:
    without_cap = _recommend(
        Regime.HIGH_VOLUME,
        _ctx(
            monthly_units=[1] * 12,
            tier=4,
            shelf_life_days=None,
            current_policy=(2, 2, 1, 1000),
        ),
    )
    with_nonbinding_cap = _recommend(
        Regime.HIGH_VOLUME,
        _ctx(
            monthly_units=[1] * 12,
            tier=4,
            shelf_life_days=10000,
            current_policy=(2, 2, 1, 1000),
        ),
    )

    assert isinstance(without_cap, PolicyRecommendation)
    assert isinstance(with_nonbinding_cap, PolicyRecommendation)
    assert (
        without_cap.rop,
        without_cap.eoq,
        without_cap.safety_stock,
        without_cap.max_stock,
    ) == (
        with_nonbinding_cap.rop,
        with_nonbinding_cap.eoq,
        with_nonbinding_cap.safety_stock,
        with_nonbinding_cap.max_stock,
    )
    assert without_cap.provenance_id != with_nonbinding_cap.provenance_id
