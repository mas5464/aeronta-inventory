"""Recommender interface + shared construction helpers (spec §7).

A recommender receives a RecommenderInput bundle and returns 0..n Recommendation. AOG
risk level, suggested tier, and confidence are filled with provisional placeholders here
and overwritten by the downstream AOG / confidence / ranking stages.
"""

from __future__ import annotations

import hashlib
import json
import math
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
from trax_io_reco.contracts.policy import AppliedConstraint, PolicyRecommendation
from trax_io_reco.contracts.recommendation import (
    CalculationEvidence,
    CalculationMemberEvidence,
    Evidence,
    Recommendation,
)
from trax_io_reco.policy.lead_time import protection_period_days
from trax_io_reco.position.net_position import (
    available,
    open_receipts_in_horizon,
)
from trax_io_reco.position.net_position import (
    net_position as calculate_net_position,
)


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


def calculation_evidence_from_net(
    inp: RecommenderInput,
    *,
    horizon_days: int,
    calculation_net: NetPosition | None = None,
) -> tuple[CalculationEvidence, str]:
    """Build the exact, content-addressed ledger for one served horizon.

    Candidate previews use this same function for their no-change baseline, so
    recommendation and candidate arithmetic cannot silently drift apart.
    """

    ctx = inp.context
    served_net = calculation_net or calculate_net_position(
        context=ctx,
        projection=inp.projection,
        window_days=horizon_days,
        as_of=inp.as_of,
    )
    member_positions = served_net.member_contributions
    if not member_positions:
        # Additive compatibility for tests or integrations that still provide the
        # original aggregate-only NetPosition contract.  New engine calculations
        # always carry exact member contributions.
        historical = served_net.projected_historical_demand
        scheduled = served_net.scheduled_demand_in_window
        if abs(historical + scheduled - served_net.projected_demand) > 1e-6:
            historical = served_net.projected_demand
            scheduled = 0.0
        open_receipts = served_net.open_receipts_in_window
        repair_receipts = served_net.repair_receipts_in_window
        if abs(open_receipts + repair_receipts - served_net.expected_receipts_in_window) > 1e-6:
            open_receipts = served_net.expected_receipts_in_window
            repair_receipts = 0.0
        member_evidence = (
            CalculationMemberEvidence(
                pn=served_net.pn,
                location=served_net.location,
                projection_kind=inp.projection.dist_kind,
                projected_historical_demand=historical,
                scheduled_demand_due=scheduled,
                projected_demand=served_net.projected_demand,
                dispatchable_available=served_net.available,
                open_receipts_due=open_receipts,
                overdue_open_receipts_due=min(
                    served_net.overdue_open_receipts_in_window,
                    open_receipts,
                ),
                repair_receipts_due=repair_receipts,
                expected_receipts_due=served_net.expected_receipts_in_window,
                net_position=served_net.net,
                scheduled_demand_status=served_net.scheduled_demand_status,
                scheduled_demand_undated_lines=(served_net.scheduled_demand_undated_lines),
                scheduled_demand_undated_units=(served_net.scheduled_demand_undated_units),
                open_receipts_status=served_net.open_receipts_status,
                open_receipts_undated_lines=(served_net.open_receipts_undated_lines),
                open_receipts_undated_units=(served_net.open_receipts_undated_units),
            ),
        )
    else:
        member_evidence = tuple(
            CalculationMemberEvidence(
                pn=member.pn,
                location=member.location,
                projection_kind=member.projection_kind,
                projected_historical_demand=member.projected_historical_demand,
                scheduled_demand_due=member.scheduled_demand_in_window,
                projected_demand=member.projected_demand,
                dispatchable_available=member.available,
                open_receipts_due=member.open_receipts_in_window,
                overdue_open_receipts_due=member.overdue_open_receipts_in_window,
                repair_receipts_due=member.repair_receipts_in_window,
                expected_receipts_due=member.expected_receipts_in_window,
                net_position=member.net,
                scheduled_demand_status=member.scheduled_demand_status,
                scheduled_demand_undated_lines=(member.scheduled_demand_undated_lines),
                scheduled_demand_undated_units=(member.scheduled_demand_undated_units),
                open_receipts_status=member.open_receipts_status,
                open_receipts_undated_lines=member.open_receipts_undated_lines,
                open_receipts_undated_units=member.open_receipts_undated_units,
            )
            for member in member_positions
        )

    projection_kinds = sorted({member.projection_kind for member in member_evidence})
    projection_kind = (
        projection_kinds[0]
        if len(projection_kinds) == 1
        else f"POOLED[{','.join(projection_kinds)}]"
    )
    projected_historical = sum(member.projected_historical_demand for member in member_evidence)
    calculation_evidence = CalculationEvidence(
        as_of=inp.as_of,
        horizon_days=horizon_days,
        projection_kind=projection_kind,
        served_historical_per_day=(
            projected_historical / horizon_days if horizon_days > 0 else 0.0
        ),
        projected_historical_demand=projected_historical,
        scheduled_demand_due=sum(member.scheduled_demand_due for member in member_evidence),
        projected_demand=served_net.projected_demand,
        dispatchable_available=served_net.available,
        open_receipts_due=sum(member.open_receipts_due for member in member_evidence),
        overdue_open_receipts_due=sum(
            member.overdue_open_receipts_due for member in member_evidence
        ),
        repair_receipts_due=sum(member.repair_receipts_due for member in member_evidence),
        expected_receipts_due=served_net.expected_receipts_in_window,
        net_position=served_net.net,
        shortage_before_action=served_net.shortage,
        pooled_group_id=(served_net.group_id if served_net.pooling_scope != "single_key" else None),
        members=member_evidence,
        scheduled_demand_status=served_net.scheduled_demand_status,
        scheduled_demand_undated_lines=served_net.scheduled_demand_undated_lines,
        scheduled_demand_undated_units=served_net.scheduled_demand_undated_units,
        open_receipts_status=served_net.open_receipts_status,
        open_receipts_undated_lines=served_net.open_receipts_undated_lines,
        open_receipts_undated_units=served_net.open_receipts_undated_units,
        pooling_scope=served_net.pooling_scope,
        excluded_member_keys=served_net.excluded_member_keys,
    )
    action_snapshot_hash = hashlib.sha256(
        json.dumps(
            {
                "base_input_snapshot_hash": inp.input_snapshot_hash,
                "calculation": calculation_evidence.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return calculation_evidence, action_snapshot_hash


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
    calculation_net: NetPosition | None = None,
    additional_action_constraints: tuple[AppliedConstraint, ...] = (),
) -> Recommendation:
    ctx = inp.context
    served_net = calculation_net or calculate_net_position(
        context=ctx,
        projection=inp.projection,
        window_days=horizon_days,
        as_of=inp.as_of,
    )
    if abs(float(projected_demand) - served_net.projected_demand) > 1e-6:
        raise ValueError(
            "recommendation projected demand must match the served net-position demand"
        )
    calculation_evidence, action_snapshot_hash = calculation_evidence_from_net(
        inp,
        horizon_days=horizon_days,
        calculation_net=served_net,
    )

    receipts = open_receipts_in_horizon(
        ctx.open_orders,
        as_of=inp.as_of,
        horizon_days=horizon_days,
    )
    available_plus_receipts = available(ctx.stock_position) + receipts.open_receipts_due
    open_order_coverage_incomplete = served_net.open_receipts_status != "available"
    deferral = AppliedConstraint(
        name="open_order_deferral",
        value=(
            None
            if open_order_coverage_incomplete
            else format(available_plus_receipts, ".6g")
        ),
        binding=(
            open_order_coverage_incomplete
            or available_plus_receipts > inp.policy.max_stock
        ),
        source=(
            f"open_orders_snapshot:{served_net.open_receipts_status}"
            if open_order_coverage_incomplete
            else "dispatchable_available+open_orders_snapshot"
        ),
        scope="action",
    )
    # A policy may have been computed directly with a horizon by a legacy
    # caller. Replace that evidence with the horizon of this actual action.
    policy_constraints = tuple(
        constraint
        for constraint in inp.policy.applied_constraints
        if constraint.scope == "policy" and constraint.name != "open_order_deferral"
    )
    action_constraints: tuple[AppliedConstraint, ...] = ()
    action_flags: tuple[str, ...] = ()
    if type == RecommendationType.ADJUST_MIN_MAX:
        action_constraints += (deferral,)
        action_flags = ("open_order_deferral",) if deferral.binding else ()
    if type == RecommendationType.PURCHASE:
        minimum_order_quantity = int(ctx.vendor_economics.minimum_order_qty)
        requested_without_floor = int(
            math.ceil(max(0.0, shortage_quantity + inp.policy.safety_stock))
        )
        action_constraints += (
            AppliedConstraint(
                name="minimum_order_quantity_action",
                value=str(minimum_order_quantity),
                binding=(
                    minimum_order_quantity > requested_without_floor
                    and recommended_quantity >= minimum_order_quantity
                ),
                source="vendor_economics.minimum_order_qty",
                scope="action",
            ),
        )
    if any(constraint.scope != "action" for constraint in additional_action_constraints):
        raise ValueError("additional action constraints must use scope='action'")
    action_constraints += additional_action_constraints
    applied_constraints = policy_constraints + action_constraints
    policy_flags = tuple(
        flag for flag in inp.policy.constraint_flags if flag != "open_order_deferral"
    )
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
        guardrail_flags=tuple(
            dict.fromkeys(
                (
                    *guardrail_flags,
                    *policy_flags,
                    *action_flags,
                )
            )
        ),
        generated_at=inp.now,
        input_snapshot_hash=action_snapshot_hash,
        policy=policy,
        current_policy=current_policy,
        applied_constraints=applied_constraints,
        calculation_evidence=calculation_evidence,
    )
