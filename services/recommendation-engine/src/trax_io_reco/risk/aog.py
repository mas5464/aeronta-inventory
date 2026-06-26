"""AOG risk scorer (spec §7.7). Cross-cutting: annotates every recommendation with a
risk level, a part-class-correct recovery time, a non-binding Tier-A suggestion when AOG
is active, and an expedite flag for high-risk buys/transfers."""

from __future__ import annotations

from datetime import timedelta

from trax_io_reco.contracts.context import NetPosition, PartLocationContext
from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType
from trax_io_reco.contracts.recommendation import Recommendation
from trax_io_reco.policy.lead_time import protection_period_days

_REPAIRABLE = {"rotable", "repairable"}
_LONG_RECOVERY_DAYS = 45.0
_AOG_WINDOW = timedelta(hours=72)


def _recovery_time_days(context: PartLocationContext) -> float:
    pc = context.part_attributes.part_class
    if pc in _REPAIRABLE and context.repair_tat.n_observations > 0:
        return float(context.repair_tat.p90_days)
    return protection_period_days(context)


def _risk_level(*, criticality: int, has_shortage: bool, long_recovery: bool) -> AogRiskLevel:
    if not has_shortage:
        return AogRiskLevel.LOW if criticality <= 2 else AogRiskLevel.NONE
    if criticality <= 2:
        return AogRiskLevel.CRITICAL if long_recovery else AogRiskLevel.HIGH
    if criticality == 3:
        return AogRiskLevel.HIGH if long_recovery else AogRiskLevel.MEDIUM
    return AogRiskLevel.MEDIUM if long_recovery else AogRiskLevel.LOW


class AogRiskScorer:
    def score(
        self, rec: Recommendation, *, context: PartLocationContext, net: NetPosition
    ) -> Recommendation:
        recovery = _recovery_time_days(context)
        has_shortage = rec.shortage_quantity > 0 or net.shortage > 0
        level = _risk_level(
            criticality=context.criticality.canonical_tier,
            has_shortage=has_shortage,
            long_recovery=recovery > _LONG_RECOVERY_DAYS,
        )

        flags = list(rec.guardrail_flags)
        tier = rec.suggested_autonomy_tier
        active = context.aog_signal.active or (
            context.aog_signal.last_shortage_at is not None
            and rec.generated_at - context.aog_signal.last_shortage_at <= _AOG_WINDOW
        )
        if active and "active_aog" not in flags:
            flags.append("active_aog")
        # High AOG risk (or an active AOG) warrants human review (spec §9.1 scenario 8).
        if active or level >= AogRiskLevel.HIGH:
            tier = AutonomyTier.ADVISOR  # non-binding suggestion (spec §2.3)

        reason = rec.reason
        if level >= AogRiskLevel.HIGH and rec.type in (
            RecommendationType.PURCHASE,
            RecommendationType.TRANSFER,
        ):
            reason = f"{reason} [EXPEDITE — AOG risk {level.name}]"

        return rec.model_copy(
            update={
                "aog_risk_level": level,
                "suggested_autonomy_tier": tier,
                "guardrail_flags": tuple(flags),
                "reason": reason,
            }
        )
