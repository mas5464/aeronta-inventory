"""DynamoDbOnlineStore put/get round-trip + tenant isolation (moto-backed; skips without deps)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("boto3")
pytest.importorskip("moto")

from trax_io_feature_store.client import (  # noqa: E402
    FeatureStoreLookupError,
    MissingTenantContextError,
    TenantContext,
)
from trax_io_feature_store.online_store import DynamoDbOnlineStore  # noqa: E402
from trax_io_feature_store.schemas import (  # noqa: E402
    FeatureBundle,
    StockPosition,
    VendorEconomics,
)

ACME = TenantContext(tenant_id="acme")
D1 = date(2026, 4, 1)


def _bundle(tenant="acme", pn="PN-A", location="LOC-1") -> FeatureBundle:
    return FeatureBundle(
        tenant_id=tenant, pn=pn, location=location,
        stock_position=StockPosition(tenant_id=tenant, pn=pn, location=location, on_hand=10,
                                     serviceable=8, unserviceable_in_repair=1,
                                     allocated_reserved=1, rental=0, loan=0, extract_date=D1),
        vendor_economics={
            "DEFAULT": VendorEconomics(tenant_id=tenant, pn=pn, vendor="DEFAULT",
                                       unit_cost=Decimal("4200.5000"), minimum_order_qty=3,
                                       currency="USD", extract_date=D1),
        },
    )


def test_put_get_roundtrip_is_lossless(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    bundle = _bundle()
    store.put_bundle(bundle)
    got = store.get_bundle(tenant=ACME, pn="PN-A", location="LOC-1")
    assert got == bundle  # nested models + Decimal + vendor map all round-trip
    assert got.vendor_economics["DEFAULT"].unit_cost == Decimal("4200.5000")
    assert got.stock_position.serviceable == 8


def test_missing_key_raises_lookup(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    with pytest.raises(FeatureStoreLookupError):
        store.get_bundle(tenant=ACME, pn="NOPE", location="LOC-1")


def test_missing_tenant_raises(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    store.put_bundle(_bundle())
    with pytest.raises(MissingTenantContextError):
        store.get_bundle(tenant=None, pn="PN-A", location="LOC-1")  # type: ignore[arg-type]


def test_cross_tenant_isolation(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    store.put_bundle(_bundle(tenant="acme"))
    other = TenantContext(tenant_id="other")
    with pytest.raises(FeatureStoreLookupError):
        store.get_bundle(tenant=other, pn="PN-A", location="LOC-1")


def test_put_is_upsert(online_table) -> None:
    store = DynamoDbOnlineStore(table=online_table)
    store.put_bundle(_bundle())
    updated = _bundle()
    updated = updated.model_copy(update={
        "stock_position": updated.stock_position.model_copy(update={"serviceable": 99}),
    })
    store.put_bundle(updated)
    got = store.get_bundle(tenant=ACME, pn="PN-A", location="LOC-1")
    assert got.stock_position.serviceable == 99


def test_hash_in_keys_does_not_collide(online_table) -> None:
    # eMRO PNs/locations can contain '#'. ("A#B","C") and ("A","B#C") must NOT collide.
    store = DynamoDbOnlineStore(table=online_table)
    store.put_bundle(_bundle(pn="A#B", location="C"))
    store.put_bundle(_bundle(pn="A", location="B#C"))
    one = store.get_bundle(tenant=ACME, pn="A#B", location="C")
    two = store.get_bundle(tenant=ACME, pn="A", location="B#C")
    assert (one.pn, one.location) == ("A#B", "C")  # distinct items, correct reads
    assert (two.pn, two.location) == ("A", "B#C")
