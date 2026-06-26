from __future__ import annotations

import pytest
from trax_io_feature_store import FeatureStoreLookupError, InMemoryFeatureStore, TenantContext

from tests.fixtures.builders import seed_part
from trax_io_reco.contracts.context import AogSignal, RepairTat
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InMemoryInventoryState

TENANT = TenantContext(tenant_id="acme")


def test_stock_position_missing_raises_from_feature_store() -> None:
    # stock_position is a Feature-Store group now (Phase 2); a miss propagates.
    fr = FeatureReader(InMemoryFeatureStore())
    with pytest.raises(FeatureStoreLookupError):
        fr.get_stock_position(tenant=TENANT, pn="P", location="L")


def test_inventory_state_optional_defaults() -> None:
    inv = InMemoryInventoryState()
    assert inv.get_scheduled_demand(tenant=TENANT, pn="P", location="L") == ()
    assert inv.get_aog_signal(tenant=TENANT, pn="P", location="L") == AogSignal()
    assert inv.get_repair_tat(tenant=TENANT, pn="P") == RepairTat()


def test_feature_reader_optional_none_on_miss() -> None:
    fs = InMemoryFeatureStore()
    fr = FeatureReader(fs)
    assert fr.get_open_orders(tenant=TENANT, pn="P", location="L") is None
    assert fr.get_location_graph(tenant=TENANT, location="L") is None


def test_assembler_builds_context_with_description() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P-1", location="YYZ",
              monthly_units=[1, 0, 2, 0, 1], description="HYDRAULIC PUMP", serviceable=3)
    assembler = ContextAssembler(features=FeatureReader(fs), inventory_state=inv)
    ctx = assembler.assemble(tenant=TENANT, pn="P-1", location="YYZ")
    assert ctx.description == "HYDRAULIC PUMP"
    assert ctx.stock_position.serviceable == 3
    assert ctx.criticality.canonical_tier == 4
    assert ctx.current_policy.max_stock == 10


def test_assembler_resolves_vendor_from_open_orders() -> None:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    # Seed with a non-default vendor used both on the open order and vendor_economics.
    seed_part(fs, inv, tenant_id="acme", pn="P-2", location="YYZ", monthly_units=[2, 2],
              vendor="HONEYWELL", open_qty=4, unit_cost="250")
    assembler = ContextAssembler(features=FeatureReader(fs), inventory_state=inv)
    ctx = assembler.assemble(tenant=TENANT, pn="P-2", location="YYZ")
    assert ctx.vendor_economics.vendor == "HONEYWELL"
    assert ctx.lead_time is not None and ctx.lead_time.vendor == "HONEYWELL"
