"""Recommender interface + shared construction helpers (spec §7).

A recommender receives a RecommenderInput bundle and returns 0..n Recommendation. AOG
risk level, suggested tier, and confidence are filled with provisional placeholders here
and overwritten by the downstream AOG / confidence / ranking stages.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from ulid import ULID

from trax_io_reco.contracts.context import (
    CurrentPolicy,
    DemandProjection,
    NetPosition,
    PartLocationContext,
)
from trax_io_reco.contracts.enums import (
    AogRiskLevel,
    AutonomyTier,
    RecommendationType,
)
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.contracts.recommendation import Evidence, Recommendation
from trax_io_reco.policy.lead_time import protection_period_days


@dataclass(frozen=True)
class DonorOption:
    location: str
    serviceable_excess: int
    lead_days: float
    cost: float


# (pn, group_id, main_warehouse) -> donor options at OTHER locations
DonorLookup = Callable[[str, str | None, str | None], list[DonorOption]]
# window_days -> NetPosition
NetPositionFn = Callable[[int], NetPosition]


@dataclass(frozen=True)
class RecommenderInput:
    context: PartLocationContext
    projection: DemandProjection
    policy: PolicyRecommendation
    now: datetime
    as_of: date
    input_snapshot_hash: str
    reporting_horizon_days: int
    net_position: NetPositionFn
    donor_lookup: DonorLookup


class Recommender(Protocol):
    def propose(self, inp: RecommenderInput) -> list[Recommendation]: ...


def protection_window(inp: RecommenderInput) -> int:
    """Replenishment protection period, never shorter than the reporting window (spec §7.2)."""
    return max(int(round(protection_period_days(inp.context))), inp.reporting_horizon_days)


def holding_delta_cost(*, units: int, unit_cost: Decimal, holding_rate: float) -> Decimal:
    """Signed holding-cost impact of a unit change (Decimal-safe)."""
    return Decimal(units) * unit_cost * Decimal(str(holding_rate))


def build_recommendation(
    inp: RecommenderInput,
    *,
    type: RecommendationType,
    current_stock: int,
    projected_demand: float,
    shortage_quantity: float,
    recommended_quantity: float,
    estimated_cost_impact: Decimal,
    reason: str,
    evidence: tuple[Evidence, ...],
    horizon_days: int,
    recommended_location: str | None = None,
    guardrail_flags: tuple[str, ...] = (),
    policy: PolicyRecommendation | None = None,
    current_policy: CurrentPolicy | None = None,
) -> Recommendation:
    ctx = inp.context
    return Recommendation(
        recommendation_id=str(ULID()),
        tenant_id=ctx.tenant_id,
        type=type,
        part_number=ctx.pn,
        description=ctx.description,
        current_location=ctx.location,
        recommended_location=recommended_location,
        current_stock=current_stock,
        projected_demand=projected_demand,
        shortage_quantity=shortage_quantity,
        recommended_quantity=recommended_quantity,
        estimated_cost_impact=estimated_cost_impact,
        aog_risk_level=AogRiskLevel.NONE,  # provisional — AogRiskScorer overwrites
        criticality_tier=int(ctx.criticality.canonical_tier),
        reason=reason,
        supporting_evidence=evidence,
        confidence_score=1.0,  # provisional — confidence stage overwrites
        horizon_days=horizon_days,
        suggested_autonomy_tier=AutonomyTier.BOUNDED,  # provisional — tier/AOG overwrite
        guardrail_flags=guardrail_flags,
        generated_at=inp.now,
        input_snapshot_hash=inp.input_snapshot_hash,
        policy=policy,
        current_policy=current_policy,
    )
