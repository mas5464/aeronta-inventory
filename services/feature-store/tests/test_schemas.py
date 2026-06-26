"""One validation smoke test per feature group (design §4.2)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from trax_io_feature_store.schemas import (
    CausalUtilization,
    Criticality,
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
    VendorEconomics,
    WashRateHistory,
    WashRatePoint,
)

EXTRACT = date(2026, 4, 15)


def test_demand_history_validates():
    h = DemandHistory(
        tenant_id="aircanada",
        pn="LRU-CFM56-HPT-BLADE",
        location="YYZ-MAIN",
        interchange_group_id="IG-HPT-BLADE",
        observations=[
            DemandObservation(bucket="month", period_start=date(2026, 3, 1), removals=4, issues=0),
            DemandObservation(bucket="month", period_start=date(2026, 2, 1), removals=2, issues=0),
        ],
        extract_date=EXTRACT,
    )
    assert h.pn == "LRU-CFM56-HPT-BLADE"
    assert len(h.observations) == 2


def test_causal_utilization_validates():
    cu = CausalUtilization(
        tenant_id="aircanada",
        ac_type="B77W",
        destination="HKG",
        observation_date=date(2026, 4, 14),
        flight_hours=14.2,
        flight_cycles=1,
        extract_date=EXTRACT,
    )
    assert cu.flight_cycles == 1


def test_lead_time_distribution_validates():
    ltd = LeadTimeDistribution(
        tenant_id="aircanada",
        pn="P-INT",
        vendor="GE-AVIATION",
        condition="OH",
        promised_lead_days=30.0,
        realized_mean_days=42.5,
        realized_p50_days=40.0,
        realized_p90_days=68.0,
        realized_p99_days=110.0,
        promised_vs_actual_delta_mean=12.5,
        n_observations=48,
        extract_date=EXTRACT,
    )
    assert ltd.promised_vs_actual_delta_mean == 12.5


def test_wash_rate_history_validates():
    wrh = WashRateHistory(
        tenant_id="aircanada",
        pn="P-INT",
        location="YYZ-MAIN",
        points=[
            WashRatePoint(period_month=date(2026, 1, 1), wash_rate=0.22),
            WashRatePoint(period_month=date(2026, 2, 1), wash_rate=0.25),
            WashRatePoint(period_month=date(2026, 3, 1), wash_rate=0.27),
        ],
        extract_date=EXTRACT,
    )
    assert len(wrh.points) == 3
    assert all(0.0 <= p.wash_rate <= 1.0 for p in wrh.points)


def test_vendor_economics_validates():
    ve = VendorEconomics(
        tenant_id="aircanada",
        pn="P-INT",
        vendor="GE-AVIATION",
        unit_cost=Decimal("12500.00"),
        market_value_unit_cost=Decimal("13000.00"),
        average_cost=Decimal("12200.00"),
        repair_cost_24mo_avg=Decimal("4200.00"),
        minimum_order_qty=1,
        extract_date=EXTRACT,
    )
    assert ve.unit_cost == Decimal("12500.00")


def test_part_attributes_validates():
    pa = PartAttributes(
        tenant_id="aircanada",
        pn="P-INT",
        description="HPT BLADE STAGE 1",
        ata_chapter="72",
        part_class="rotable",
        shelf_life_days=None,
        hazardous_material=False,
        tool_control_item=False,
        fleet_effectivity_tail_count=48,
        extract_date=EXTRACT,
    )
    assert pa.part_class == "rotable"


def test_criticality_validates():
    c = Criticality(
        tenant_id="aircanada",
        pn="P-INT",
        raw_essentiality_code="NGO",
        canonical_tier=1,
        mapping_source="auto_inferred",
        extract_date=EXTRACT,
    )
    assert c.canonical_tier == 1


def test_interchangeable_graph_validates():
    g = InterchangeableGraph(
        tenant_id="aircanada",
        pn="P-INT",
        group_id="IG-HPT-BLADE",
        members=["P-INT", "P-INT-ALT"],
        edges=[InterchangeEdge(from_pn="P-INT-ALT", to_pn="P-INT", one_way=True)],
        extract_date=EXTRACT,
    )
    assert g.edges[0].one_way is True


def test_location_graph_validates():
    lg = LocationGraph(
        tenant_id="aircanada",
        location="YYZ-MAIN",
        node=LocationNode(location="YYZ-MAIN", related_main_warehouse=None, role="main"),
        children=["YUL-OUT", "YVR-OUT"],
        extract_date=EXTRACT,
    )
    assert lg.node.role == "main"


def test_open_orders_snapshot_validates():
    snap = OpenOrdersSnapshot(
        tenant_id="aircanada",
        pn="P-INT",
        location="YYZ-MAIN",
        snapshot_at=datetime(2026, 4, 15, 6, 0, tzinfo=timezone.utc),
        orders=[
            OpenOrder(
                order_id="PO-1001",
                order_type="PO",
                vendor="GE-AVIATION",
                qty_open=2,
                expected_rcv_date=date(2026, 5, 20),
            )
        ],
        total_open_qty=2,
        extract_date=EXTRACT,
    )
    assert snap.total_open_qty == 2
