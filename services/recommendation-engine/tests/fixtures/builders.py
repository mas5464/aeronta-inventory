"""Reusable builders for feature-store + inventory-state test data.

Constructs the real feature-store pydantic schemas and seeds the real
InMemoryFeatureStore (keys follow the lookup-arg order, per recon) plus the engine's
InMemoryInventoryState. Used by the data-layer tests and the eight acceptance scenarios.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from trax_io_feature_store import InMemoryFeatureStore
from trax_io_feature_store.schemas import (
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
)

from trax_io_reco.contracts.context import AogSignal, RepairTat, ScheduledDemandItem
from trax_io_reco.data.inventory_state import InMemoryInventoryState

EXTRACT_DATE = date(2026, 4, 1)
DEFAULT_VENDOR = "DEFAULT"


def demand_history(
    *, tenant_id: str, pn: str, location: str, monthly_units: list[int], rotable: bool = False
) -> DemandHistory:
    obs = [
        DemandObservation(
            bucket="month",
            period_start=date(2025, (i % 12) + 1, 1),
            removals=(u if rotable else 0),
            issues=(0 if rotable else u),
        )
        for i, u in enumerate(monthly_units)
    ]
    return DemandHistory(
        tenant_id=tenant_id, pn=pn, location=location, observations=obs, extract_date=EXTRACT_DATE
    )


def part_attributes(
    *,
    tenant_id: str,
    pn: str,
    description: str | None = "WIDGET",
    part_class: str | None = "expendable",
    shelf_life_days: int | None = None,
    hazmat: bool = False,
    tool: bool = False,
) -> PartAttributes:
    return PartAttributes(
        tenant_id=tenant_id,
        pn=pn,
        description=description,
        part_class=part_class,
        shelf_life_days=shelf_life_days,
        hazardous_material=hazmat,
        tool_control_item=tool,
        extract_date=EXTRACT_DATE,
    )


def criticality(*, tenant_id: str, pn: str, tier: int = 4) -> Criticality:
    return Criticality(
        tenant_id=tenant_id, pn=pn, raw_essentiality_code=str(tier),
        canonical_tier=tier, extract_date=EXTRACT_DATE,  # type: ignore[arg-type]
    )


def vendor_economics(
    *, tenant_id: str, pn: str, vendor: str = DEFAULT_VENDOR, unit_cost: str = "100",
    min_oq: int = 1,
) -> VendorEconomics:
    return VendorEconomics(
        tenant_id=tenant_id, pn=pn, vendor=vendor, unit_cost=Decimal(unit_cost),
        minimum_order_qty=min_oq, extract_date=EXTRACT_DATE,
    )


def lead_time(
    *, tenant_id: str, pn: str, vendor: str = DEFAULT_VENDOR, mean_days: float = 21.0,
    n_obs: int = 10, condition: str = "NEW",
) -> LeadTimeDistribution:
    return LeadTimeDistribution(
        tenant_id=tenant_id, pn=pn, vendor=vendor, condition=condition,  # type: ignore[arg-type]
        promised_lead_days=mean_days, realized_mean_days=mean_days, realized_p50_days=mean_days,
        realized_p90_days=mean_days * 1.3, realized_p99_days=mean_days * 1.6,
        promised_vs_actual_delta_mean=0.0, n_observations=n_obs, extract_date=EXTRACT_DATE,
    )


def open_orders(
    *, tenant_id: str, pn: str, location: str, qty: int = 0, vendor: str = DEFAULT_VENDOR,
    rcv_date: date | None = None, order_type: str = "PO",
) -> OpenOrdersSnapshot:
    orders = (
        [OpenOrder(order_id="O1", order_type=order_type, vendor=vendor, qty_open=qty,  # type: ignore[arg-type]
                   expected_rcv_date=rcv_date)]
        if qty > 0 else []
    )
    return OpenOrdersSnapshot(
        tenant_id=tenant_id, pn=pn, location=location, snapshot_at=datetime(2026, 4, 1),
        orders=orders, total_open_qty=qty, extract_date=EXTRACT_DATE,
    )


def location_graph(
    *, tenant_id: str, location: str, main_warehouse: str | None = None, role: str = "outstation"
) -> LocationGraph:
    return LocationGraph(
        tenant_id=tenant_id, location=location,
        node=LocationNode(location=location, related_main_warehouse=main_warehouse, role=role),  # type: ignore[arg-type]
        extract_date=EXTRACT_DATE,
    )


def interchange(
    *, tenant_id: str, pn: str, group_id: str, members: list[str],
    edges: list[tuple[str, str, bool]] | None = None,
) -> InterchangeableGraph:
    return InterchangeableGraph(
        tenant_id=tenant_id, pn=pn, group_id=group_id, members=members,
        edges=[InterchangeEdge(from_pn=a, to_pn=b, one_way=ow) for a, b, ow in (edges or [])],
        extract_date=EXTRACT_DATE,
    )


def seed_part(
    fs: InMemoryFeatureStore,
    inv: InMemoryInventoryState,
    *,
    tenant_id: str,
    pn: str,
    location: str,
    monthly_units: list[int],
    rotable: bool = False,
    tier: int = 4,
    unit_cost: str = "100",
    min_oq: int = 1,
    vendor: str = DEFAULT_VENDOR,
    part_class: str = "expendable",
    description: str | None = "WIDGET",
    shelf_life_days: int | None = None,
    lead_mean_days: float = 21.0,
    serviceable: int = 0,
    allocated: int = 0,
    in_repair: int = 0,
    current_policy: tuple[int, int, int, int] = (5, 5, 2, 10),
    open_qty: int = 0,
    open_rcv_date: date | None = None,
    scheduled: list[ScheduledDemandItem] | None = None,
    aog: AogSignal | None = None,
    repair_tat: RepairTat | None = None,
) -> None:
    """Seed every required group for one (pn, location) with sensible defaults."""
    fs.seed(tenant_id, "demand_history", (pn, location),
            demand_history(tenant_id=tenant_id, pn=pn, location=location,
                           monthly_units=monthly_units, rotable=rotable))
    fs.seed(tenant_id, "part_attributes", (pn,),
            part_attributes(tenant_id=tenant_id, pn=pn, description=description,
                            part_class=part_class, shelf_life_days=shelf_life_days))
    fs.seed(tenant_id, "criticality", (pn,), criticality(tenant_id=tenant_id, pn=pn, tier=tier))
    fs.seed(tenant_id, "vendor_economics", (pn, vendor),
            vendor_economics(tenant_id=tenant_id, pn=pn, vendor=vendor, unit_cost=unit_cost,
                             min_oq=min_oq))
    fs.seed(tenant_id, "lead_time_distribution", (pn, vendor, "NEW"),
            lead_time(tenant_id=tenant_id, pn=pn, vendor=vendor, mean_days=lead_mean_days))
    fs.seed(tenant_id, "open_orders_snapshot", (pn, location),
            open_orders(tenant_id=tenant_id, pn=pn, location=location, qty=open_qty,
                        vendor=vendor, rcv_date=open_rcv_date))

    rop, eoq, ss, mx = current_policy
    on_hand = serviceable + in_repair
    # stock_position + current_policy are now Feature-Store groups (Phase 2 promotion).
    fs.seed(tenant_id, "stock_position", (pn, location),
            StockPosition(tenant_id=tenant_id, pn=pn, location=location, on_hand=on_hand,
                          serviceable=serviceable, allocated_reserved=allocated,
                          unserviceable_in_repair=in_repair, extract_date=EXTRACT_DATE))
    fs.seed(tenant_id, "current_policy", (pn, location),
            CurrentPolicy(tenant_id=tenant_id, pn=pn, location=location, rop=rop, eoq=eoq,
                          safety_stock=ss, max_stock=mx, extract_date=EXTRACT_DATE))
    if scheduled is not None:
        inv.seed(tenant_id, "scheduled_demand", (pn, location), tuple(scheduled))
    if aog is not None:
        inv.seed(tenant_id, "aog_signal", (pn, location), aog)
    if repair_tat is not None:
        inv.seed(tenant_id, "repair_tat", (pn,), repair_tat)
