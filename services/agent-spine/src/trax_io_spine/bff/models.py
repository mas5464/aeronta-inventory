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
