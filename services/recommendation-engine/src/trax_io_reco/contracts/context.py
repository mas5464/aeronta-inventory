"""Input + computed context contracts (spec §5.3).

Design decision (Global Constraints): the engine REUSES the feature-store pydantic
schemas for the nine FS-served groups rather than redefining them. It defines only the
gap models (served by InventoryStateProvider), the computed models (DemandProjection,
NetPosition), and the assembled wrapper PartLocationContext.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    StringConstraints,
    model_validator,
)
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
    distribution (spec §5.3 / §6.4). Callers scale the rate to any window.

    ``forecast_model`` and ``forecast_version`` identify the implementation that
    actually produced this projection.  They are optional only for compatibility
    with older/custom projectors; candidate-frontier generation fails closed when
    either value is unavailable.
    """

    mean_per_day: float
    std_per_day: float
    dist_kind: Literal["NORMAL", "COMPOUND_POISSON", "NBD", "EMPIRICAL"]
    dist_params: dict[str, float]
    historical_component: float
    scheduled_component: float
    # Scheduled demand remains discrete dated evidence. It is intentionally not
    # blended permanently into ``mean_per_day``; horizon consumers filter it.
    scheduled_demand_total: float = 0.0
    scheduled_by_date: dict[date, float] = Field(default_factory=dict)
    by_aircraft: dict[str, float] = Field(default_factory=dict)
    by_task: dict[str, float] = Field(default_factory=dict)
    basis_window_days: int
    forecast_model: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None
    ) = None
    forecast_version: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None
    ) = None

    @model_validator(mode="after")
    def _paired_forecast_identity(self) -> DemandProjection:
        if (self.forecast_model is None) != (self.forecast_version is None):
            raise ValueError("forecast_model and forecast_version must be supplied together")
        return self


class NetPositionMember(_Base):
    """Exact demand/supply contribution from one key in a served net position.

    A single-key net position carries one member.  An interchange roll-up carries
    every member that was summed so downstream evidence can reconcile the served
    recommendation without re-reading or re-running the engine.
    """

    pn: str
    location: str
    projection_kind: Literal["NORMAL", "COMPOUND_POISSON", "NBD", "EMPIRICAL"]
    projected_historical_demand: NonNegativeFloat = 0.0
    scheduled_demand_in_window: NonNegativeFloat = 0.0
    projected_demand: NonNegativeFloat = 0.0
    available: NonNegativeFloat = 0.0
    open_receipts_in_window: NonNegativeFloat = 0.0
    overdue_open_receipts_in_window: NonNegativeFloat = 0.0
    repair_receipts_in_window: NonNegativeFloat = 0.0
    expected_receipts_in_window: NonNegativeFloat = 0.0
    net: float = 0.0
    scheduled_demand_status: Literal["available", "partial", "unavailable"] = "unavailable"
    scheduled_demand_undated_lines: NonNegativeInt = 0
    scheduled_demand_undated_units: NonNegativeInt = 0
    open_receipts_status: Literal["available", "partial", "unavailable"] = "unavailable"
    open_receipts_undated_lines: NonNegativeInt = 0
    open_receipts_undated_units: NonNegativeInt = 0


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
    projected_historical_demand: NonNegativeFloat = 0.0
    scheduled_demand_in_window: NonNegativeFloat = 0.0
    open_receipts_in_window: NonNegativeFloat = 0.0
    overdue_open_receipts_in_window: NonNegativeFloat = 0.0
    repair_receipts_in_window: NonNegativeFloat = 0.0
    member_contributions: tuple[NetPositionMember, ...] = ()
    scheduled_demand_status: Literal["available", "partial", "unavailable"] = "unavailable"
    open_receipts_status: Literal["available", "partial", "unavailable"] = "unavailable"
    pooling_scope: Literal["single_key", "complete_group", "worklist_partial"] = "single_key"
    excluded_member_keys: tuple[str, ...] = ()
    scheduled_demand_undated_lines: NonNegativeInt = 0
    scheduled_demand_undated_units: NonNegativeInt = 0
    open_receipts_undated_lines: NonNegativeInt = 0
    open_receipts_undated_units: NonNegativeInt = 0


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
    # Procurement NEW remains the policy-driving legacy field.
    lead_time: LeadTimeDistribution | None = None
    # Descriptive REP evidence only ("RO cycle-time proxy" for creation-to-last-receipt);
    # it is not projected repair supply and grants no repair receipt credit.
    repair_cycle_time: LeadTimeDistribution | None = None
    location_graph: LocationGraph | None = None
    open_orders: OpenOrdersSnapshot | None = None
    requisition: RequisitionSnapshot | None = None
    interchange_group: InterchangeableGraph | None = None
    demand_history: DemandHistory
    causal: CausalUtilization | None = None
    scheduled_demand: tuple[ScheduledDemandItem, ...] = ()
    scheduled_demand_status: Literal["available", "partial", "unavailable"] = "unavailable"
    aog_signal: AogSignal = AogSignal()
    repair_tat: RepairTat = RepairTat()
    tenant_policy_config: TenantPolicyConfig = TenantPolicyConfig()

    @model_validator(mode="after")
    def _supply_cycle_lanes_are_independent(self) -> PartLocationContext:
        if self.lead_time is not None and self.lead_time.condition != "NEW":
            raise ValueError("lead_time must contain procurement NEW evidence")
        if (
            self.repair_cycle_time is not None
            and self.repair_cycle_time.condition != "REP"
        ):
            raise ValueError("repair_cycle_time must contain repair REP evidence")
        return self

    @property
    def description(self) -> str:
        """Non-empty part description sourced from part attributes (spec §5.2/§10)."""
        return self.part_attributes.description or self.pn
