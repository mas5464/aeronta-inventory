"""Planner-UI BFF wire models — mirror the engine/spine contracts for the future TS client."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType

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
    promised_days: float | None
    realized_mean_days: float | None
    n_observations: int


class OpenOrderView(_Base):
    order_id: str
    order_type: str
    vendor: str | None
    qty_open: int
    expected_rcv_date: str | None


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


class PartContext(_Base):
    pn: str
    location: str
    attributes: PartAttributesView
    stock: StockBreakdown | None
    current_policy: _PolicyView | None
    proposed_policy: _PolicyView | None
    lead_time: LeadTimeView | None
    open_orders: tuple[OpenOrderView, ...]
    total_open_qty: int
    demand: DemandSummary | None
    unit_cost: float | None
