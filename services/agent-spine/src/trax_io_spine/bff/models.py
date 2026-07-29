"""Planner-UI BFF wire models — mirror the engine/spine contracts for the future TS client."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from trax_io_reco.contracts.candidate import CandidateFrontier
from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType
from trax_io_reco.contracts.repair import RepairPipeline, RepairReturnProfile

from trax_io_spine.contracts import WritebackResult


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class RejectReason(StrEnum):
    WRONG_FOR_FLEET = "wrong_for_fleet"
    WRONG_ESSENTIALITY = "wrong_essentiality"
    BAD_LEAD_TIME = "bad_lead_time"
    PLANNER_OVERRIDE = "planner_override"
    OTHER = "other"


class QueueSortKey(StrEnum):
    """Server-side sort key for `GET .../recommendations` (task F2). `PRIORITY` is the
    default and reproduces the queue's pre-existing (and only) ordering byte-for-byte."""

    PRIORITY = "priority_score"
    COST_IMPACT = "estimated_cost_impact"
    CONFIDENCE = "confidence_score"
    CRITICALITY = "criticality_tier"


class QueueRow(_Base):
    recommendation_id: str
    pn: str
    location: str
    type: RecommendationType
    criticality_tier: int
    aog_risk_level: AogRiskLevel
    confidence_score: float
    recommended_quantity: float
    estimated_cost_impact: Decimal
    tier: AutonomyTier
    priority_score: float
    status: TaskStatus
    reason: str
    approvable: bool  # has a writable policy — approve writes rather than 409
    description: str
    current_stock: int
    shortage_quantity: float
    recommended_location: str | None
    horizon_days: int


class PagedQueue(_Base):
    items: tuple[QueueRow, ...]
    total: int
    limit: int
    offset: int


class _PolicyView(_Base):
    rop: int
    eoq: int
    safety_stock: int
    max_stock: int


class _EvidenceView(_Base):
    kind: str
    ref_id: str
    detail: str
    as_of: str | None = None


class RecommendationDetail(_Base):
    recommendation_id: str
    pn: str
    location: str
    type: RecommendationType
    criticality_tier: int
    aog_risk_level: AogRiskLevel
    confidence_score: float
    recommended_quantity: float
    estimated_cost_impact: Decimal
    tier: AutonomyTier
    status: TaskStatus
    reason: str
    provenance_id: str | None
    projected_demand: float
    current_policy: _PolicyView | None
    proposed_policy: _PolicyView | None
    supporting_evidence: tuple[_EvidenceView, ...]
    guardrail_flags: tuple[str, ...]
    guardrail_notes: tuple[str, ...]
    description: str
    current_stock: int
    shortage_quantity: float
    recommended_location: str | None
    horizon_days: int


class RejectRequest(_Base):
    reason: RejectReason
    detail: str = ""


class DeferRequest(_Base):
    until: datetime | None = None


class BulkApproveFilter(_Base):
    tiers: tuple[AutonomyTier, ...] | None = None
    max_delta_pct: float | None = None
    criticality_min: int | None = None
    types: tuple[RecommendationType, ...] | None = None


class ActionResult(_Base):
    recommendation_id: str
    status: TaskStatus
    writeback: WritebackResult | None = None
    message: str = ""


class KillSwitchState(_Base):
    engaged: bool


class StockBreakdown(_Base):
    on_hand: int
    serviceable: int
    in_repair: int
    allocated: int
    rental: int
    loan: int


class LeadTimeView(_Base):
    """Legacy NEW-only compatibility projection.

    New consumers should use ``PartContext.procurement_lead_time`` and
    ``PartContext.repair_cycle_time``.  Keeping this deliberately small model
    unchanged prevents old snapshots and clients from silently changing the
    meaning of ``lead_time``.
    """

    promised_days: float | None
    realized_mean_days: float | None
    n_observations: int


