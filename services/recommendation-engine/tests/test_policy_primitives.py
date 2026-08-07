from __future__ import annotations

from datetime import date

from trax_io_feature_store.schemas import PartAttributes

from trax_io_reco.contracts.context import CurrentPolicy, DemandProjection
from trax_io_reco.policy.base_stock import compute_base_stock
from trax_io_reco.policy.constraints import apply_constraints
from trax_io_reco.policy.R_Q import compute_R_Q
from trax_io_reco.policy.s_S import compute_s_S


def _cp(lam: float) -> DemandProjection:
    return DemandProjection(
        mean_per_day=lam,
        std_per_day=lam**0.5,
        dist_kind="COMPOUND_POISSON",
        dist_params={"lambda": lam, "clump_p": 1.0},
        historical_component=lam,
        scheduled_component=0.0,
        by_aircraft={},
        by_task={},
        basis_window_days=730,
    )


def _normal(mean: float, var: float) -> DemandProjection:
    return DemandProjection(
        mean_per_day=mean,
        std_per_day=var**0.5,
        dist_kind="NORMAL",
        dist_params={"mean": mean, "var": var},
        historical_component=mean,
        scheduled_component=0.0,
        by_aircraft={},
        by_task={},
        basis_window_days=730,
    )


def _pa() -> PartAttributes:
    return PartAttributes(tenant_id="t", pn="P", extract_date=date(2026, 4, 1))


def test_base_stock_pinned_compound_poisson() -> None:
    # lam*lead = 0.1*20 = 2.0 -> Poisson(2), S(0.95)=5 -> max=5, rop=4, eoq=1.
    rop, eoq, ss, mx = compute_base_stock(
        projection=_cp(0.1), lead_mean=20.0, lead_var=0.0, service_level=0.95
    )
    assert (rop, eoq, mx) == (4, 1, 5)
    assert rop >= ss and mx >= rop + eoq


def test_s_S_floors_hold() -> None:
    rop, eoq, ss, mx = compute_s_S(
        projection=_cp(0.05),
        lead_mean=20.0,
        lead_var=0.0,
        service_level=0.95,
        ordering_cost=150.0,
        holding_cost_rate=0.25,
        unit_cost=100.0,
        min_order_qty=1,
    )
    assert rop >= ss and mx >= rop + eoq and eoq >= 1


def test_R_Q_floors_and_minoq() -> None:
    rop, eoq, ss, mx = compute_R_Q(
        projection=_normal(1.0, 1.5),
        lead_mean=21.0,
        lead_var=4.0,
        service_level=0.92,
        ordering_cost=150.0,
        holding_cost_rate=0.25,
        unit_cost=50.0,
        min_order_qty=12,
    )
    assert eoq >= 12  # MinOQ floor
    assert rop >= ss and mx >= rop + eoq


def test_constraints_shelf_life_clamp() -> None:
    res = apply_constraints(
        (10, 5, 3, 40),
        part_attributes=PartAttributes(
            tenant_id="t", pn="P", shelf_life_days=30, extract_date=date(2026, 4, 1)
        ),
        current_policy=CurrentPolicy(rop=10, eoq=5, safety_stock=3, max_stock=40),
        avg_daily_demand=3.0,  # cap = floor(0.6*30*3) = 54 -> no clamp
        min_order_qty=1,
    )
    assert res.violation is None and res.values is not None


def test_shelf_life_unit_cap_scales_with_units_per_day() -> None:
    attributes = PartAttributes(
        tenant_id="t",
        pn="P",
        shelf_life_days=30,
        extract_date=date(2026, 4, 1),
    )
    policy = CurrentPolicy(rop=0, eoq=1, safety_stock=0, max_stock=40)
    low_rate = apply_constraints(
        (0, 1, 0, 40),
        part_attributes=attributes,
        current_policy=policy,
        avg_daily_demand=0.2,
        min_order_qty=1,
    )
    high_rate = apply_constraints(
        (0, 1, 0, 40),
        part_attributes=attributes,
        current_policy=policy,
        avg_daily_demand=2.0,
        min_order_qty=1,
    )

    assert low_rate.values == (0, 1, 0, 3)
    assert high_rate.values == (0, 1, 0, 36)
    low_cap = next(
        constraint
        for constraint in low_rate.applied_constraints
        if constraint.name == "shelf_life_cap"
    )
    high_cap = next(
        constraint
        for constraint in high_rate.applied_constraints
        if constraint.name == "shelf_life_cap"
    )
    assert low_cap.value == "3"
    assert high_cap.value == "36"


def test_constraints_violation_routes_to_skip() -> None:
    # shelf_life 1 day + demand 1/day -> cap = floor(0.6) = 0 -> max < rop+eoq -> violation.
    res = apply_constraints(
        (10, 5, 3, 40),
        part_attributes=PartAttributes(
            tenant_id="t", pn="P", shelf_life_days=1, extract_date=date(2026, 4, 1)
        ),
        current_policy=CurrentPolicy(rop=10, eoq=5, safety_stock=3, max_stock=40),
        avg_daily_demand=1.0,
        min_order_qty=1,
    )
    assert res.values is None and res.violation is not None
    assert "shelf_life_clamped" in res.flags
