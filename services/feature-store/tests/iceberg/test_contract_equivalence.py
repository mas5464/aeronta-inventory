"""ADR-0002 contract test: InMemoryFeatureStore and GlueIcebergFeatureStore must be
observationally equivalent.

Seeds the SAME logical data into both backends, then asserts identical pydantic results and
identical tenant-isolation error behavior across every materialized feature group + mapping
shape (flat, decimal/null, bool/None, exploded-aggregate, nested array<struct>, flat->nested).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

pytest.importorskip("pyiceberg")

from trax_io_feature_store.client import (  # noqa: E402
    FeatureStoreLookupError,
    InMemoryFeatureStore,
    MissingTenantContextError,
    TenantContext,
)
from trax_io_feature_store.iceberg_store import GlueIcebergFeatureStore  # noqa: E402
from trax_io_feature_store.schemas import (  # noqa: E402
    CausalUtilization,
    Criticality,
    CurrentPolicy,
    DemandHistory,
    DemandObservation,
    InterchangeableGraph,
    InterchangeEdge,
    LeadTimeDistribution,
    LocationGraph,
    LocationNode,
    OpenOrder,
    OpenOrdersSnapshot,
    PartAttributes,
    StockPosition,
    VendorEconomics,
    WashRateHistory,
    WashRatePoint,
)

T = TenantContext(tenant_id="acme")
D1 = date(2026, 4, 1)

_GROUP = {
    StockPosition: "stock_position",
    CurrentPolicy: "current_policy",
    VendorEconomics: "vendor_economics",
    PartAttributes: "part_attributes",
    Criticality: "criticality",
    LeadTimeDistribution: "lead_time_distribution",
    OpenOrdersSnapshot: "open_orders_snapshot",
    InterchangeableGraph: "interchangeable_graph",
    CausalUtilization: "causal_utilization",
}


def _to_rows(m):
    """Reverse-map a pydantic model to its Iceberg row(s) for seeding the lake."""
    d = m.model_dump()
    if isinstance(m, DemandHistory):
        return "demand_history", [
            {
                "pn": d["pn"], "location": d["location"],
                "interchange_group_id": d["interchange_group_id"], "bucket": o["bucket"],
                "period_start": o["period_start"], "removals": o["removals"], "issues": o["issues"],
                "source": "nightly-extract", "tenant_id": d["tenant_id"],
                "extract_date": d["extract_date"],
            }
            for o in d["observations"]
        ]
    if isinstance(m, LocationGraph):
        return "location_graph", [
            {
                "location": d["location"],
                "related_main_warehouse": d["node"]["related_main_warehouse"],
                "role": d["node"]["role"], "children": d["children"],
                "tenant_id": d["tenant_id"], "extract_date": d["extract_date"],
            }
        ]
    if isinstance(m, WashRateHistory):  # exploded per period_month, like demand_history
        return "wash_rate_history", [
            {
                "pn": d["pn"], "location": d["location"], "period_month": p["period_month"],
                "wash_rate": p["wash_rate"], "tenant_id": d["tenant_id"],
                "extract_date": d["extract_date"],
            }
            for p in d["points"]
        ]
    return _GROUP[type(m)], [d]


# (truth model, in-memory bucket, in-memory key, reader(store) -> model)
CASES = [
    (
        StockPosition(tenant_id="acme", pn="PN-A", location="LOC-1", on_hand=10, serviceable=8,
                      unserviceable_in_repair=1, allocated_reserved=1, rental=0, loan=0,
                      extract_date=D1),
        "stock_position", ("PN-A", "LOC-1"),
        lambda s: s.get_stock_position(tenant=T, pn="PN-A", location="LOC-1"),
    ),
    (
        CurrentPolicy(tenant_id="acme", pn="PN-A", location="LOC-1", rop=5, eoq=4, safety_stock=2,
                      max_stock=40, replenishment_lead_days=21.5, extract_date=D1),
        "current_policy", ("PN-A", "LOC-1"),
        lambda s: s.get_current_policy(tenant=T, pn="PN-A", location="LOC-1"),
    ),
    (
        VendorEconomics(tenant_id="acme", pn="PN-A", vendor="DEFAULT",
                        unit_cost=Decimal("4200.5000"), market_value_unit_cost=None,
                        average_cost=None, kit_cost=None, repair_cost_24mo_avg=Decimal("70.0000"),
                        minimum_order_qty=3, currency="USD", extract_date=D1),
        "vendor_economics", ("PN-A", "DEFAULT"),
        lambda s: s.get_vendor_economics(tenant=T, pn="PN-A", vendor="DEFAULT"),
    ),
    (
        PartAttributes(tenant_id="acme", pn="PN-A", description="Widget", ata_chapter="32",
                       part_class="rotable", shelf_life_days=None, hazardous_material=True,
                       tool_control_item=False, fleet_effectivity_tail_count=12, extract_date=D1),
        "part_attributes", ("PN-A",),
        lambda s: s.get_part_attributes(tenant=T, pn="PN-A"),
    ),
    (
        Criticality(tenant_id="acme", pn="PN-A", raw_essentiality_code="AOG", canonical_tier=1,
                    mapping_source="auto_inferred", extract_date=D1),
        "criticality", ("PN-A",),
        lambda s: s.get_criticality(tenant=T, pn="PN-A"),
    ),
    (
        LeadTimeDistribution(tenant_id="acme", pn="PN-A", vendor="DEFAULT", condition="NEW",
                             promised_lead_days=30.0, realized_mean_days=11.0,
                             realized_p50_days=10.0, realized_p90_days=20.0,
                             realized_p99_days=20.0, promised_vs_actual_delta_mean=-19.0,
                             n_observations=5, extract_date=D1,
                             evidence_status="observed",
                             source="order_plan_closed_orders",
                             grouping_level="part_condition", confidence="low",
                             data_cutoff=date(2026, 3, 28),
                             model_version="supply-cycle-v1",
                             classification_source="explicit_order_type"),
        "lead_time_distribution", ("PN-A", "DEFAULT", "NEW"),
        lambda s: s.get_lead_time_distribution(tenant=T, pn="PN-A", vendor="DEFAULT",
                                               condition="NEW"),
    ),
    (
        LeadTimeDistribution(tenant_id="acme", pn="PN-A", vendor="SHOP-1", condition="REP",
                             promised_lead_days=14.0, realized_mean_days=18.0,
                             realized_p50_days=17.0, realized_p90_days=25.0,
                             realized_p99_days=25.0, promised_vs_actual_delta_mean=4.0,
                             n_observations=4, extract_date=D1,
                             evidence_status="observed",
                             source="order_plan_closed_orders",
                             grouping_level="part_vendor_condition", confidence="low",
                             data_cutoff=date(2026, 3, 30),
                             model_version="supply-cycle-v1",
                             proxy_definition="order_creation_to_last_receipt",
                             classification_source="legacy_order_id_prefix"),
        "lead_time_distribution", ("PN-A", "SHOP-1", "REP"),
        lambda s: s.get_lead_time_distribution(tenant=T, pn="PN-A", vendor="SHOP-1",
                                               condition="REP"),
    ),
    (
        DemandHistory(tenant_id="acme", pn="PN-A", location="LOC-1", interchange_group_id=None,
                      observations=[
                          DemandObservation(bucket="day", period_start=date(2026, 3, 1),
                                            removals=3, issues=2),
                          DemandObservation(bucket="day", period_start=date(2026, 3, 2),
                                            removals=5, issues=0),
                      ], extract_date=D1),
        "demand_history", ("PN-A", "LOC-1"),
        lambda s: s.get_demand_history(tenant=T, pn="PN-A", location="LOC-1"),
    ),
    (
        OpenOrdersSnapshot(tenant_id="acme", pn="PN-A", location="LOC-1",
                           snapshot_at=datetime(2026, 4, 1, 0, 0), orders=[
                               OpenOrder(order_id="O1", order_type="PO", vendor=None, qty_open=7,
                                         expected_rcv_date=date(2026, 4, 10),
                                         location="LOC-1"),
                               OpenOrder(order_id="O2", order_type="RO", vendor=None, qty_open=4,
                                         expected_rcv_date=None, order_line_id="8",
                                         opened_at=datetime(2026, 3, 20, 11, 30),
                                         status="IN_PROGRESS", shop="SHOP-1",
                                         location="LOC-1"),
                           ], total_open_qty=11, extract_date=D1),
        "open_orders_snapshot", ("PN-A", "LOC-1"),
        lambda s: s.get_open_orders_snapshot(tenant=T, pn="PN-A", location="LOC-1"),
    ),
    (
        InterchangeableGraph(tenant_id="acme", pn="PN-2", group_id="PN-1+PN-2+PN-3",
                             members=["PN-1", "PN-2", "PN-3"], edges=[
                                 InterchangeEdge(from_pn="PN-1", to_pn="PN-2", one_way=False),
                                 InterchangeEdge(from_pn="PN-2", to_pn="PN-3", one_way=True),
                             ], extract_date=D1),
        "interchangeable_graph", ("PN-2",),
        lambda s: s.get_interchangeable_graph(tenant=T, pn="PN-2"),
    ),
    (
        LocationGraph(tenant_id="acme", location="YOW",
                      node=LocationNode(location="YOW", related_main_warehouse="YYZ",
                                        role="outstation"), children=[], extract_date=D1),
        "location_graph", ("YOW",),
        lambda s: s.get_location_graph(tenant=T, location="YOW"),
    ),
    (
        WashRateHistory(tenant_id="acme", pn="PN-A", location="LOC-1", points=[
            WashRatePoint(period_month=date(2026, 2, 1), wash_rate=0.10),
            WashRatePoint(period_month=date(2026, 3, 1), wash_rate=0.20),
        ], extract_date=D1),
        "wash_rate_history", ("PN-A", "LOC-1"),
        lambda s: s.get_wash_rate_history(tenant=T, pn="PN-A", location="LOC-1"),
    ),
    (
        CausalUtilization(tenant_id="acme", ac_type="A320", destination="YYZ",
                          observation_date=date(2026, 3, 1), flight_hours=12.5, flight_cycles=4,
                          extract_date=D1),
        "causal_utilization", ("A320", "YYZ"),
        lambda s: s.get_causal_utilization(tenant=T, ac_type="A320", destination="YYZ"),
    ),
]


@pytest.fixture
def both_stores(catalog, seed):
    inmem = InMemoryFeatureStore()
    ice = GlueIcebergFeatureStore(
        catalog=catalog,
        namespace="trax_io",
        table_prefix="",
    )
    for truth, bucket, key, _reader in CASES:
        inmem.seed("acme", bucket, key, truth)
        group, rows = _to_rows(truth)
        seed(group, rows)
    return inmem, ice


@pytest.mark.parametrize("case", CASES, ids=[c[1] for c in CASES])
def test_inmemory_and_iceberg_return_equal_results(both_stores, case) -> None:
    inmem, ice = both_stores
    _truth, _bucket, _key, reader = case
    from_inmem = reader(inmem)
    from_ice = reader(ice)
    assert from_inmem == from_ice, f"{_bucket}: in-memory != iceberg"
    assert from_ice == _truth  # and both equal the seeded truth (lossless round-trip)


def test_tenant_isolation_behaves_identically(both_stores) -> None:
    inmem, ice = both_stores
    other = TenantContext(tenant_id="other")
    # missing tenant -> MissingTenantContextError on both
    for store in (inmem, ice):
        with pytest.raises(MissingTenantContextError):
            store.get_stock_position(tenant=None, pn="PN-A", location="LOC-1")  # type: ignore[arg-type]
    # cross-tenant read -> FeatureStoreLookupError on both
    for store in (inmem, ice):
        with pytest.raises(FeatureStoreLookupError):
            store.get_stock_position(tenant=other, pn="PN-A", location="LOC-1")
    # genuinely missing key -> FeatureStoreLookupError on both
    for store in (inmem, ice):
        with pytest.raises(FeatureStoreLookupError):
            store.get_criticality(tenant=T, pn="DOES-NOT-EXIST")
