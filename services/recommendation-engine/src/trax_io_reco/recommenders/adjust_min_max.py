"""Adjust Min/Max recommender (spec §7.1) — the locked-v1 anchor."""

from __future__ import annotations

from trax_io_reco.contracts.enums import EvidenceKind, RecommendationType
from trax_io_reco.contracts.recommendation import Evidence, Recommendation
from trax_io_reco.policy.lead_time import protection_period_days
from trax_io_reco.recommenders.base import (
    RecommenderInput,
    build_recommendation,
    holding_delta_cost,
)

MATERIALITY = 0.05  # strict relative change threshold


def _rel_delta(new: int, old: int) -> float:
    return abs(new - old) / max(old, 1)


class AdjustMinMaxRecommender:
    def propose(self, inp: RecommenderInput) -> list[Recommendation]:
        proposed = inp.policy
        current = inp.context.current_policy
        deltas = {
            "rop": _rel_delta(proposed.rop, current.rop),
            "eoq": _rel_delta(proposed.eoq, current.eoq),
            "safety_stock": _rel_delta(proposed.safety_stock, current.safety_stock),
            "max_stock": _rel_delta(proposed.max_stock, current.max_stock),
        }
        if max(deltas.values()) <= MATERIALITY:
            return []  # no material change

        flags: list[str] = []
        if max(deltas.values()) > 1.0:
            flags.append("delta_gt_100pct")  # engine flags; Guardrail (#4) enforces the cap

        horizon = int(round(protection_period_days(inp.context)))
        unit_cost = inp.context.vendor_economics.unit_cost
        holding_rate = inp.context.tenant_policy_config.holding_cost_rate
        cost_impact = holding_delta_cost(
            units=proposed.max_stock - current.max_stock, unit_cost=unit_cost,
            holding_rate=holding_rate,
        )
        reason = (
            f"Recompute {proposed.policy_kind.value} levels "
            f"(ROP {current.rop}->{proposed.rop}, Max {current.max_stock}->{proposed.max_stock}) "
            f"at fill-rate {proposed.service_level_target:.1%}"
        )
        evidence = (
            Evidence(
                kind=EvidenceKind.DEMAND_HISTORY,
                ref_id=f"{inp.context.pn}@{inp.context.location}",
                detail=(
                    f"{inp.projection.dist_kind} demand ~{inp.projection.mean_per_day:.3f}/day "
                    f"over {inp.projection.basis_window_days}d"
                ),
            ),
        )
        return [
            build_recommendation(
                inp,
                type=RecommendationType.ADJUST_MIN_MAX,
                current_stock=inp.context.stock_position.serviceable,
                projected_demand=inp.projection.mean_per_day * horizon,
                shortage_quantity=0.0,
                recommended_quantity=float(proposed.max_stock),
                estimated_cost_impact=cost_impact,
                reason=reason,
                evidence=evidence,
                horizon_days=horizon,
                guardrail_flags=tuple(flags),
                policy=proposed,
                current_policy=current,
            )
        ]
