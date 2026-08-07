"""Reduce Stock / Sell recommender (spec §7.4). Flags high-value, slow/dead inventory."""

from __future__ import annotations

from trax_io_reco.contracts.enums import EvidenceKind, RecommendationType
from trax_io_reco.contracts.policy import AppliedConstraint
from trax_io_reco.contracts.recommendation import Evidence, Recommendation
from trax_io_reco.demand.basis import (
    demand_basis_trace,
    projected_demand_in_horizon,
    scheduled_items_in_horizon,
)
from trax_io_reco.recommenders.base import (
    RecommenderInput,
    build_recommendation,
    holding_delta_cost,
)

EXCESS_MULTIPLE = 1.5  # serviceable > 1.5 * Max (strict)


class ReduceSellRecommender:
    def propose(self, inp: RecommenderInput) -> list[Recommendation]:
        ctx = inp.context
        serviceable = ctx.stock_position.serviceable
        # Compare against the freshly computed target Max (always >= 1), not the stale
        # current Max which may be 0 for a newly-onboarded PN (would falsely flag any stock).
        max_stock = max(inp.policy.max_stock, ctx.current_policy.max_stock)
        unit_cost = ctx.vendor_economics.unit_cost
        threshold = ctx.tenant_policy_config.high_value_threshold

        demand_trace = demand_basis_trace(ctx.demand_history)
        if (
            demand_trace.exposure_days <= 0
            or demand_trace.observation_window_source == "unavailable"
        ):
            # Unknown history is not evidence of zero usage. The service also
            # skips such keys; keep this boundary safe for direct recommender use.
            return []
        if ctx.scheduled_demand_status != "available":
            # Unknown or partial future demand is not evidence that inventory is
            # disposable.  Suppress both sell and reduce recommendations until the
            # requisition snapshot is complete.
            return []
        usage = demand_trace.demanded_units
        excess = serviceable - max_stock
        shelf_expiring = bool(ctx.part_attributes.shelf_life_days) and serviceable > max_stock

        is_excess_highvalue = (
            serviceable > EXCESS_MULTIPLE * max_stock
            and usage == 0
            and float(unit_cost) >= threshold
        )
        if not (is_excess_highvalue or shelf_expiring) or excess <= 0:
            return []

        scheduled_in_horizon = scheduled_items_in_horizon(
            ctx.scheduled_demand,
            as_of=inp.as_of,
            horizon_days=inp.reporting_horizon_days,
        )
        has_future = bool(scheduled_in_horizon)
        sell = usage == 0 and not has_future and float(unit_cost) >= threshold
        rec_type = RecommendationType.SELL if sell else RecommendationType.REDUCE_STOCK

        evidence: list[Evidence] = [
            Evidence(
                kind=EvidenceKind.DEMAND_HISTORY,
                ref_id=f"{ctx.pn}@{ctx.location}",
                detail=f"{usage} units used over {demand_trace.exposure_days}d; "
                f"on-hand {serviceable} vs Max {max_stock}",
            )
        ]
        if shelf_expiring:
            evidence.append(
                Evidence(
                    kind=EvidenceKind.SHELF_LIFE,
                    ref_id=str(ctx.part_attributes.shelf_life_days),
                    detail=f"shelf life {ctx.part_attributes.shelf_life_days}d",
                )
            )
        verb = "Sell" if sell else "Reduce stock / lower reorder levels"
        reason = (
            f"{verb}: {excess} excess units of a high-value part "
            f"(${float(unit_cost):,.0f}) with {usage} usage"
        )
        # Holding cost released (negative impact = savings).
        cost_impact = -holding_delta_cost(
            units=excess,
            unit_cost=unit_cost,
            holding_rate=ctx.tenant_policy_config.holding_cost_rate,
        )
        return [
            build_recommendation(
                inp,
                type=rec_type,
                current_stock=serviceable,
                projected_demand=projected_demand_in_horizon(
                    historical_per_day=inp.projection.historical_component,
                    scheduled_items=ctx.scheduled_demand,
                    as_of=inp.as_of,
                    horizon_days=inp.reporting_horizon_days,
                ),
                shortage_quantity=0.0,
                recommended_quantity=float(excess),
                estimated_cost_impact=cost_impact,
                reason=reason,
                evidence=tuple(evidence),
                horizon_days=inp.reporting_horizon_days,
                additional_action_constraints=(
                    AppliedConstraint(
                        name="outbound_excess_limit",
                        value=str(excess),
                        binding=True,
                        source="dispatchable_serviceable_minus_target_max",
                        scope="action",
                    ),
                    AppliedConstraint(
                        name="scheduled_demand_sell_gate",
                        value=str(len(scheduled_in_horizon)),
                        binding=has_future,
                        source="scheduled_demand_snapshot:requested_horizon",
                        scope="action",
                    ),
                ),
            )
        ]
