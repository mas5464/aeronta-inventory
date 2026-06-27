"""GlueIcebergFeatureStore reads against a local pyiceberg lake (skips without iceberg extra)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

pytest.importorskip("pyiceberg")

from trax_io_feature_store.client import (  # noqa: E402
    FeatureStoreLookupError,
    MissingTenantContextError,
    TenantContext,
)
from trax_io_feature_store.iceberg_store import GlueIcebergFeatureStore  # noqa: E402

ACME = TenantContext(tenant_id="acme")
D1 = date(2026, 4, 1)
D2 = date(2026, 4, 2)


def _store(catalog) -> GlueIcebergFeatureStore:
    return GlueIcebergFeatureStore(catalog=catalog)


def _stock_row(pn, loc, serviceable, extract_date, tenant="acme"):
    return {
        "pn": pn, "location": loc, "on_hand": serviceable + 2, "serviceable": serviceable,
        "unserviceable_in_repair": 1, "allocated_reserved": 1, "rental": 0, "loan": 0,
        "tenant_id": tenant, "extract_date": extract_date,
    }


def test_stock_position_roundtrip(catalog, seed) -> None:
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 8, D1)])
    sp = _store(catalog).get_stock_position(tenant=ACME, pn="PN-A", location="LOC-1")
    assert (sp.pn, sp.location, sp.serviceable, sp.on_hand) == ("PN-A", "LOC-1", 8, 10)
    assert sp.tenant_id == "acme" and sp.extract_date == D1


def test_latest_extract_date_wins(catalog, seed) -> None:
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 8, D1)])
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 99, D2)])  # newer snapshot
    sp = _store(catalog).get_stock_position(tenant=ACME, pn="PN-A", location="LOC-1")
    assert sp.serviceable == 99 and sp.extract_date == D2  # latest wins


def test_vendor_economics_decimal_and_nulls(catalog, seed) -> None:
    seed("vendor_economics", [{
        "pn": "PN-A", "vendor": "DEFAULT", "unit_cost": Decimal("4200.5000"),
        "market_value_unit_cost": None, "average_cost": None, "kit_cost": None,
        "repair_cost_24mo_avg": Decimal("70.0000"), "minimum_order_qty": 3, "currency": "USD",
        "tenant_id": "acme", "extract_date": D1,
    }])
    ve = _store(catalog).get_vendor_economics(tenant=ACME, pn="PN-A", vendor="DEFAULT")
    assert ve.unit_cost == Decimal("4200.5000")
    assert ve.market_value_unit_cost is None and ve.kit_cost is None
    assert ve.repair_cost_24mo_avg == Decimal("70.0000")
    assert ve.minimum_order_qty == 3 and ve.currency == "USD"


def test_demand_history_aggregates_and_uses_latest_date(catalog, seed) -> None:
    # Two observations on the latest date + one stale-date row that must be excluded.
    seed("demand_history", [
        {"pn": "PN-A", "location": "LOC-1", "interchange_group_id": None, "bucket": "day",
         "period_start": date(2026, 3, 2), "removals": 5, "issues": 0, "source": "nightly-extract",
         "tenant_id": "acme", "extract_date": D2},
        {"pn": "PN-A", "location": "LOC-1", "interchange_group_id": None, "bucket": "day",
         "period_start": date(2026, 3, 1), "removals": 3, "issues": 2, "source": "nightly-extract",
         "tenant_id": "acme", "extract_date": D2},
        {"pn": "PN-A", "location": "LOC-1", "interchange_group_id": None, "bucket": "day",
         "period_start": date(2026, 1, 1), "removals": 99, "issues": 0, "source": "nightly-extract",
         "tenant_id": "acme", "extract_date": D1},  # stale extract_date -> excluded
    ])
    dh = _store(catalog).get_demand_history(tenant=ACME, pn="PN-A", location="LOC-1")
    assert dh.extract_date == D2
    assert [(o.period_start, o.removals, o.issues) for o in dh.observations] == [
        (date(2026, 3, 1), 3, 2),  # sorted by period_start
        (date(2026, 3, 2), 5, 0),
    ]
    assert dh.source == "nightly_extract"  # model default (Glue's hyphen value is dropped)


def test_open_orders_nested_struct(catalog, seed) -> None:
    seed("open_orders_snapshot", [{
        "pn": "PN-A", "location": "LOC-1", "snapshot_at": datetime(2026, 4, 1, 0, 0),
        "orders": [
            {"order_id": "O1", "order_type": "PO", "vendor": None, "qty_open": 7,
             "expected_rcv_date": date(2026, 4, 10)},
            {"order_id": "O2", "order_type": "RO", "vendor": None, "qty_open": 4,
             "expected_rcv_date": None},
        ],
        "total_open_qty": 11, "tenant_id": "acme", "extract_date": D1,
    }])
    oo = _store(catalog).get_open_orders_snapshot(tenant=ACME, pn="PN-A", location="LOC-1")
    assert oo.total_open_qty == 11 and len(oo.orders) == 2
    o1 = next(o for o in oo.orders if o.order_id == "O1")
    assert (o1.order_type, o1.qty_open, o1.expected_rcv_date) == ("PO", 7, date(2026, 4, 10))
    assert next(o for o in oo.orders if o.order_id == "O2").expected_rcv_date is None


def test_interchangeable_graph_nested(catalog, seed) -> None:
    seed("interchangeable_graph", [{
        "pn": "PN-2", "group_id": "PN-1+PN-2+PN-3", "members": ["PN-1", "PN-2", "PN-3"],
        "edges": [
            {"from_pn": "PN-1", "to_pn": "PN-2", "one_way": False},
            {"from_pn": "PN-2", "to_pn": "PN-3", "one_way": True},
        ],
        "tenant_id": "acme", "extract_date": D1,
    }])
    ig = _store(catalog).get_interchangeable_graph(tenant=ACME, pn="PN-2")
    assert ig.group_id == "PN-1+PN-2+PN-3" and ig.members == ["PN-1", "PN-2", "PN-3"]
    assert [(e.from_pn, e.to_pn, e.one_way) for e in ig.edges] == [
        ("PN-1", "PN-2", False), ("PN-2", "PN-3", True),
    ]


def test_location_graph_flat_to_nested_node(catalog, seed) -> None:
    seed("location_graph", [{
        "location": "YOW", "related_main_warehouse": "YYZ", "role": "outstation",
        "children": [], "tenant_id": "acme", "extract_date": D1,
    }])
    lg = _store(catalog).get_location_graph(tenant=ACME, location="YOW")
    assert lg.location == "YOW" and lg.children == []
    assert lg.node.related_main_warehouse == "YYZ" and lg.node.role == "outstation"


def test_missing_row_raises_lookup(catalog, seed) -> None:
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 8, D1)])
    with pytest.raises(FeatureStoreLookupError):
        _store(catalog).get_stock_position(tenant=ACME, pn="NOPE", location="LOC-1")


def test_missing_table_raises_lookup(catalog) -> None:
    # Table genuinely absent (not yet provisioned) -> NoSuchTableError -> lookup error, not a crash.
    with pytest.raises(FeatureStoreLookupError):
        _store(catalog).get_causal_utilization(tenant=ACME, ac_type="A320", destination="YYZ")


def test_empty_table_raises_lookup(catalog, seed) -> None:
    # The live production shape for causal/wash: the CDK creates the table but no Glue job
    # populates it -> the table exists but is empty -> lookup error (empty-rows path).
    seed("wash_rate_history", [])  # create an empty table
    with pytest.raises(FeatureStoreLookupError):
        _store(catalog).get_wash_rate_history(tenant=ACME, pn="PN-A", location="LOC-1")


def test_wash_rate_history_aggregates_points(catalog, seed) -> None:
    # Exploded per period_month -> reader must rebuild the sorted `points` list (not drop them).
    seed("wash_rate_history", [
        {"pn": "PN-A", "location": "LOC-1", "period_month": date(2026, 3, 1), "wash_rate": 0.2,
         "tenant_id": "acme", "extract_date": D1},
        {"pn": "PN-A", "location": "LOC-1", "period_month": date(2026, 2, 1), "wash_rate": 0.1,
         "tenant_id": "acme", "extract_date": D1},
    ])
    wr = _store(catalog).get_wash_rate_history(tenant=ACME, pn="PN-A", location="LOC-1")
    assert [(p.period_month, p.wash_rate) for p in wr.points] == [
        (date(2026, 2, 1), 0.1), (date(2026, 3, 1), 0.2),  # sorted by period_month
    ]


def test_reappended_partition_is_last_write_wins(catalog, seed) -> None:
    # Iceberg appends don't dedupe: a re-run leaves two row-sets for the same (key, extract_date).
    # The latest `ingested_at` must win deterministically (matching the in-memory last-write-wins).
    old = datetime(2026, 4, 1, 1, 0)
    new = datetime(2026, 4, 1, 9, 0)
    seed("stock_position", [{**_stock_row("PN-A", "LOC-1", 8, D1), "ingested_at": old}])
    seed("stock_position", [{**_stock_row("PN-A", "LOC-1", 99, D1), "ingested_at": new}])
    sp = _store(catalog).get_stock_position(tenant=ACME, pn="PN-A", location="LOC-1")
    assert sp.serviceable == 99  # freshest ingestion wins, deterministically


def test_missing_tenant_raises(catalog, seed) -> None:
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 8, D1)])
    with pytest.raises(MissingTenantContextError):
        _store(catalog).get_stock_position(tenant=None, pn="PN-A", location="LOC-1")  # type: ignore[arg-type]


def test_cross_tenant_isolation(catalog, seed) -> None:
    seed("stock_position", [_stock_row("PN-A", "LOC-1", 8, D1, tenant="acme")])
    other = TenantContext(tenant_id="other")
    with pytest.raises(FeatureStoreLookupError):
        _store(catalog).get_stock_position(tenant=other, pn="PN-A", location="LOC-1")


def test_iter_inference_keys_returns_distinct_tenant_scoped(catalog, seed) -> None:
    seed("stock_position", [
        _stock_row("PN-A", "LOC-1", 8, D1),
        _stock_row("PN-A", "LOC-1", 9, D2),       # same key, newer date -> still one key
        _stock_row("PN-B", "LOC-2", 5, D1),
        _stock_row("PN-Z", "LOC-9", 5, D1, tenant="other"),  # other tenant -> excluded
    ])
    keys = _store(catalog).iter_inference_keys(tenant=ACME)
    assert keys == [("PN-A", "LOC-1"), ("PN-B", "LOC-2")]  # distinct, sorted, tenant-scoped


def test_iter_inference_keys_empty_when_no_table(catalog) -> None:
    assert _store(catalog).iter_inference_keys(tenant=ACME) == []
