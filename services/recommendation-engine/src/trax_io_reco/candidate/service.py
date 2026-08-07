"""Recommendation-service adapter for deterministic per-key candidate frontiers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from trax_io_reco.candidate.identity import content_digest
from trax_io_reco.candidate.integration import (
    candidate_from_finalized_recommendations,
    no_change_from_calculation_evidence,
)
from trax_io_reco.candidate.models import model_identity_from_served
from trax_io_reco.candidate.planner import CandidatePlanner
from trax_io_reco.confidence import CONFIDENCE_MODEL_VERSION
from trax_io_reco.contracts.candidate import (
    CandidateFingerprintInputs,
    CandidateFrontier,
    CandidateTargetLevels,
    FingerprintComponent,
    LifecycleEconomics,
)
from trax_io_reco.contracts.context import (
    DemandProjection,
    PartLocationContext,
)
from trax_io_reco.contracts.enums import AogRiskLevel, RecommendationType
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.contracts.recommendation import Recommendation
from trax_io_reco.recommenders.base import (
    RecommenderInput,
    calculation_evidence_from_net,
    protection_window,
)
from trax_io_reco.risk.aog import AOG_RISK_MODEL_VERSION, risk_level_for_position

_CONSTRAINT_SET_VERSION = "policy-constraints-v1"
_ARBITRATION_VERSION = "transfer-first-residual-purchase-v1"
_ECONOMICS_VERSION = "candidate-lifecycle-cost-v1"
_OBJECTIVE_DEFINITION_VERSION = "shortage-aog-holding-ordering-v1"
_CANDIDATE_SERVICE_ADAPTER_VERSION = "recommendation-service-frontier-v1"


@dataclass(frozen=True)
class ServedCandidateMember:
    context: PartLocationContext
    projection: DemandProjection
    policy: PolicyRecommendation

    @property
    def decision_key(self) -> str:
        return f"{self.context.pn}@{self.context.location}"


def _levels(
    *,
    rop: int,
    eoq: int,
    safety_stock: int,
    max_stock: int,
) -> CandidateTargetLevels:
    return CandidateTargetLevels(
        rop=rop,
        eoq=eoq,
        safety_stock=safety_stock,
        max_stock=max_stock,
    )


def _current_levels(context: PartLocationContext) -> CandidateTargetLevels:
    current = context.current_policy
    return _levels(
        rop=current.rop,
        eoq=current.eoq,
        safety_stock=current.safety_stock,
        max_stock=current.max_stock,
    )


def _proposed_levels(policy: PolicyRecommendation) -> CandidateTargetLevels:
    return _levels(
        rop=policy.rop,
        eoq=policy.eoq,
        safety_stock=policy.safety_stock,
        max_stock=policy.max_stock,
    )


def _risk_ratio(
    *,
    context: PartLocationContext,
    has_shortage: bool,
) -> Decimal:
    level = risk_level_for_position(context=context, has_shortage=has_shortage)
    return Decimal(int(level)) / Decimal(int(AogRiskLevel.CRITICAL))


def _ending_net(
    *,
    net_before: Decimal,
    recommendations: tuple[Recommendation, ...],
) -> Decimal:
    inbound = Decimal("0")
    outbound = Decimal("0")
    for recommendation in recommendations:
        quantity = Decimal(str(recommendation.recommended_quantity))
        if recommendation.type in {
            RecommendationType.PURCHASE,
            RecommendationType.TRANSFER,
        }:
            inbound += quantity
        elif recommendation.type in {
            RecommendationType.REDUCE_STOCK,
            RecommendationType.SELL,
        }:
            outbound += quantity
    return net_before + inbound - outbound


def _canonical_horizon(
    *,
    inp: RecommenderInput,
    recommendations: tuple[Recommendation, ...],
) -> int:
    priorities = {
        RecommendationType.PURCHASE: 0,
        RecommendationType.TRANSFER: 0,
        RecommendationType.REDUCE_STOCK: 1,
        RecommendationType.SELL: 1,
        RecommendationType.ADJUST_MIN_MAX: 2,
    }
    if not recommendations:
        return protection_window(inp)
    selected = min(
        recommendations,
        key=lambda recommendation: (
            priorities[recommendation.type],
            recommendation.horizon_days,
            recommendation.type.value,
        ),
    )
    return selected.horizon_days


def _observation_window(
    members: tuple[ServedCandidateMember, ...],
) -> tuple[date | None, date | None, tuple[FingerprintComponent, ...]]:
    windows = {
        (
            member.context.demand_history.observation_start,
            member.context.demand_history.observation_end,
        )
        for member in members
    }
    if len(windows) == 1:
        start, end = windows.pop()
        if (start is None) == (end is None):
            return start, end, ()
    components = tuple(
        FingerprintComponent(
            name=f"demand_observation_window:{member.decision_key}",
            value=(
                f"{member.context.demand_history.observation_start or 'unavailable'}"
                f"/{member.context.demand_history.observation_end or 'unavailable'}"
            ),
        )
        for member in sorted(members, key=lambda item: item.decision_key)
    )
    return None, None, components


def _infeasibility_reasons(
    recommendations: tuple[Recommendation, ...],
) -> tuple[str, ...]:
    hard_stops = {"delta_gt_100pct", "open_order_deferral"}
    return tuple(
        sorted(
            hard_stops.intersection(
                flag
                for recommendation in recommendations
                for flag in recommendation.guardrail_flags
            )
        )
    )


def _physical_groups(
    recommendations: tuple[Recommendation, ...],
) -> tuple[tuple[Recommendation, ...], ...]:
    by_type = {recommendation.type: recommendation for recommendation in recommendations}
    groups: list[tuple[Recommendation, ...]] = []
    transfer_purchase = tuple(
        by_type[item]
        for item in (RecommendationType.TRANSFER, RecommendationType.PURCHASE)
        if item in by_type
    )
    if transfer_purchase:
        groups.append(transfer_purchase)
    for item in (RecommendationType.REDUCE_STOCK, RecommendationType.SELL):
        if item in by_type:
            groups.append((by_type[item],))
    return tuple(groups)


def build_service_frontier(
    *,
    inp: RecommenderInput,
    served_members: tuple[ServedCandidateMember, ...],
    expected_member_keys: tuple[str, ...],
    finalized_recommendations: tuple[Recommendation, ...],
    confidence: Decimal,
) -> CandidateFrontier:
    """Build one frontier from the exact post-arbitration recommendation ledger."""

    horizon_days = _canonical_horizon(
        inp=inp,
        recommendations=finalized_recommendations,
    )
    served_net = inp.net_position(horizon_days)
    trace, trace_snapshot_hash = calculation_evidence_from_net(
        inp,
        horizon_days=horizon_days,
        calculation_net=served_net,
    )
    compatible = tuple(
        recommendation
        for recommendation in finalized_recommendations
        if recommendation.calculation_evidence == trace
        and recommendation.input_snapshot_hash == trace_snapshot_hash
    )
    decision_key = f"{inp.context.pn}@{inp.context.location}"
    member_keys = tuple(sorted(f"{member.pn}@{member.location}" for member in trace.members))
    members_by_key = {member.decision_key: member for member in served_members}
    if set(member_keys) != set(members_by_key):
        raise ValueError("served candidate members do not match calculation evidence")
    model_identity = model_identity_from_served(
        decision_key=decision_key,
        projection=inp.projection,
        policy=inp.policy,
        member_projections=(
            {
                member_key: members_by_key[member_key].projection
                for member_key in member_keys
            }
            if len(member_keys) > 1
            else None
        ),
    )

    currency = inp.context.tenant_policy_config.currency.upper()
    unit_value = inp.context.vendor_economics.unit_cost
    economics = LifecycleEconomics(
        currency=currency,
        inventory_unit_value=unit_value,
        annual_holding_rate=Decimal(
            str(inp.context.tenant_policy_config.holding_cost_rate)
        ),
        ordering_cost_per_purchase=Decimal(
            str(inp.context.tenant_policy_config.ordering_cost)
        ),
        # Recommended default: one unit of unfilled demand carries one unit-value
        # penalty until the tenant objective configuration is introduced.
        shortage_cost_per_unit=unit_value,
        horizon_days=horizon_days,
    )
    observation_start, observation_end, window_components = _observation_window(
        served_members
    )
    planner = CandidatePlanner()
    fingerprint_inputs = CandidateFingerprintInputs(
        tenant_id=inp.context.tenant_id,
        decision_key=decision_key,
        member_keys=member_keys,
        source_snapshot_hash=inp.input_snapshot_hash,
        context_digest=content_digest(
            {
                "served_members": tuple(
                    {
                        "context": member.context,
                        "projection": member.projection,
                        "policy": member.policy,
                    }
                    for member in sorted(
                        served_members,
                        key=lambda item: item.decision_key,
                    )
                ),
                "expected_member_keys": expected_member_keys,
            }
        ),
        tenant_policy_version=content_digest(inp.context.tenant_policy_config),
        observation_start=observation_start,
        observation_end=observation_end,
        as_of=inp.as_of,
        horizon_days=horizon_days,
        currency=currency,
        model_identity=model_identity,
        constraint_set_version=_CONSTRAINT_SET_VERSION,
        arbitration_version=_ARBITRATION_VERSION,
        economics_version=_ECONOMICS_VERSION,
        objective_definition_version=_OBJECTIVE_DEFINITION_VERSION,
        objective_inputs=(
            FingerprintComponent(
                name="annual_holding_rate",
                value=str(inp.context.tenant_policy_config.holding_cost_rate),
            ),
            FingerprintComponent(
                name="ordering_cost_per_purchase",
                value=str(inp.context.tenant_policy_config.ordering_cost),
            ),
            FingerprintComponent(
                name="shortage_cost_per_unit",
                value=format(unit_value, "f"),
            ),
        ),
        additional_result_inputs=tuple(
            (
                FingerprintComponent(
                    name="calculation_trace",
                    value=content_digest(trace),
                ),
                FingerprintComponent(
                    name="expected_member_keys",
                    value=",".join(sorted(expected_member_keys)),
                ),
                FingerprintComponent(
                    name="reporting_horizon_days",
                    value=str(inp.reporting_horizon_days),
                ),
                FingerprintComponent(
                    name="confidence_model_version",
                    value=CONFIDENCE_MODEL_VERSION,
                ),
                FingerprintComponent(
                    name="confidence_score",
                    value=format(confidence, "f"),
                ),
                FingerprintComponent(
                    name="aog_risk_model_version",
                    value=AOG_RISK_MODEL_VERSION,
                ),
                FingerprintComponent(
                    name="candidate_service_adapter_version",
                    value=_CANDIDATE_SERVICE_ADAPTER_VERSION,
                ),
                *window_components,
                *(
                    (
                        FingerprintComponent(
                            name="aog_evaluation_instant",
                            value=inp.now.isoformat(),
                        ),
                    )
                    if inp.context.aog_signal.last_shortage_at is not None
                    else ()
                ),
            )
        ),
    )
    frontier_id = planner.fingerprint(fingerprint_inputs)
    current_levels = _current_levels(inp.context)
    proposed_levels = _proposed_levels(inp.policy)
    baseline = no_change_from_calculation_evidence(
        frontier_id=frontier_id,
        tenant_id=inp.context.tenant_id,
        pn=inp.context.pn,
        location=inp.context.location,
        input_snapshot_hash=trace_snapshot_hash,
        calculation_evidence=trace,
        model_identity=model_identity,
        current_levels=current_levels,
        economics=economics,
        expected_aog_risk=_risk_ratio(
            context=inp.context,
            has_shortage=trace.shortage_before_action > 0,
        ),
        confidence=confidence,
    )

    candidates = [baseline]
    adjust = next(
        (
            recommendation
            for recommendation in compatible
            if recommendation.type == RecommendationType.ADJUST_MIN_MAX
        ),
        None,
    )
    if adjust is not None:
        candidates.append(
            candidate_from_finalized_recommendations(
                frontier_id=frontier_id,
                recommendations=(adjust,),
                model_identity=model_identity,
                current_levels=current_levels,
                target_levels=proposed_levels,
                economics=economics,
                expected_aog_risk=_risk_ratio(
                    context=inp.context,
                    has_shortage=trace.shortage_before_action > 0,
                ),
                purchase_unit_cost=unit_value,
                infeasibility_reasons=_infeasibility_reasons((adjust,)),
            )
        )

    for physical in _physical_groups(compatible):
        ending_net = _ending_net(
            net_before=Decimal(str(trace.net_position)),
            recommendations=physical,
        )
        common = {
            "frontier_id": frontier_id,
            "model_identity": model_identity,
            "current_levels": current_levels,
            "economics": economics,
            "expected_aog_risk": _risk_ratio(
                context=inp.context,
                has_shortage=ending_net < 0,
            ),
            "purchase_unit_cost": unit_value,
        }
        candidates.append(
            candidate_from_finalized_recommendations(
                recommendations=physical,
                target_levels=current_levels,
                infeasibility_reasons=_infeasibility_reasons(physical),
                **common,
            )
        )
        if adjust is not None:
            combined = (*physical, adjust)
            candidates.append(
                candidate_from_finalized_recommendations(
                    recommendations=combined,
                    target_levels=proposed_levels,
                    infeasibility_reasons=_infeasibility_reasons(combined),
                    **common,
                )
            )

    return planner.build_frontier(
        inputs=fingerprint_inputs,
        candidates=tuple(candidates),
    )


__all__ = ["ServedCandidateMember", "build_service_frontier"]