class SupplyCycleLaneView(_Base):
    """One independently sourced procurement or repair supply-cycle lane.

    ``unavailable`` is a real wire state, not a zero-valued observation.  The
    validator keeps missing/legacy evidence from accidentally carrying metrics
    or provenance and pins the repair proxy wording used by the browser.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    condition: Literal["NEW", "REP"]
    status: Literal["observed", "configured_fallback", "unavailable"]
    mean_days: float | None = Field(default=None, ge=0.0)
    p50_days: float | None = Field(default=None, ge=0.0)
    p90_days: float | None = Field(default=None, ge=0.0)
    p99_days: float | None = Field(default=None, ge=0.0)
    n_observations: int = Field(default=0, ge=0)
    source: Literal["order_plan_closed_orders", "pn_vendor_price"] | None = None
    grouping_level: Literal["part_vendor_condition", "part_condition"] | None = None
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    data_cutoff: str | None = None
    model_version: str | None = None
    classification_source: Literal[
        "explicit_order_type",
        "legacy_order_id_prefix",
        "configured_condition",
    ] | None = None
    proxy_definition: Literal[
        "order_creation_to_last_receipt",
        "configured_repair_promise",
    ] | None = None
    proxy_label: Literal[
        "RO cycle-time proxy",
        "Configured repair promise",
    ] | None = None
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def _coherent_lane(self) -> SupplyCycleLaneView:
        metrics = (self.mean_days, self.p50_days, self.p90_days, self.p99_days)
        provenance = (
            self.source,
            self.grouping_level,
            self.data_cutoff,
            self.model_version,
            self.classification_source,
            self.proxy_definition,
            self.proxy_label,
        )
        if self.status == "unavailable":
            if any(value is not None for value in metrics + provenance):
                raise ValueError(
                    "unavailable supply-cycle lanes cannot carry metrics or provenance"
                )
            if self.n_observations != 0 or self.confidence != "unknown":
                raise ValueError(
                    "unavailable supply-cycle lanes require zero observations and "
                    "unknown confidence"
                )
            if not self.unavailable_reason or not self.unavailable_reason.strip():
                raise ValueError(
                    "unavailable supply-cycle lanes require an unavailable_reason"
                )
            return self

        if any(value is None for value in metrics):
            raise ValueError("available supply-cycle lanes require all distribution metrics")
        if (
            self.source is None
            or self.grouping_level is None
            or self.confidence == "unknown"
            or self.data_cutoff is None
            or self.model_version is None
            or self.classification_source is None
        ):
            raise ValueError(
                "available supply-cycle lanes require complete evidence provenance"
            )
        if self.unavailable_reason is not None:
            raise ValueError("available supply-cycle lanes cannot be marked unavailable")

        if self.status == "observed":
            if (
                self.source != "order_plan_closed_orders"
                or self.n_observations == 0
                or self.classification_source
                not in {"explicit_order_type", "legacy_order_id_prefix"}
            ):
                raise ValueError(
                    "observed supply-cycle lanes require classified closed-order "
                    "observations"
                )
        elif (
            self.source != "pn_vendor_price"
            or self.n_observations != 0
            or self.classification_source != "configured_condition"
        ):
            raise ValueError(
                "configured fallback lanes require a configured price promise and "
                "zero observations"
            )

        if self.condition == "NEW":
            if self.proxy_definition is not None or self.proxy_label is not None:
                raise ValueError("NEW procurement evidence cannot carry a repair proxy")
        elif self.status == "observed":
            if (
                self.proxy_definition != "order_creation_to_last_receipt"
                or self.proxy_label != "RO cycle-time proxy"
            ):
                raise ValueError(
                    "observed REP evidence must be labeled RO cycle-time proxy"
                )
        elif (
            self.proxy_definition != "configured_repair_promise"
            or self.proxy_label != "Configured repair promise"
        ):
            raise ValueError(
                "configured REP evidence must be labeled Configured repair promise"
            )
        return self


def _legacy_supply_cycle_lane(condition: Literal["NEW", "REP"]) -> SupplyCycleLaneView:
    return SupplyCycleLaneView(
        condition=condition,
        status="unavailable",
        unavailable_reason=(
            "Supply-cycle lane was not stored in this legacy part-context payload."
        ),
    )


class OpenOrderView(_Base):
    order_id: str
    order_type: str
    vendor: str | None
    qty_open: int
    expected_rcv_date: str | None
    # Additive Phase 5 source identity/lifecycle fields. They remain nullable
    # because legacy feature snapshots predate repair-line reconciliation.
    order_line_id: str | None = None
    opened_at: str | None = None
    status: str | None = None
    serial_number: str | None = None
    location: str | None = None
    shop: str | None = None


class DemandPoint(_Base):
    period_start: str
    removals: int
    issues: int
    total: int


class DemandSummary(_Base):
    total_24mo: int
    points: tuple[DemandPoint, ...]


class PartAttributesView(_Base):
    description: str
    ata_chapter: str | None
    part_class: str | None
    shelf_life_days: int | None
    hazardous_material: bool
    tool_control_item: bool
    criticality_tier: int | None


class PlanningConstraintView(_Base):
    """One planner-facing constraint and whether it determined the served policy."""

    name: str
    value: str | None
    binding: bool
    source: str
    scope: Literal["policy", "action"] = "policy"


class PlanningMemberTraceView(_Base):
    """One part/location's persisted contribution to a served calculation."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    pn: str
    location: str
    projection_kind: str
    projected_historical_demand: float = Field(ge=0.0)
    scheduled_demand_status: Literal["available", "partial", "unavailable"] = (
        "unavailable"
    )
    scheduled_demand_undated_lines: int = Field(default=0, ge=0)
    scheduled_demand_undated_units: int = Field(default=0, ge=0)
    scheduled_demand_due: float = Field(ge=0.0)
    projected_demand: float = Field(ge=0.0)
    dispatchable_available: float = Field(ge=0.0)
    open_receipts_status: Literal["available", "partial", "unavailable"] = (
        "unavailable"
    )
    open_receipts_undated_lines: int = Field(default=0, ge=0)
    open_receipts_undated_units: int = Field(default=0, ge=0)
    open_receipts_due: float = Field(ge=0.0)
    overdue_open_receipts_due: float = Field(ge=0.0)
    repair_receipts_due: float = Field(ge=0.0)
    expected_receipts_due: float = Field(ge=0.0)
    net_position: float

    @model_validator(mode="after")
    def _reconciles(self) -> PlanningMemberTraceView:
        tolerance = 1e-6
        if self.overdue_open_receipts_due > self.open_receipts_due + tolerance:
            raise ValueError(
                "member overdue_open_receipts_due cannot exceed open_receipts_due"
            )
        if (
            abs(
                self.projected_historical_demand
                + self.scheduled_demand_due
                - self.projected_demand
            )
            > tolerance
        ):
            raise ValueError(
                "member projected_demand must equal historical plus scheduled demand"
            )
        if (
            abs(
                self.open_receipts_due
                + self.repair_receipts_due
                - self.expected_receipts_due
            )
            > tolerance
        ):
            raise ValueError(
                "member expected_receipts_due must equal open plus repair receipts"
            )
        if (
            abs(
                self.dispatchable_available
                + self.expected_receipts_due
                - self.projected_demand
                - self.net_position
            )
            > tolerance
        ):
            raise ValueError(
                "member net_position must reconcile availability, receipts, and demand"
            )
        return self


