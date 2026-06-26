"""Pydantic v2 models for the 10 feature groups called out in design §4.2.

These are the schemas only — no materialization logic, no ETL. The models
are the authoritative contract for what flows through the Iceberg + DynamoDB
layers; Glue jobs and the online-layer writer will serialize/deserialize
against them.

Every model carries `tenant_id` so that mis-tenanted rows fail loudly at
validation time. The production Iceberg layout partitions on
`(tenant_id, extract_date)`; `extract_date` is captured on each row.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt


class _Base(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


# ---------------------------------------------------------------------------
# 1. demand_history
# ---------------------------------------------------------------------------


class DemandObservation(_Base):
    """One bucketed demand observation (day/week/month)."""

    bucket: Literal["day", "week", "month"]
    period_start: date
    removals: NonNegativeInt = 0  # rotable
    issues: NonNegativeInt = 0  # expendable


class DemandHistory(_Base):
    """Rolled removals + issues per (tenant, pn, location).

    Source: AC_PN_TRANSACTION_HISTORY + PN_INVENTORY_HISTORY (design §4.3).
    Interchange rollup is applied before policy calc but stored separately.
    """

    tenant_id: str
    pn: str
    location: str
    interchange_group_id: str | None = None
    observations: list[DemandObservation] = Field(default_factory=list)
    extract_date: date
    source: Literal["nightly_extract", "event_cdc"] = "nightly_extract"


# ---------------------------------------------------------------------------
# 2. causal_utilization
# ---------------------------------------------------------------------------


class CausalUtilization(_Base):
    """Flight hours + cycles by AC type x destination x day.

    Source: AC_ACTUAL_FLIGHTS x AC_MASTER (design §4.3).
    """

    tenant_id: str
    ac_type: str
    destination: str
    observation_date: date
    flight_hours: NonNegativeFloat
    flight_cycles: NonNegativeInt
    extract_date: date


# ---------------------------------------------------------------------------
# 3. lead_time_distribution
# ---------------------------------------------------------------------------


class LeadTimeDistribution(_Base):
    """Empirical lead-time distribution per (pn, vendor, condition).

    Blends PN_VENDOR_PRICE.lead_days (promised) with realized closed-order
    lead days. The promised-vs-actual delta is the highest-signal safety-stock
    driver (design §4.3).
    """

    tenant_id: str
    pn: str
    vendor: str
    condition: Literal["NEW", "SV", "OH", "AR", "USED", "REP"]
    promised_lead_days: NonNegativeFloat
    realized_mean_days: NonNegativeFloat
    realized_p50_days: NonNegativeFloat
    realized_p90_days: NonNegativeFloat
    realized_p99_days: NonNegativeFloat
    promised_vs_actual_delta_mean: float
    n_observations: NonNegativeInt
    extract_date: date


# ---------------------------------------------------------------------------
# 4. wash_rate_history
# ---------------------------------------------------------------------------


class WashRatePoint(_Base):
    period_month: date  # first day of month
    wash_rate: float = Field(ge=0.0, le=1.0)


class WashRateHistory(_Base):
    """Trend of the PartMaster wash rate formula (design §4.3)."""

    tenant_id: str
    pn: str
    location: str
    points: list[WashRatePoint] = Field(default_factory=list)
    extract_date: date


# ---------------------------------------------------------------------------
# 5. vendor_economics
# ---------------------------------------------------------------------------


class VendorEconomics(_Base):
    """Cost + commercial terms per (pn, vendor).

    Source: PN_VENDOR_PRICE, ORDER_INVOICE, PKG_TRAX_PTC.getKitCost (§4.3).
    """

    tenant_id: str
    pn: str
    vendor: str
    unit_cost: Decimal
    market_value_unit_cost: Decimal | None = None
    average_cost: Decimal | None = None
    kit_cost: Decimal | None = None
    repair_cost_24mo_avg: Decimal | None = None
    minimum_order_qty: NonNegativeInt = 1
    currency: str = "USD"
    extract_date: date


# ---------------------------------------------------------------------------
# 6. part_attributes
# ---------------------------------------------------------------------------


class PartAttributes(_Base):
    """Hard-constraint + descriptive attributes per PN (design §4.3)."""

    tenant_id: str
    pn: str
    description: str | None = None
    ata_chapter: str | None = None
    part_class: Literal["rotable", "repairable", "expendable", "consumable"] | None = None
    shelf_life_days: NonNegativeInt | None = None
    hazardous_material: bool = False
    tool_control_item: bool = False
    fleet_effectivity_tail_count: NonNegativeInt | None = None
    extract_date: date


# ---------------------------------------------------------------------------
# 7. criticality
# ---------------------------------------------------------------------------


class Criticality(_Base):
    """Essentiality code normalized to canonical 5-tier scale (design §4.3)."""

    tenant_id: str
    pn: str
    raw_essentiality_code: str
    canonical_tier: Literal[1, 2, 3, 4, 5]
    mapping_source: Literal["auto_inferred", "planner_override"] = "auto_inferred"
    extract_date: date


# ---------------------------------------------------------------------------
# 8. interchangeable_graph
# ---------------------------------------------------------------------------


class InterchangeEdge(_Base):
    from_pn: str
    to_pn: str
    one_way: bool = False


class InterchangeableGraph(_Base):
    """Interchangeability rollup per PN (design §4.3).

    The rollup is applied before the policy calc; one-way chains are honored
    or else stock is over-sized.
    """

    tenant_id: str
    pn: str
    group_id: str
    members: list[str] = Field(default_factory=list)
    edges: list[InterchangeEdge] = Field(default_factory=list)
    extract_date: date


# ---------------------------------------------------------------------------
# 9. location_graph
# ---------------------------------------------------------------------------


class LocationNode(_Base):
    location: str
    related_main_warehouse: str | None = None
    role: Literal["main", "outstation"] = "outstation"


class LocationGraph(_Base):
    """Location hierarchy from LOCATION_MASTER.RELATED_MAIN_WAREHOUSE."""

    tenant_id: str
    location: str
    node: LocationNode
    children: list[str] = Field(default_factory=list)
    extract_date: date


# ---------------------------------------------------------------------------
# 10. open_orders_snapshot
# ---------------------------------------------------------------------------


class OpenOrder(_Base):
    order_id: str
    order_type: Literal["PO", "RO"]
    vendor: str | None = None
    qty_open: NonNegativeInt
    expected_rcv_date: date | None = None


class OpenOrdersSnapshot(_Base):
    """Open POs + ROs per (pn, location) as of snapshot_at."""

    tenant_id: str
    pn: str
    location: str
    snapshot_at: datetime
    orders: list[OpenOrder] = Field(default_factory=list)
    total_open_qty: NonNegativeInt
    extract_date: date
