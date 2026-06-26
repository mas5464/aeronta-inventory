"""Tenant-isolation + Protocol conformance tests for the in-memory stub."""

from __future__ import annotations

from datetime import date

import pytest

from trax_io_feature_store import (
    FeatureStoreClient,
    FeatureStoreLookupError,
    InMemoryFeatureStore,
    MissingTenantContextError,
    TenantContext,
)
from trax_io_feature_store.schemas import (
    CurrentPolicy,
    DemandHistory,
    DemandObservation,
    StockPosition,
)

EXTRACT = date(2026, 4, 15)


def _demand(tenant_id: str, pn: str, location: str) -> DemandHistory:
    return DemandHistory(
        tenant_id=tenant_id,
        pn=pn,
        location=location,
        observations=[
            DemandObservation(bucket="month", period_start=date(2026, 3, 1), removals=3),
        ],
        extract_date=EXTRACT,
    )


def test_in_memory_store_conforms_to_protocol():
    store = InMemoryFeatureStore()
    assert isinstance(store, FeatureStoreClient)


def test_read_requires_tenant_context():
    store = InMemoryFeatureStore()
    with pytest.raises(MissingTenantContextError):
        store.get_demand_history(tenant=None, pn="X", location="Y")  # type: ignore[arg-type]


def test_tenant_context_rejects_empty_id():
    with pytest.raises(ValueError):
        TenantContext(tenant_id="")


def test_cross_tenant_read_is_blocked():
    store = InMemoryFeatureStore()
    store.seed(
        "aircanada",
        "demand_history",
        ("P-INT", "YYZ-MAIN"),
        _demand("aircanada", "P-INT", "YYZ-MAIN"),
    )
    jetblue = TenantContext(tenant_id="jetblue")
    with pytest.raises(FeatureStoreLookupError):
        store.get_demand_history(tenant=jetblue, pn="P-INT", location="YYZ-MAIN")


def test_read_after_seed_is_consistent():
    store = InMemoryFeatureStore()
    tenant = TenantContext(tenant_id="aircanada")
    store.seed(
        "aircanada",
        "demand_history",
        ("P-INT", "YYZ-MAIN"),
        _demand("aircanada", "P-INT", "YYZ-MAIN"),
    )
    h = store.get_demand_history(tenant=tenant, pn="P-INT", location="YYZ-MAIN")
    assert h.pn == "P-INT"
    assert h.observations[0].removals == 3


def test_unknown_key_raises_lookup_error():
    store = InMemoryFeatureStore()
    tenant = TenantContext(tenant_id="aircanada")
    with pytest.raises(FeatureStoreLookupError):
        store.get_part_attributes(tenant=tenant, pn="DOES-NOT-EXIST")


# --- Phase 2: promoted gap groups (stock_position, current_policy) ---


def test_stock_position_read_roundtrip():
    store = InMemoryFeatureStore()
    sp = StockPosition(
        tenant_id="acme", pn="P-1", location="YYZ", on_hand=10, serviceable=8,
        allocated_reserved=2, unserviceable_in_repair=2, extract_date=EXTRACT,
    )
    store.seed("acme", "stock_position", ("P-1", "YYZ"), sp)
    got = store.get_stock_position(tenant=TenantContext(tenant_id="acme"), pn="P-1", location="YYZ")
    assert got.serviceable - got.allocated_reserved == 6


def test_current_policy_read_roundtrip():
    store = InMemoryFeatureStore()
    cp = CurrentPolicy(
        tenant_id="acme", pn="P-1", location="YYZ", rop=5, eoq=5, safety_stock=2,
        max_stock=40, replenishment_lead_days=21.0, extract_date=EXTRACT,
    )
    store.seed("acme", "current_policy", ("P-1", "YYZ"), cp)
    got = store.get_current_policy(tenant=TenantContext(tenant_id="acme"), pn="P-1", location="YYZ")
    assert got.max_stock == 40


def test_promoted_groups_in_buckets_and_protocol():
    store = InMemoryFeatureStore()
    assert "stock_position" in store._BUCKETS and "current_policy" in store._BUCKETS
    assert isinstance(store, FeatureStoreClient)  # Protocol still satisfied with new methods
    with pytest.raises(FeatureStoreLookupError):
        store.get_stock_position(tenant=TenantContext(tenant_id="acme"), pn="X", location="Y")
