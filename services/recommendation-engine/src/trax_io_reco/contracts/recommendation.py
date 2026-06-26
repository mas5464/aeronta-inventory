"""The recommendation output contracts — the authoritative API/UI response shape
(spec §5.2). `RecommendationBatch` serializes to the exact JSON the UI/API returns.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from trax_io_reco.contracts.context import CurrentPolicy
from trax_io_reco.contracts.enums import (
    AogRiskLevel,
    AutonomyTier,
    EvidenceKind,
    RecommendationType,
)
from trax_io_reco.contracts.policy import PolicyRecommendation


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Evidence(_Base):
    kind: EvidenceKind
    ref_id: str
    detail: str
    as_of: date | None = None


class Recommendation(_Base):
    recommendation_id: str
    tenant_id: str
    type: RecommendationType
    part_number: str
    description: str
    current_location: str
    recommended_location: str | None = None
    current_stock: int
    projected_demand: float
    shortage_quantity: float = Field(ge=0.0)
    recommended_quantity: float
    estimated_cost_impact: Decimal
    aog_risk_level: AogRiskLevel
    criticality_tier: int = 5  # 1 (most critical) .. 5; drives ranking weight
    reason: str
    supporting_evidence: tuple[Evidence, ...]
    confidence_score: float = Field(ge=0.0, le=1.0)
    horizon_days: int
    suggested_autonomy_tier: AutonomyTier
    guardrail_flags: tuple[str, ...] = ()
    generated_at: datetime
    input_snapshot_hash: str
    policy: PolicyRecommendation | None = None
    current_policy: CurrentPolicy | None = None


class SkippedKey(_Base):
    pn: str
    location: str
    reason: str


class BatchSummary(_Base):
    total: int
    by_type: dict[str, int] = Field(default_factory=dict)
    by_aog: dict[int, int] = Field(default_factory=dict)


class RecommendationBatch(_Base):
    tenant_id: str
    generated_at: datetime
    reporting_horizon_days: int = 30
    recommendations: tuple[Recommendation, ...] = ()
    skipped: tuple[SkippedKey, ...] = ()
    summary: BatchSummary
