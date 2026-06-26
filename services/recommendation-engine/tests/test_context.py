from __future__ import annotations

from datetime import date

from trax_io_feature_store.schemas import (
    Criticality,
    DemandHistory,
    PartAttributes,
    VendorEconomics,
)

from trax_io_reco.contracts.context import (
    CurrentPolicy,
    DemandProjection,
    NetPosition,
    PartLocationContext,
    StockPosition,
    TenantPolicyConfig,
)


def test_stock_position_dispatchable() -> None:
    sp = StockPosition(
        on_hand=10, serviceable=8, unserviceable_in_repair=2, allocated_reserved=3
    )
    assert sp.serviceable - sp.allocated_reserved == 5


def test_demand_projection_is_rate() -> None:
    dp = DemandProjection(
        mean_per_day=0.5,
        std_per_day=0.7,
        dist_kind="COMPOUND_POISSON",
        dist_params={"lambda": 0.4, "clump_p": 0.8},
        historical_component=0.5,
        scheduled_component=0.0,
        by_aircraft={},
        by_task={},
        basis_window_days=730,
    )
    assert dp.mean_per_day == 0.5
    assert dp.dist_kind == "COMPOUND_POISSON"


def test_tenant_policy_config_defaults_match_spec_5_5() -> None:
    cfg = TenantPolicyConfig()
    assert cfg.service_level_by_tier == {1: 0.995, 2: 0.98, 3: 0.95, 4: 0.92, 5: 0.90}


def test_net_position_fields() -> None:
    np_ = NetPosition(
        pn="P",
        location="L",
        group_id=None,
        window_days=30,
        available=5.0,
        expected_receipts_in_window=2.0,
        projected_demand=10.0,
        net=-3.0,
        shortage=3.0,
    )
    assert np_.shortage == 3.0


def test_context_description_falls_back_to_pn() -> None:
    ctx = PartLocationContext(
        tenant_id="t",
        pn="P-1",
        location="YYZ",
        stock_position=StockPosition(on_hand=1, serviceable=1),
        current_policy=CurrentPolicy(rop=1, eoq=1, safety_stock=0, max_stock=2),
        vendor_economics=VendorEconomics(
            tenant_id="t", pn="P-1", vendor="V", unit_cost="100", extract_date=date(2026, 4, 1)
        ),
        part_attributes=PartAttributes(
            tenant_id="t", pn="P-1", description=None, extract_date=date(2026, 4, 1)
        ),
        criticality=Criticality(
            tenant_id="t",
            pn="P-1",
            raw_essentiality_code="E",
            canonical_tier=3,
            extract_date=date(2026, 4, 1),
        ),
        demand_history=DemandHistory(
            tenant_id="t", pn="P-1", location="YYZ", extract_date=date(2026, 4, 1)
        ),
    )
    assert ctx.description == "P-1"  # falls back when part description is None
