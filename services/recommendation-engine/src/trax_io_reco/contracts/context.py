"""Input + computed context contracts (spec §5.3).

Design decision (Global Constraints): the engine REUSES the feature-store pydantic
schemas for the nine FS-served groups rather than redefining them. It defines only the
gap models (served by InventoryStateProvider), the computed models (DemandProjection,
NetPosition), and the assembled wrapper PartLocationContext.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt
from trax_io_feature_store.schemas import (
    CausalUtilization,
    Criticality,
    DemandHistory,
    InterchangeableGraph,
    LeadTimeDistribution,
    LocationGraph,
    OpenOrdersSnapshot,
    PartAttributes,
    RequisitionSnapshot,
    VendorEconomics,
)

from trax_io_reco.contracts.enums import EvidenceKind


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------- #
# Gap models — served by InventoryStateProvider (v1 stubs, spec §10)
# --------------------------------------------------------------------------- #
class StockPosition(_Base):
    """On-hand stock by (PN, Location). Source: stock_amount #18 (spec §10).

    Dispatchable stock is ``serviceable - allocated_reserved`` (spec §5.5); the other
    fields are excluded from 'available' (in-repair, rental, loan are not dispatchable).
    """

    on_hand: NonNegativeInt
    serviceable: NonNegativeInt
    unserviceable_in_repair: NonNegativeInt = 0
    allocated_reserved: NonNegativeInt = 0
    rental: NonNegativeInt = 0
    loan: NonNegativeInt = 0


class CurrentPolicy(_Base):
    """Existing PN_INVENTORY_LEVEL values. Source: stock_level_upload #19 (alias-corrected)."""

    rop: NonNegativeInt
    eoq: NonNegativeInt
    safety_stock: NonNegativeInt
    max_stock: NonNegativeInt
    replenishment_lead_days: NonNegativeFloat = 0.0


class ScheduledDemandItem(_Base):
    """A forward known-demand item (planned task / WO). Sparse in v1 (spec §10)."""

    due_date: date
    qty: NonNegativeInt
    source_ref: str
    source_kind: EvidenceKind
    ac_type: str | None = None


class AogSignal(_Base):
    """AOG state/history. No extract domain in v1 — pure stub (spec §10)."""

    active: bool = False
    last_event_date: date | None = None
    events_24mo: NonNegativeInt = 0
    last_shortage_at: datetime | None = None


class RepairTat(_Base):
    """Repair-loop turnaround distribution. Derived/stubbed in v1 (spec §10)."""

    mean_days: NonNegativeFloat = 0.0
    p90_days: NonNegativeFloat = 0.0
    n_observations: NonNegativeInt = 0


class TenantPolicyConfig(_Base):
    """Onboarding configuration — NOT provider-served (spec §10)."""

    service_level_by_tier: dict[int, float] = Field(
        default_factory=lambda: {1: 0.995, 2: 0.98, 3: 0.95, 4: 0.92, 5: 0.90}
    )
    holding_cost_rate: float = 0.25
    ordering_cost: float = 150.0
    high_value_threshold: float = 5000.0
    currency: str = "USD"


# --------------------------------------------------------------------------- #
# Computed models
# --------------------------------------------------------------------------- #
class DemandProjection(_Base):
    """Deterministic demand projection as a per-DAY rate plus a parameterized
    distribution (spec §5.3 / §6.4). Callers scale the rate to any window."""

    mean_per_day: float
    std_per_day: float
    dist_kind: Literal["NORMAL", "COMPOUND_POISSON", "NBD", "EMPIRICAL"]
    dist_params: dict[str, float]
    historical_component: float
    scheduled_component: float
    by_aircraft: dict[str, float] = Field(default_factory=dict)
    by_task: dict[str, float] = Field(default_factory=dict)
    basis_window_days: int


class NetPosition(_Base):
    """Net inventory position over a requested window W (spec §5.5)."""

    pn: str
    location: str
    group_id: str | None
    window_days: int
    available: float
    expected_receipts_in_window: float
    projected_demand: float
    net: float
    shortage: float


# --------------------------------------------------------------------------- #
# Assembled wrapper
# --------------------------------------------------------------------------- #
class PartLocationContext(_Base):
    tenant_id: str
    pn: str
    location: str
    stock_position: StockPosition
    current_policy: CurrentPolicy
    vendor_economics: VendorEconomics
    part_attributes: PartAttributes
    criticality: Criticality
    lead_time: LeadTimeDistribution | None = None
    location_graph: LocationGraph | None = None
    open_orders: OpenOrdersSnapshot | None = None
    requisition: RequisitionSnapshot | None = None
    interchange_group: InterchangeableGraph | None = None
    demand_history: DemandHistory
    causal: CausalUtilization | None = None
    scheduled_demand: tuple[ScheduledDemandItem, ...] = ()
    aog_signal: AogSignal = AogSignal()
    repair_tat: RepairTat = RepairTat()
    tenant_policy_config: TenantPolicyConfig = TenantPolicyConfig()

    @property
    def description(self) -> str:
        """Non-empty part description sourced from part attributes (spec §5.2/§10)."""
        return self.part_attributes.description or self.pn
