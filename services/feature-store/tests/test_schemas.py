"""One validation smoke test per feature group (design §4.2)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from trax_io_feature_store.schemas import (
    CausalUtilization,
    Criticality,
    CurrentPolicy,
    DemandHistory,
    DemandObservation,
    FeatureBundle,
    InterchangeableGraph,
    InterchangeEdge,
    LeadTimeDistribution,
    LocationGraph,
    LocationNode,
    OpenOrder,
    OpenOrdersSnapshot,
    PartAttributes,
    RequisitionLine,
    RequisitionSnapshot,
    StockPosition,
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


def test_demand_history_preserves_window_events_and_units_separately() -> None:
    history = DemandHistory(
        tenant_id="aircanada",
        pn="FILTER",
        location="YYZ-MAIN",
        observation_start=date(2023, 4, 16),
        observation_end=date(2026, 4, 16),
        event_count_source="observed",
        observations=[
            DemandObservation(
                bucket="month",
                period_start=date(2026, 4, 1),
                issues=7,
                issue_events=1,
                removal_events=0,
            )
        ],
        extract_date=EXTRACT,
    )

    assert history.observation_start == date(2023, 4, 16)
    assert history.observation_end == date(2026, 4, 16)
    assert history.observations[0].issues == 7
    assert history.observations[0].issue_events == 1


def test_legacy_demand_history_payload_keeps_validating() -> None:
    history = DemandHistory.model_validate(
        {
            "tenant_id": "aircanada",
            "pn": "LEGACY",
            "location": "YYZ-MAIN",
            "observations": [
                {
                    "bucket": "month",
                    "period_start": "2026-03-01",
                    "removals": 2,
                    "issues": 0,
                }
            ],
            "extract_date": "2026-04-15",
        }
    )

    assert history.observation_start is None
    assert history.observations[0].removal_events is None


def test_demand_history_rejects_only_one_observation_boundary() -> None:
    with pytest.raises(ValueError, match="supplied together"):
        DemandHistory(
            tenant_id="acme",
            pn="PN1",
            location="YYZ",
            observation_start=date(2024, 1, 1),
            observations=[],
            extract_date=date(2024, 4, 1),
        )


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
        snapshot_at=datetime(2026, 4, 15, 6, 0, tzinfo=UTC),
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


def test_open_order_repair_evidence_is_additive_and_legacy_safe():
    legacy = OpenOrder(
        order_id="PO-1001",
        order_type="PO",
        vendor="GE-AVIATION",
        qty_open=2,
        expected_rcv_date=date(2026, 5, 20),
    )
    assert legacy.order_line_id is None
    assert legacy.opened_at is None
    assert legacy.status == "OPEN"
    assert legacy.serial_number is None
    assert legacy.shop is None
    assert legacy.location is None

    repair = OpenOrder(
        order_id="RO-2001",
        order_type="RO",
        vendor="SHOP-9",
        qty_open=1,
        expected_rcv_date=None,
        order_line_id="7",
        opened_at=datetime(2026, 4, 2, 13, 15, tzinfo=UTC),
        status="in_progress",
        serial_number="SER-9",
        shop="SHOP-9",
        location="YYZ",
    )
    assert repair.order_line_id == "7"
    assert repair.opened_at == datetime(2026, 4, 2, 13, 15, tzinfo=UTC)
    assert repair.status == "IN_PROGRESS"
    assert repair.serial_number == "SER-9"
    assert repair.shop == "SHOP-9"
    assert repair.location == "YYZ"


def test_requisition_snapshot_validates():
    snap = RequisitionSnapshot(
        tenant_id="aircanada",
        pn="P-INT",
        location="YYZ-MAIN",
        snapshot_at=datetime(2026, 4, 15, 6, 0, tzinfo=UTC),
        lines=[
            RequisitionLine(
                requisition_id="REQ_1001_1",
                qty_needed=3,
                need_by=date(2026, 5, 1),
                alt_source_location="YOW",
            )
        ],
        total_qty_needed=3,
        extract_date=EXTRACT,
    )
    assert snap.lines[0].requisition_id == "REQ_1001_1"
    assert snap.total_qty_needed == 3


def _complete_feature_bundle() -> FeatureBundle:
    tenant_id = "aircanada"
    pn = "P-INT"
    location = "YYZ-MAIN"
    return FeatureBundle(
        tenant_id=tenant_id,
        pn=pn,
        location=location,
        stock_position=StockPosition(
            tenant_id=tenant_id,
            pn=pn,
            location=location,
            on_hand=4,
            serviceable=4,
            extract_date=EXTRACT,
        ),
        current_policy=CurrentPolicy(
            tenant_id=tenant_id,
            pn=pn,
            location=location,
            rop=2,
            eoq=1,
            safety_stock=1,
            max_stock=5,
            extract_date=EXTRACT,
        ),
        demand_history=DemandHistory(
            tenant_id=tenant_id,
            pn=pn,
            location=location,
            observations=[],
            extract_date=EXTRACT,
        ),
        open_orders_snapshot=OpenOrdersSnapshot(
            tenant_id=tenant_id,
            pn=pn,
            location=location,
            snapshot_at=datetime(2026, 4, 15, 6, 0, tzinfo=UTC),
            orders=[],
            total_open_qty=0,
            extract_date=EXTRACT,
        ),
        requisition_snapshot=RequisitionSnapshot(
            tenant_id=tenant_id,
            pn=pn,
            location=location,
            snapshot_at=datetime(2026, 4, 15, 6, 0, tzinfo=UTC),
            lines=[],
            total_qty_needed=0,
            extract_date=EXTRACT,
        ),
        location_graph=LocationGraph(
            tenant_id=tenant_id,
            location=location,
            node=LocationNode(location=location, role="main"),
            extract_date=EXTRACT,
        ),
        part_attributes=PartAttributes(
            tenant_id=tenant_id,
            pn=pn,
            extract_date=EXTRACT,
        ),
        criticality=Criticality(
            tenant_id=tenant_id,
            pn=pn,
            raw_essentiality_code="4",
            canonical_tier=4,
            extract_date=EXTRACT,
        ),
        interchangeable_graph=InterchangeableGraph(
            tenant_id=tenant_id,
            pn=pn,
            group_id="IG-1",
            extract_date=EXTRACT,
        ),
        vendor_economics={
            "DEFAULT": VendorEconomics(
                tenant_id=tenant_id,
                pn=pn,
                vendor="DEFAULT",
                unit_cost=Decimal("10"),
                extract_date=EXTRACT,
            )
        },
        lead_time_distribution={
            "DEFAULT|NEW": LeadTimeDistribution(
                tenant_id=tenant_id,
                pn=pn,
                vendor="DEFAULT",
                condition="NEW",
                promised_lead_days=21,
                realized_mean_days=21,
                realized_p50_days=21,
                realized_p90_days=21,
                realized_p99_days=21,
                promised_vs_actual_delta_mean=0,
                n_observations=0,
                extract_date=EXTRACT,
            )
        },
    )


@pytest.mark.parametrize(
    ("field", "identity_field"),
    [
        *[
            (field, identity_field)
            for field in (
                "stock_position",
                "current_policy",
                "demand_history",
                "open_orders_snapshot",
                "requisition_snapshot",
            )
            for identity_field in ("tenant_id", "pn", "location")
        ],
        *[
            (field, identity_field)
            for field in (
                "part_attributes",
                "criticality",
                "interchangeable_graph",
            )
            for identity_field in ("tenant_id", "pn")
        ],
        ("location_graph", "tenant_id"),
        ("location_graph", "location"),
    ],
)
def test_feature_bundle_rejects_nested_identity_mismatch(
    field: str,
    identity_field: str,
) -> None:
    payload = _complete_feature_bundle().model_dump()
    payload[field][identity_field] = "WRONG"

    with pytest.raises(ValueError, match=field):
        FeatureBundle.model_validate(payload)


def test_feature_bundle_rejects_location_node_identity_mismatch() -> None:
    payload = _complete_feature_bundle().model_dump()
    payload["location_graph"]["node"]["location"] = "WRONG"

    with pytest.raises(ValueError, match="location_graph.node"):
        FeatureBundle.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "identity_field"),
    [
        ("vendor_economics", "tenant_id"),
        ("vendor_economics", "pn"),
        ("lead_time_distribution", "tenant_id"),
        ("lead_time_distribution", "pn"),
    ],
)
def test_feature_bundle_rejects_vendor_feature_identity_mismatch(
    field: str,
    identity_field: str,
) -> None:
    payload = _complete_feature_bundle().model_dump()
    only_value = next(iter(payload[field].values()))
    only_value[identity_field] = "WRONG"

    with pytest.raises(ValueError, match=field):
        FeatureBundle.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "wrong_key"),
    [
        ("vendor_economics", "OTHER"),
        ("lead_time_distribution", "OTHER|NEW"),
    ],
)
def test_feature_bundle_rejects_vendor_map_key_mismatch(
    field: str,
    wrong_key: str,
) -> None:
    payload = _complete_feature_bundle().model_dump()
    value = next(iter(payload[field].values()))
    payload[field] = {wrong_key: value}

    with pytest.raises(ValueError, match=field):
        FeatureBundle.model_validate(payload)
