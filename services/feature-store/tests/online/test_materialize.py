"""materialize_bundle: assemble a FeatureBundle from an offline FeatureStoreClient, then verify
the offline -> online round-trip (moto-backed; skips without deps)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

pytest.importorskip("boto3")
pytest.importorskip("moto")

from trax_io_feature_store.client import InMemoryFeatureStore, TenantContext  # noqa: E402
from trax_io_feature_store.materialize import materialize_bundle  # noqa: E402
from trax_io_feature_store.online_store import DynamoDbOnlineStore  # noqa: E402
from trax_io_feature_store.schemas import (  # noqa: E402
    Criticality,
    CurrentPolicy,
    DemandHistory,
    DemandObservation,
    InterchangeableGraph,
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
)

ACME = TenantContext(tenant_id="acme")
D1 = date(2026, 4, 1)
PN, LOC = "PN-A", "LOC-1"


def _seeded_offline() -> InMemoryFeatureStore:
    fs = InMemoryFeatureStore()
    fs.seed("acme", "stock_position", (PN, LOC), StockPosition(
        tenant_id="acme", pn=PN, location=LOC, on_hand=10, serviceable=8,
        unserviceable_in_repair=1, allocated_reserved=1, rental=0, loan=0, extract_date=D1))
    fs.seed("acme", "current_policy", (PN, LOC), CurrentPolicy(
        tenant_id="acme", pn=PN, location=LOC, rop=5, eoq=4, safety_stock=2, max_stock=40,
        replenishment_lead_days=21.0, extract_date=D1))
    fs.seed("acme", "demand_history", (PN, LOC), DemandHistory(
        tenant_id="acme", pn=PN, location=LOC, observations=[
            DemandObservation(bucket="day", period_start=date(2026, 3, 1), removals=3, issues=2)],
        extract_date=D1))
    # open order names vendor V1 -> the bundle must also fetch V1's economics + lead time
    fs.seed("acme", "open_orders_snapshot", (PN, LOC), OpenOrdersSnapshot(
        tenant_id="acme", pn=PN, location=LOC, snapshot_at=datetime(2026, 4, 1, 0, 0),
        orders=[OpenOrder(order_id="O1", order_type="PO", vendor="V1", qty_open=5,
                          expected_rcv_date=None)],
        total_open_qty=5, extract_date=D1))
    fs.seed("acme", "requisition_snapshot", (PN, LOC), RequisitionSnapshot(
        tenant_id="acme", pn=PN, location=LOC, snapshot_at=datetime(2026, 4, 1, 0, 0),
        lines=[RequisitionLine(requisition_id="R1", qty_needed=3,
                               need_by=date(2026, 4, 20))],
        total_qty_needed=3, extract_date=D1))
    fs.seed("acme", "location_graph", (LOC,), LocationGraph(
        tenant_id="acme", location=LOC, node=LocationNode(location=LOC, role="main"),
        children=[], extract_date=D1))
    fs.seed("acme", "part_attributes", (PN,), PartAttributes(
        tenant_id="acme", pn=PN, part_class="rotable", extract_date=D1))
    fs.seed("acme", "criticality", (PN,), Criticality(
        tenant_id="acme", pn=PN, raw_essentiality_code="AOG", canonical_tier=1, extract_date=D1))
    fs.seed("acme", "interchangeable_graph", (PN,), InterchangeableGraph(
        tenant_id="acme", pn=PN, group_id=PN, members=[PN], edges=[], extract_date=D1))
    for vendor in ("DEFAULT", "V1"):
        fs.seed("acme", "vendor_economics", (PN, vendor), VendorEconomics(
            tenant_id="acme", pn=PN, vendor=vendor, unit_cost="100", extract_date=D1))
        fs.seed("acme", "lead_time_distribution", (PN, vendor, "NEW"), LeadTimeDistribution(
            tenant_id="acme", pn=PN, vendor=vendor, condition="NEW", promised_lead_days=30.0,
            realized_mean_days=30.0, realized_p50_days=30.0, realized_p90_days=39.0,
            realized_p99_days=48.0, promised_vs_actual_delta_mean=0.0, n_observations=0,
            extract_date=D1))
        fs.seed("acme", "lead_time_distribution", (PN, vendor, "REP"), LeadTimeDistribution(
            tenant_id="acme", pn=PN, vendor=vendor, condition="REP", promised_lead_days=12.0,
            realized_mean_days=12.0, realized_p50_days=12.0, realized_p90_days=12.0,
            realized_p99_days=12.0, promised_vs_actual_delta_mean=None, n_observations=0,
            extract_date=D1))
    return fs


def test_materialize_packs_all_relevant_features() -> None:
    bundle = materialize_bundle(_seeded_offline(), tenant=ACME, pn=PN, location=LOC)
    assert bundle.stock_position.serviceable == 8
    assert bundle.current_policy.rop == 5
    assert bundle.demand_history.observations[0].removals == 3
    assert bundle.location_graph.node.role == "main"
    assert bundle.part_attributes.part_class == "rotable"
    assert bundle.criticality.canonical_tier == 1
    assert bundle.interchangeable_graph.group_id == PN
    assert bundle.requisition_snapshot.total_qty_needed == 3
    # DEFAULT + the open-order vendor V1 both pulled in
    assert set(bundle.vendor_economics) == {"DEFAULT", "V1"}
    assert set(bundle.lead_time_distribution) == {
        "DEFAULT|NEW",
        "DEFAULT|REP",
        "V1|NEW",
        "V1|REP",
    }


def test_materialize_windows_demand_history() -> None:
    # Full offline history must be trimmed to the recent window so the online item stays thin.
    fs = InMemoryFeatureStore()
    obs = [DemandObservation(bucket="day", period_start=date(2026, 1, 1) + timedelta(days=i),
                             removals=i, issues=0) for i in range(100)]
    fs.seed("acme", "demand_history", (PN, LOC), DemandHistory(
        tenant_id="acme", pn=PN, location=LOC, observations=obs, extract_date=D1))
    bundle = materialize_bundle(fs, tenant=ACME, pn=PN, location=LOC, demand_window=10)
    kept = bundle.demand_history.observations
    assert len(kept) == 10  # windowed
    # the 10 most recent (latest period_start) are kept, in order
    assert kept[0].period_start == date(2026, 1, 1) + timedelta(days=90)
    assert kept[-1].period_start == date(2026, 1, 1) + timedelta(days=99)


def test_materialize_preserves_complete_configured_demand_window_by_default() -> None:
    fs = InMemoryFeatureStore()
    observations = [
        DemandObservation(
            bucket="month",
            period_start=date(2023 + i // 12, i % 12 + 1, 1),
            issues=i,
            removal_events=0,
            issue_events=1 if i else 0,
        )
        for i in range(36)
    ]
    history = DemandHistory(
        tenant_id="acme",
        pn=PN,
        location=LOC,
        observation_start=date(2023, 1, 1),
        observation_end=date(2025, 12, 31),
        bucket="month",
        event_count_source="observed",
        observations=observations,
        extract_date=D1,
    )
    fs.seed("acme", "demand_history", (PN, LOC), history)

    bundle = materialize_bundle(fs, tenant=ACME, pn=PN, location=LOC)

    assert bundle.demand_history == history
    assert len(bundle.demand_history.observations) == 36
    assert bundle.demand_history.observation_start == date(2023, 1, 1)


def test_explicit_demand_cap_advances_configured_start_bound() -> None:
    fs = InMemoryFeatureStore()
    observations = [
        DemandObservation(
            bucket="day",
            period_start=date(2026, 1, 1) + timedelta(days=i),
            issues=1,
            removal_events=0,
            issue_events=1,
        )
        for i in range(100)
    ]
    fs.seed("acme", "demand_history", (PN, LOC), DemandHistory(
        tenant_id="acme", pn=PN, location=LOC,
        observation_start=date(2026, 1, 1), observation_end=date(2026, 4, 10),
        bucket="day", event_count_source="observed", observations=observations,
        extract_date=D1))

    bundle = materialize_bundle(
        fs,
        tenant=ACME,
        pn=PN,
        location=LOC,
        demand_window=10,
    )

    assert bundle.demand_history.observation_start == date(2026, 4, 1)
    assert bundle.demand_history.observation_end == date(2026, 4, 10)
    assert len(bundle.demand_history.observations) == 10


def test_materialize_tolerates_absent_groups() -> None:
    # Nothing seeded for this key -> every optional group None, maps empty (no crash).
    bundle = materialize_bundle(InMemoryFeatureStore(), tenant=ACME, pn="GHOST", location="NOWHERE")
    assert bundle.stock_position is None and bundle.current_policy is None
    assert bundle.part_attributes is None and bundle.criticality is None
    assert bundle.vendor_economics == {} and bundle.lead_time_distribution == {}


def test_offline_to_online_roundtrip(online_table) -> None:
    # The bundle materialized from the offline store survives a put/get through DynamoDB unchanged.
    materialized = materialize_bundle(_seeded_offline(), tenant=ACME, pn=PN, location=LOC)
    store = DynamoDbOnlineStore(table=online_table)
    stage = store.begin_population(tenant=ACME)
    store.put_bundle(materialized, stage=stage)
    store.commit_population(stage=stage, key_count=1)
    assert store.get_bundle(tenant=ACME, pn=PN, location=LOC) == materialized
