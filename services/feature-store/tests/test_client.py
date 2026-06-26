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
from trax_io_feature_store.schemas import DemandHistory, DemandObservation

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