class PlanningTraceView(_Base):
    """Auditable observation and exact served-calculation evidence.

    Every field has an unavailable-safe default so JSON persisted before this
    additive contract existed remains valid. Raw observation statistics remain
    separate from the recommendation engine's served projection. When the exact
    engine carrier is absent, ``calculation_source`` prevents reconstructed or
    unavailable values from being presented as served arithmetic.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    calculation_source: Literal[
        "served_calculation", "legacy_reconstructed", "unavailable"
    ] = "unavailable"
    observation_start: str | None = None
    observation_end: str | None = None
    exposure_days: int = Field(default=0, ge=0)
    bucket: Literal["day", "week", "month"] | None = None
    observed_periods: int = Field(default=0, ge=0)
    zero_filled_periods: int = Field(default=0, ge=0)
    demand_event_count: int | None = Field(default=None, ge=0)
    event_count_source: Literal["observed", "bucket_fallback", "unavailable"] = "unavailable"
    demanded_units: int = Field(default=0, ge=0)
    historical_per_day: float = Field(default=0.0, ge=0.0)
    as_of: str | None = None
    horizon_days: int = Field(default=0, ge=0)
    horizon_end: str | None = None
    projection_kind: str | None = None
    served_historical_per_day: float | None = Field(default=None, ge=0.0)
    projected_historical_demand: float = Field(default=0.0, ge=0.0)
    scheduled_demand_status: Literal["available", "partial", "unavailable"] = (
        "unavailable"
    )
    scheduled_demand_undated_lines: int = Field(default=0, ge=0)
    scheduled_demand_undated_units: int = Field(default=0, ge=0)
    scheduled_demand_due: float = Field(default=0.0, ge=0.0)
    projected_demand: float | None = Field(default=None, ge=0.0)
    dispatchable_available: float | None = Field(default=None, ge=0.0)
    open_receipts_status: Literal["available", "partial", "unavailable"] = (
        "unavailable"
    )
    open_receipts_undated_lines: int = Field(default=0, ge=0)
    open_receipts_undated_units: int = Field(default=0, ge=0)
    open_receipts_due: float = Field(default=0.0, ge=0.0)
    overdue_open_receipts_due: float = Field(default=0.0, ge=0.0)
    repair_receipts_due: float | None = Field(default=None, ge=0.0)
    expected_receipts_due: float | None = Field(default=None, ge=0.0)
    net_position: float | None = None
    shortage_before_action: float | None = Field(default=None, ge=0.0)
    pooled_group_id: str | None = None
    pooling_scope: Literal[
        "single_key", "complete_group", "worklist_partial"
    ] = "single_key"
    excluded_member_keys: tuple[str, ...] = ()
    members: tuple[PlanningMemberTraceView, ...] = ()
    constraints: tuple[PlanningConstraintView, ...] = ()
    warnings: tuple[str, ...] = (
        "Planning inputs were not persisted with this legacy part context; "
        "zero-valued trace fields are unavailable, not observed zeros.",
    )

    @model_validator(mode="after")
    def _consistent_bounds(self) -> PlanningTraceView:
        def parsed(label: str, value: str | None) -> date | None:
            if value is None:
                return None
            try:
                return date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{label} must be an ISO date") from exc

        observation_start = parsed("observation_start", self.observation_start)
        observation_end = parsed("observation_end", self.observation_end)
        as_of = parsed("as_of", self.as_of)
        horizon_end = parsed("horizon_end", self.horizon_end)

        if (observation_start is None) != (observation_end is None):
            raise ValueError(
                "observation_start and observation_end must both be present or absent"
            )
        if (as_of is None) != (horizon_end is None):
            raise ValueError("as_of and horizon_end must both be present or absent")
        if observation_start is not None and observation_end is not None:
            if observation_end < observation_start:
                raise ValueError("observation_end must not precede observation_start")
            inclusive_days = (observation_end - observation_start).days + 1
            if self.exposure_days != inclusive_days:
                raise ValueError(
                    "exposure_days must equal the inclusive observation interval"
                )
        if as_of is not None and horizon_end is not None and horizon_end < as_of:
            raise ValueError("horizon_end must not precede as_of")
        if self.overdue_open_receipts_due > self.open_receipts_due:
            raise ValueError(
                "overdue_open_receipts_due cannot exceed open_receipts_due"
            )

        if self.calculation_source != "served_calculation":
            return self

        required = {
            "as_of": self.as_of,
            "horizon_end": self.horizon_end,
            "projection_kind": self.projection_kind,
            "served_historical_per_day": self.served_historical_per_day,
            "projected_demand": self.projected_demand,
            "dispatchable_available": self.dispatchable_available,
            "repair_receipts_due": self.repair_receipts_due,
            "expected_receipts_due": self.expected_receipts_due,
            "net_position": self.net_position,
            "shortage_before_action": self.shortage_before_action,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(
                "served_calculation requires exact fields: " + ", ".join(missing)
            )
        if not self.members:
            raise ValueError("served_calculation requires at least one member")

        assert as_of is not None
        assert horizon_end is not None
        if horizon_end != as_of + timedelta(days=self.horizon_days):
            raise ValueError("horizon_end must equal as_of plus horizon_days")

        # The None checks above narrow these values for human readers; float()
        # keeps the reconciliation block concise for static type checkers.
        projected_demand = float(self.projected_demand)
        dispatchable_available = float(self.dispatchable_available)
        repair_receipts_due = float(self.repair_receipts_due)
        expected_receipts_due = float(self.expected_receipts_due)
        net_position = float(self.net_position)
        shortage_before_action = float(self.shortage_before_action)
        tolerance = 1e-6
        if (
            abs(
                float(self.served_historical_per_day) * self.horizon_days
                - self.projected_historical_demand
            )
            > tolerance
        ):
            raise ValueError(
                "projected_historical_demand must equal served rate times horizon_days"
            )
        if (
            abs(
                self.projected_historical_demand
                + self.scheduled_demand_due
                - projected_demand
            )
            > tolerance
        ):
            raise ValueError(
                "projected_demand must equal historical plus scheduled demand"
            )
        if (
            abs(
                self.open_receipts_due
                + repair_receipts_due
                - expected_receipts_due
            )
            > tolerance
        ):
            raise ValueError(
                "expected_receipts_due must equal open plus repair receipts"
            )
        if (
            abs(
                dispatchable_available
                + expected_receipts_due
                - projected_demand
                - net_position
            )
            > tolerance
        ):
            raise ValueError(
                "net_position must reconcile availability, receipts, and demand"
            )
        if abs(max(0.0, -net_position) - shortage_before_action) > tolerance:
            raise ValueError(
                "shortage_before_action must equal the negative net-position floor"
            )

        sums = {
            "projected_historical_demand": sum(
                member.projected_historical_demand for member in self.members
            ),
            "scheduled_demand_due": sum(
                member.scheduled_demand_due for member in self.members
            ),
            "projected_demand": sum(member.projected_demand for member in self.members),
            "dispatchable_available": sum(
                member.dispatchable_available for member in self.members
            ),
            "open_receipts_due": sum(
                member.open_receipts_due for member in self.members
            ),
            "overdue_open_receipts_due": sum(
                member.overdue_open_receipts_due for member in self.members
            ),
            "repair_receipts_due": sum(
                member.repair_receipts_due for member in self.members
            ),
            "expected_receipts_due": sum(
                member.expected_receipts_due for member in self.members
            ),
            "net_position": sum(member.net_position for member in self.members),
            "scheduled_demand_undated_lines": sum(
                member.scheduled_demand_undated_lines for member in self.members
            ),
            "scheduled_demand_undated_units": sum(
                member.scheduled_demand_undated_units for member in self.members
            ),
            "open_receipts_undated_lines": sum(
                member.open_receipts_undated_lines for member in self.members
            ),
            "open_receipts_undated_units": sum(
                member.open_receipts_undated_units for member in self.members
            ),
        }
        for field_name, member_sum in sums.items():
            if abs(member_sum - float(getattr(self, field_name))) > tolerance:
                raise ValueError(
                    f"{field_name} must equal the sum of member evidence"
                )
        if len(self.members) > 1 and self.pooled_group_id is None:
            raise ValueError(
                "pooled_group_id is required when multiple members are served"
            )
        member_keys = [(member.pn, member.location) for member in self.members]
        if len(member_keys) != len(set(member_keys)):
            raise ValueError("calculation member keys must be unique")
        if len(self.excluded_member_keys) != len(set(self.excluded_member_keys)):
            raise ValueError("excluded_member_keys must be unique")
        included_key_labels = {f"{pn}@{location}" for pn, location in member_keys}
        if included_key_labels.intersection(self.excluded_member_keys):
            raise ValueError("included and excluded member keys must be disjoint")
        for field_name in ("scheduled_demand_status", "open_receipts_status"):
            states = {getattr(member, field_name) for member in self.members}
            expected = (
                "available"
                if states == {"available"}
                else ("unavailable" if states == {"unavailable"} else "partial")
            )
            if getattr(self, field_name) != expected:
                raise ValueError(
                    f"{field_name} must summarize the member availability states"
                )
        if self.pooling_scope == "worklist_partial" and not self.excluded_member_keys:
            raise ValueError(
                "worklist_partial pooling requires excluded_member_keys"
            )
        if self.pooling_scope != "worklist_partial" and self.excluded_member_keys:
            raise ValueError(
                "excluded_member_keys require worklist_partial pooling"
            )
        if self.pooling_scope == "complete_group" and len(self.members) < 2:
            raise ValueError(
                "complete_group pooling requires multiple member contributions"
            )
        if self.pooling_scope == "single_key":
            if len(self.members) != 1 or self.pooled_group_id is not None:
                raise ValueError(
                    "single_key pooling requires exactly one member and no group id"
                )
        elif self.pooled_group_id is None:
            raise ValueError(
                "pooled calculation evidence requires pooled_group_id"
            )
        aggregate_states = (
            (
                "scheduled demand",
                self.scheduled_demand_status,
                self.scheduled_demand_due,
                self.scheduled_demand_undated_lines,
                self.scheduled_demand_undated_units,
            ),
            (
                "open receipts",
                self.open_receipts_status,
                self.open_receipts_due,
                self.open_receipts_undated_lines,
                self.open_receipts_undated_units,
            ),
        )
        for label, status, due, undated_lines, undated_units in aggregate_states:
            if status == "available" and (undated_lines or undated_units):
                raise ValueError(
                    f"available aggregate {label} cannot contain undated exclusions"
                )
            if status == "unavailable" and (
                due > tolerance or undated_lines or undated_units
            ):
                raise ValueError(
                    f"unavailable aggregate {label} cannot contain observed quantities"
                )
        return self


class PartContext(_Base):
    pn: str
    location: str
    attributes: PartAttributesView
    stock: StockBreakdown | None
    current_policy: _PolicyView | None
    proposed_policy: _PolicyView | None
    lead_time: LeadTimeView | None
    procurement_lead_time: SupplyCycleLaneView = Field(
        default_factory=lambda: _legacy_supply_cycle_lane("NEW")
    )
    repair_cycle_time: SupplyCycleLaneView = Field(
        default_factory=lambda: _legacy_supply_cycle_lane("REP")
    )
    open_orders: tuple[OpenOrderView, ...]
    total_open_qty: int
    open_orders_status: Literal["available", "partial", "unavailable"] = "unavailable"
    # Additive Phase 5 contract. Legacy persisted part-context JSON omits this
    # field; absence is an unavailable state, never a zero-valued pipeline.
    repair_pipeline: RepairPipeline | None = None
    # Additive Phase 6 age-conditioned projection. Legacy payloads and
    # non-repairable parts omit it; absence is unknown/not-applicable, never zero.
    repair_return_profile: RepairReturnProfile | None = None
    demand: DemandSummary | None
    unit_cost: float | None
    planning_trace: PlanningTraceView = PlanningTraceView()
    # Additive Phase-2 preview. Legacy snapshots/PG rows omit this field and
    # therefore remain valid with an explicit "not computed" value.
    candidate_frontier: CandidateFrontier | None = None


class Breakdown(_Base):
    key: str
    count: int
    on_hand: int
    shortage: float


class PartShortfall(_Base):
    pn: str
    location: str
    shortage: float
    on_hand: int
    projected_demand: float


class DashboardSummary(_Base):
    parts: int
    total_on_hand: int
    total_on_hand_value: float
    total_shortage: float
    total_projected_demand: float
    aog_exposure: int
    open_recommendations: int
    net_cost_impact: float
    by_criticality: tuple[Breakdown, ...]
    by_ata: tuple[Breakdown, ...]
    by_part_class: tuple[Breakdown, ...]
    by_tier: tuple[Breakdown, ...]
    top_shortages: tuple[PartShortfall, ...]


# --------------------------------------------------------------------------- #
# Slice S5 — Forecast & Service Levels (PRD §6.6)
# --------------------------------------------------------------------------- #
class ServiceLevelBand(_Base):
    """One criticality tier's differentiated SL policy (spec: `TenantPolicyConfig.
    service_level_by_tier`) crossed with the real count of (PN, Location) keys
    classified into that tier by the feature store's `Criticality.canonical_tier`.

    `actual_coverage` is the honest on-hand-vs-shortage proxy already used by the
    Overview's SlInvestmentPanel — not a true fill-rate backtest (no such series is
    computed at serve time).
    """

    criticality_tier: int
    target_service_level: float
    sku_count: int
    actual_coverage: float | None


class ServiceLevelPolicy(_Base):
    bands: tuple[ServiceLevelBand, ...]


class MethodCoverageRow(_Base):
    """Count of (PN, Location) keys whose demand regime — and therefore forecast
    method — is `regime`, per the deterministic classifier (spec §6.1:
    `trax_io_reco.regime.classifier.classify`, thresholded on 24-month event counts).
    """

    regime: str
    method: str
    sku_count: int
    pct: float


class MethodCoverage(_Base):
    total_skus: int
    rows: tuple[MethodCoverageRow, ...]


class AccuracyPoint(_Base):
    """A single monthly period's actual-vs-projected demand, network-aggregated.

    This is NOT a backtested forecast accuracy metric — no held-out backtest runs
    at serve time. It's an honest proxy: recent actual demand (from real,
    monthly-bucketed `DEMAND_HISTORY` observations) vs. the engine's current
    constant-rate (mean-per-day) demand projection, scaled to this period's own
    length in days and aggregated across the portfolio. `projected` is a re-scaled
    constant rate, not a genuine per-period reforecast.
    """

    period_start: str
    actual: float
    projected: float


class ForecastAccuracy(_Base):
    status: str  # "proxy" (honest gap — see docstring) — never "connected" in v1
    note: str
    points: tuple[AccuracyPoint, ...]


class ForecastSummary(_Base):
    service_levels: ServiceLevelPolicy
    method_coverage: MethodCoverage
    accuracy: ForecastAccuracy


# --------------------------------------------------------------------------- #
# Slice S6 — What-If Scenarios (PRD §6.5)
# --------------------------------------------------------------------------- #
class ScenarioScopeKind(StrEnum):
    ALL = "all"
    CRITICALITY_TIER = "criticality_tier"
    ATA_CHAPTER = "ata_chapter"


class ScenarioParamsWire(_Base):
    """The What-If sliders. All optional fields fall back to the real
    `TenantPolicyConfig` / current-state defaults when unset (see `bff/scenario.py`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    service_level_target: float | None = Field(
        default=None,
        gt=0.0,
        lt=1.0,
        allow_inf_nan=False,
    )
    service_level_by_tier: dict[int, float] = Field(default_factory=dict)
    budget_cap: float | None = Field(
        default=None,
        ge=0.0,
        allow_inf_nan=False,
        description=(
            "Informational only: flags whether the proposed investment exceeds this "
            "cap via `ScenarioSolveResult.budget_cap_binds`. Does NOT filter, scale, "
            "or otherwise constrain the solve — the solver always solves the full "
            "in-scope key set at the requested service level regardless of this cap."
        ),
    )
    lead_time_delta_pct: float = Field(
        default=0.0,
        gt=-1.0,
        le=10.0,
        allow_inf_nan=False,
    )
    procurement_lead_time_delta_pct: float | None = Field(
        default=None,
        gt=-1.0,
        le=10.0,
        allow_inf_nan=False,
    )
    repair_tat_delta_pct: float = Field(
        default=0.0,
        gt=-1.0,
        le=10.0,
        allow_inf_nan=False,
    )
    scope: ScenarioScopeKind = ScenarioScopeKind.ALL
    scope_value: str | None = None

    @field_validator("service_level_by_tier")
    @classmethod
    def _valid_service_level_overrides(
        cls,
        values: dict[int, float],
    ) -> dict[int, float]:
        invalid_tiers = sorted(tier for tier in values if tier not in range(1, 6))
        if invalid_tiers:
            raise ValueError("service_level_by_tier keys must be tiers 1 through 5")
        if any(
            not math.isfinite(value) or not 0.0 < value < 1.0
            for value in values.values()
        ):
            raise ValueError(
                "service_level_by_tier values must be finite and between zero and one"
            )
        return values

    @model_validator(mode="after")
    def _materialize_procurement_compatibility(self) -> ScenarioParamsWire:
        """Map the legacy lead delta to NEW procurement only.

        An explicitly supplied modern field wins, including an explicit zero.
        The materialized value is serialized so clients never have to guess
        which assumption the legacy field affected.
        """

        if (
            self.procurement_lead_time_delta_pct is not None
            and "lead_time_delta_pct" in self.model_fields_set
            and self.lead_time_delta_pct != 0
            and self.procurement_lead_time_delta_pct
            != self.lead_time_delta_pct
        ):
            raise ValueError(
                "lead_time_delta_pct and procurement_lead_time_delta_pct "
                "cannot specify conflicting procurement assumptions"
            )
        effective = (
            self.procurement_lead_time_delta_pct
            if self.procurement_lead_time_delta_pct is not None
            else self.lead_time_delta_pct
        )
        # Materialize both names to the same effective NEW-procurement value.
        # Equivalent legacy and modern payloads therefore serialize identically,
        # while repair TAT remains a wholly independent assumption.
        object.__setattr__(self, "lead_time_delta_pct", effective)
        object.__setattr__(self, "procurement_lead_time_delta_pct", effective)
        return self


class ScenarioOutcomeWire(_Base):
    """`projected_coverage` is the target cycle-service-level a fully-funded proposed
    policy would achieve (monotonic in the SL slider). `on_hand_gap_ratio` is the
    fraction of scoped keys whose current real on-hand already meets the proposed
    reorder point — real, useful, but NOT expected to be monotonic in SL (see
    `bff/scenario.py` module docstring).

    Simplification disclosure: every number here comes from one uniform (R,Q)
    normal-approximation solve (spec §6.2/§6.4 math) applied to ALL keys regardless of
    demand regime — an interactive approximation for a real-time What-If slider, not a
    re-run of the full recommendation engine. The engine's real per-regime policy
    dispatch (base-stock / (s,S) / (R,Q)) may differ materially from this uniform
    approximation, especially for ultra-rare and intermittent-demand keys.
    """

    service_level: float
    projected_investment: float
    projected_coverage: float
    on_hand_gap_ratio: float
    scored_keys: int


class FrontierPointWire(_Base):
    service_level: float
    projected_investment: float
    projected_coverage: float


class ScenarioRepairReturnOutcomeWire(_Base):
    horizon_days: int
    eligible_quantity: int
    expected_units: float
    modeled_keys: int
    unavailable_keys: int
    unscoped_keys: int = Field(default=0, ge=0)
    serviceable_yield_assumption: float


class ScenarioAssumptionImpact(_Base):
    label: str = Field(min_length=1)
    affected_key_count: int = Field(ge=0)


class ScenarioSolveResult(_Base):
    """Response of `POST /scenarios/solve` — live, not persisted (API-SPEC.md).

    `skipped_keys` / `total_keys` is the honest data-quality disclosure: keys in the
    tenant's full real key universe (`total_keys`) that are missing demand history,
    criticality, vendor economics, or stock position cannot be scored at all
    (`skipped_keys`), independent of the scenario's own `scope` filter — how many of
    the *in-scope* keys were actually scored is `ScenarioOutcomeWire.scored_keys`.

    Simplification disclosure: `current`/`proposed`/`frontier` are all solved via one
    uniform (R,Q) normal-approximation model applied identically across every demand
    regime (see `ScenarioOutcomeWire` and `bff/scenario.py` module docstring) — an
    interactive approximation, not a re-run of the engine's regime-conditional policy
    dispatch. Treat this as directional for scenario comparison, not as a substitute
    for the real per-key recommendation the engine would produce.
    """

    params: ScenarioParamsWire
    current: ScenarioOutcomeWire
    proposed: ScenarioOutcomeWire
    delta_investment: float
    delta_coverage: float
    frontier: tuple[FrontierPointWire, ...]
    skipped_keys: int
    total_keys: int
    budget_cap_binds: bool
    # Old persisted scenarios deserialize as v1 with unavailable additive
    # metadata. Newly solved scenarios explicitly set v2 and every field below.
    contract_version: Literal["scenario-solve.v1", "scenario-solve.v2"] = (
        "scenario-solve.v1"
    )
    repair_current: ScenarioRepairReturnOutcomeWire | None = None
    repair_proposed: ScenarioRepairReturnOutcomeWire | None = None
    assumption_impacts: tuple[ScenarioAssumptionImpact, ...] = ()
    affected_key_count: int | None = Field(default=None, ge=0)
    fingerprint: str | None = Field(
        default=None,
        pattern=r"^scenario_v2_[0-9a-f]{64}$",
    )
    source_as_of: str | None = None
    source_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    source_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    warning_codes: tuple[str, ...] = ()


class SaveScenarioRequest(_Base):
    name: str
    params: ScenarioParamsWire
    result: ScenarioSolveResult


class ScenarioStatus(StrEnum):
    DRAFT = "draft"
    COMMITTED = "committed"


class Scenario(_Base):
    """A saved scenario (API-SPEC.md `Scenario`). `status` starts `DRAFT`; `commit`
    (`POST /scenarios/{id}/commit`) promotes it to `COMMITTED` and appends an
    in-memory `AuditEvent` — v1 does NOT write policies back to eMRO (out of scope;
    see `bff/scenario.py` module docstring and ADR follow-up)."""

    id: str
    name: str
    params: ScenarioParamsWire
    result: ScenarioSolveResult
    status: ScenarioStatus
    created_at: datetime
    committed_at: datetime | None = None


class ScenarioAuditEvent(_Base):
    """In-memory commit acknowledgement — NOT a real eMRO writeback (spec: Writeback
    is the ONLY agent with eMRO write permission; scenario commit is a planning-tool
    audit marker, not a policy write)."""

    scenario_id: str
    scenario_name: str
    action: Literal["commit"]
    at: datetime
    note: str = (
        "Scenario committed as the tenant's target plan. No eMRO writeback occurred — "
        "promoting a scenario's levers into live (ROP, EOQ, Safety Stock, Max) policy "
        "writes is out of scope for v1 (see docs/adr for the writeback seam)."
    )


# --------------------------------------------------------------------------- #
# Slice S7 — Data & Connections / feed health (PRD §6.7)
# --------------------------------------------------------------------------- #
class FeedId(StrEnum):
    """The 13 canonical feeds (DATA-MODEL.md §2 / PRD §7 / BUILD-PLAN.md), in the
    exact order the spec lists them."""

    REQUISITIONS = "REQUISITIONS"
    PURCHASE_ORDERS = "PURCHASE_ORDERS"
    QUOTATIONS = "QUOTATIONS"
    REPAIR_ORDERS = "REPAIR_ORDERS"
    INVENTORY = "INVENTORY"
    SERIAL_TRACKING = "SERIAL_TRACKING"
    RELIABILITY = "RELIABILITY"
    FLEET_UTILIZATION = "FLEET_UTILIZATION"
    MAINTENANCE_SCHEDULE = "MAINTENANCE_SCHEDULE"
    VENDOR_MASTER = "VENDOR_MASTER"
    INTERCHANGEABILITY = "INTERCHANGEABILITY"
    CONTRACTS = "CONTRACTS"
    SHELF_LIFE = "SHELF_LIFE"


class FeedConnectionStatus(StrEnum):
    """Truthful connection status, derived from the real 21-domain extract registry
    (`tools/nightly-extract/src/trax_io_extract/domains.py`) and what
    `services/recommendation-engine/.../extract_loader.py` actually consumes —
    NOT the spec's `FeedHealth.status` (`HEALTHY`/`PARTIAL`/`LOW_COVERAGE`/`STALE`),
    which describes data quality of an already-wired feed. v1 has a more basic gap:
    several feeds have no eMRO domain wired at all yet."""

    CONNECTED = "connected"
    """Extracted AND consumed into a feature-store schema the engine reads."""
    PARTIAL = "partial"
    """Either extracted-but-not-consumed, or consumed but structurally thin
    (e.g. a duration field standing in for a full ledger)."""
    NOT_CONNECTED = "not_connected"
    """No backing eMRO extract domain exists in v1 at all."""


class FeedHealthRow(_Base):
    """One spec feed's honest connection status (`GET /v1/tenants/{t}/feeds`).

    `domains` lists the real extract domain names (`domains.py` `Domain.name`) that
    back this feed, empty when `status` is `NOT_CONNECTED`. `rows`/`last_sync` come
    from the loaded extract's `manifest.json` artifacts when available — the
    committed sample manifest carries per-domain `status` but no `row_count`, and a
    manifest can be absent/trimmed entirely, so both are `None` in that case rather
    than fabricated. `notes` are the honest caveats: what's collapsed, what's
    extracted-but-unwired, what has no eMRO source at all in v1.
    """

    feed_id: FeedId
    name: str
    status: FeedConnectionStatus
    domains: tuple[str, ...]
    rows: int | None
    last_sync: str | None
    notes: str


class FeedHealthStrip(_Base):
    """Aggregate counts for the Data & Connections health strip (PRD §6.7)."""

    connected: int
    partial: int
    not_connected: int
    extract_date: str | None


class FeedsSummary(_Base):
    """Response of `GET /v1/tenants/{t}/feeds` — the Data & Connections view's
    ground truth. `health` is the aggregate strip; `feeds` is the full 13-row
    table, always in the canonical `FeedId` order."""

    health: FeedHealthStrip
    feeds: tuple[FeedHealthRow, ...]
