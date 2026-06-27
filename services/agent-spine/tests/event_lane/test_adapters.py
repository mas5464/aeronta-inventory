from datetime import date

import pytest
from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.schemas import (
    Criticality,
    CurrentPolicy,
    FeatureBundle,
    PartAttributes,
    StockPosition,
    VendorEconomics,
)

from trax_io_spine.event_lane.adapters import BundleFeatureStore, BundleInventoryState

ACME = TenantContext(tenant_id="acme")
_D = date(2026, 4, 1)


def _bundle() -> FeatureBundle:
    return FeatureBundle(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        stock_position=StockPosition(tenant_id="acme", pn="PN-A", location="LOC-1",
                                     on_hand=10, serviceable=10, extract_date=_D),
        current_policy=CurrentPolicy(tenant_id="acme", pn="PN-A", location="LOC-1",
                                     rop=5, eoq=4, safety_stock=2, max_stock=12, extract_date=_D),
        part_attributes=PartAttributes(tenant_id="acme", pn="PN-A", extract_date=_D),
        criticality=Criticality(tenant_id="acme", pn="PN-A", raw_essentiality_code="4",
                                canonical_tier=4, extract_date=_D),
        vendor_economics={"DEFAULT": VendorEconomics(tenant_id="acme", pn="PN-A", vendor="DEFAULT",
                                                     unit_cost=100, extract_date=_D)},
    )


def _store() -> BundleFeatureStore:
    return BundleFeatureStore("acme", {("PN-A", "LOC-1"): _bundle()})


def test_serves_key_level_and_part_level_reads() -> None:
    s = _store()
    assert s.get_stock_position(tenant=ACME, pn="PN-A", location="LOC-1").serviceable == 10
    assert s.get_current_policy(tenant=ACME, pn="PN-A", location="LOC-1").rop == 5
    assert s.get_part_attributes(tenant=ACME, pn="PN-A").pn == "PN-A"
    assert s.get_criticality(tenant=ACME, pn="PN-A").canonical_tier == 4
    assert s.get_vendor_economics(tenant=ACME, pn="PN-A", vendor="DEFAULT").unit_cost == 100


def test_missing_field_raises_lookup() -> None:
    # bundle has no demand_history -> miss
    with pytest.raises(FeatureStoreLookupError):
        _store().get_demand_history(tenant=ACME, pn="PN-A", location="LOC-1")


def test_unmodeled_groups_always_miss() -> None:
    with pytest.raises(FeatureStoreLookupError):
        _store().get_causal_utilization(tenant=ACME, ac_type="A320", destination="YYZ")
    with pytest.raises(FeatureStoreLookupError):
        _store().get_wash_rate_history(tenant=ACME, pn="PN-A", location="LOC-1")


def test_cross_tenant_raises() -> None:
    other = TenantContext(tenant_id="other")
    with pytest.raises(FeatureStoreLookupError):
        _store().get_stock_position(tenant=other, pn="PN-A", location="LOC-1")


def test_inventory_state_defaults_are_empty() -> None:
    inv = BundleInventoryState()
    assert inv.get_scheduled_demand(tenant=ACME, pn="PN-A", location="LOC-1") == ()
    assert inv.get_aog_signal(tenant=ACME, pn="PN-A", location="LOC-1") is not None
    assert inv.get_repair_tat(tenant=ACME, pn="PN-A") is not None
