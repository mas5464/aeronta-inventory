"""populate_online: materialize + upsert bundles, skipping incomplete keys (moto-backed)."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("boto3")
pytest.importorskip("moto")

from trax_io_feature_store.client import (  # noqa: E402
    FeatureStoreLookupError,
    InMemoryFeatureStore,
    TenantContext,
)
from trax_io_feature_store.online_store import DynamoDbOnlineStore  # noqa: E402
from trax_io_feature_store.online_writer import PopulateResult, populate_online  # noqa: E402
from trax_io_feature_store.schemas import CurrentPolicy, StockPosition  # noqa: E402

ACME = TenantContext(tenant_id="acme")
D1 = date(2026, 4, 1)


def _offline_with_one_complete_one_stockless() -> InMemoryFeatureStore:
    fs = InMemoryFeatureStore()
    # (PN-A, LOC-1): has stock -> complete -> written
    fs.seed("acme", "stock_position", ("PN-A", "LOC-1"), StockPosition(
        tenant_id="acme", pn="PN-A", location="LOC-1", on_hand=10, serviceable=8,
        unserviceable_in_repair=1, allocated_reserved=1, rental=0, loan=0, extract_date=D1))
    # (PN-B, LOC-1): policy but NO stock -> incomplete -> skipped (not written as null-stock)
    fs.seed("acme", "current_policy", ("PN-B", "LOC-1"), CurrentPolicy(
        tenant_id="acme", pn="PN-B", location="LOC-1", rop=5, eoq=4, safety_stock=2, max_stock=40,
        replenishment_lead_days=21.0, extract_date=D1))
    return fs


def test_populate_writes_complete_skips_incomplete(online_table) -> None:
    offline = _offline_with_one_complete_one_stockless()
    online = DynamoDbOnlineStore(table=online_table)
    keys = [("PN-A", "LOC-1"), ("PN-B", "LOC-1")]

    result = populate_online(offline, online, tenant=ACME, keys=keys)

    assert result == PopulateResult(written=1, skipped_incomplete=1, failed_oversize=0)
    assert result.total == 2
    # complete key is online...
    got = online.get_bundle(tenant=ACME, pn="PN-A", location="LOC-1")
    assert got.stock_position.serviceable == 8
    # ...and the stockless key was NOT written (no misleading null-stock bundle)
    with pytest.raises(FeatureStoreLookupError):
        online.get_bundle(tenant=ACME, pn="PN-B", location="LOC-1")


def test_populate_empty_keys_is_noop(online_table) -> None:
    online = DynamoDbOnlineStore(table=online_table)
    result = populate_online(InMemoryFeatureStore(), online, tenant=ACME, keys=[])
    assert result == PopulateResult()
