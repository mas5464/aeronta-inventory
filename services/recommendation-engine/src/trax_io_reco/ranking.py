"""Suggested-tier logic + deterministic ranking (spec §7.8 / §7.9)."""

from __future__ import annotations

from decimal import Decimal

from trax_io_reco.contracts.enums import AutonomyTier, RecommendationType
from trax_io_reco.contracts.recommendation import Recommendation

_TYPE_ORDER = {
    RecommendationType.PURCHASE: 0,
    RecommendationType.TRANSFER: 1,
    RecommendationType.ADJUST_MIN_MAX: 2,
    RecommendationType.REDUCE_STOCK: 3,
    RecommendationType.SELL: 4,
}


def suggest_tier(
    *, criticality: int, unit_cost: float, delta_pct: float, active_aog: bool
) -> AutonomyTier:
    """Non-binding autonomy-tier suggestion per the §6.1 default criteria."""
    if criticality == 1 or unit_cost >= 10_000 or delta_pct > 0.25 or active_aog:
        return AutonomyTier.ADVISOR
    if criticality in (4, 5) and unit_cost < 500 and delta_pct <= 0.40:
        return AutonomyTier.AUTONOMOUS
    return AutonomyTier.BOUNDED


def _score(rec: Recommendation) -> Decimal:
    # criticality_weight x cost magnitude x AOG factor (spec §7.9). Cost already scales with
    # the action magnitude (qty x unit_cost / holding delta), subsuming the |delta| factor.
    criticality_weight = Decimal(6 - int(rec.criticality_tier))  # tier 1 -> 5 ... tier 5 -> 1
    aog_factor = Decimal(1 + int(rec.aog_risk_level))
    return criticality_weight * abs(rec.estimated_cost_impact) * aog_factor


def rank(recs: list[Recommendation]) -> list[Recommendation]:
    """Total order: score desc, then a deterministic tie-break independent of input order
    (criticality asc, part_number asc, type order, location, recommended_location, reason)."""
    return sorted(
        recs,
        key=lambda r: (
            -_score(r),
            int(r.criticality_tier),
            r.part_number,
            _TYPE_ORDER[r.type],
            r.current_location,
            r.recommended_location or "",
            r.reason,
        ),
    )
