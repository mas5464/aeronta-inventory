"""Agent Spine contracts: guardrail outcomes, writeback I/O, orchestration result.

Re-exports #11's mirrors (AutonomyTier, PolicyRecommendation) and #11's SkippedKey so the
spine consumes the engine's types verbatim rather than redefining them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt
from trax_io_reco.contracts.enums import AutonomyTier
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.contracts.recommendation import SkippedKey

__all__ = [
    "ApprovalTask",
    "AutonomyTier",
    "GuardrailOutcome",
    "GuardrailStatus",
    "HistoryEntry",
    "OrchestrationResult",
    "PolicyRecommendation",
    "RollbackRequest",
    "RollbackResult",
    "RollbackStatus",
    "SkippedKey",
    "WritebackRequest",
    "WritebackResult",
    "WritebackStatus",
]


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GuardrailStatus(StrEnum):
    APPROVED_FOR_WRITE = "approved_for_write"
    QUEUED_FOR_APPROVAL = "queued_for_approval"
    REJECTED_HARD_GUARDRAIL = "rejected_hard_guardrail"


class WritebackStatus(StrEnum):
    WRITTEN = "written"
    DEFERRED_OPEN_ORDER = "deferred_open_order"
    FAILED = "failed"
    SHADOWED = "shadowed"


class ApprovalTask(_Base):
    task_id: str
    tenant_id: str
    pn: str
    location: str
    tier: AutonomyTier
    priority_score: float = Field(ge=0.0)
    reason: str = ""


class GuardrailOutcome(_Base):
    recommendation_id: str
    status: GuardrailStatus
    tier: AutonomyTier
    delta_pct: float = Field(ge=0.0)
    reasons: tuple[str, ...] = ()
    approval_task: ApprovalTask | None = None


class WritebackRequest(_Base):
    tenant_id: str
    pn: str
    location: str
    rop: NonNegativeInt
    eoq: NonNegativeInt
    safety_stock: NonNegativeInt
    max_stock: NonNegativeInt
    provenance_id: str
    idempotency_key: str = Field(min_length=1)
    tier: AutonomyTier | None = None
    shadow: bool = False


class WritebackResult(_Base):
    tenant_id: str
    pn: str
    location: str
    status: WritebackStatus
    old_values: dict[str, int] | None = None
    new_values: dict[str, int] | None = None
    written_at: datetime | None = None
    error_message: str | None = None


class OrchestrationResult(_Base):
    tenant_id: str
    generated_at: datetime
    written: tuple[WritebackResult, ...] = ()
    deferred: tuple[WritebackResult, ...] = ()
    failed: tuple[WritebackResult, ...] = ()
    shadowed: tuple[WritebackResult, ...] = ()
    queued: tuple[ApprovalTask, ...] = ()
    rejected: tuple[GuardrailOutcome, ...] = ()
    skipped: tuple[SkippedKey, ...] = ()
    summary: dict[str, int] = Field(default_factory=dict)


class RollbackStatus(StrEnum):
    ROLLED_BACK = "rolled_back"
    OUTSIDE_WINDOW = "outside_window"
    NOTHING_TO_REVERT = "nothing_to_revert"


class HistoryEntry(_Base):
    tenant_id: str
    pn: str
    location: str
    version: int  # monotonic per (tenant, pn, location), starting at 1
    status: WritebackStatus
    old_values: dict[str, int] | None
    new_values: dict[str, int]
    provenance_id: str
    tier: AutonomyTier | None
    agent_version: str
    changed_by_principal: str
    idempotency_key: str | None
    parent_version: int | None
    changed_at: datetime


class RollbackRequest(_Base):
    tenant_id: str
    pn: str
    location: str
    reason: str
    principal: str = "planner"
    requested_at: datetime


class RollbackResult(_Base):
    tenant_id: str
    pn: str
    location: str
    status: RollbackStatus
    from_values: dict[str, int] | None = None
    to_values: dict[str, int] | None = None
    reverted_from_version: int | None = None
    new_version: int | None = None
    rolled_back_at: datetime | None = None
    error_message: str | None = None
