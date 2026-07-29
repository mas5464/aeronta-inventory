"""Adapters from finalized recommendation outputs to reconciled candidates.

The recommendation service may use these helpers only *after* constraint application
and arbitration.  They deliberately ignore the recommendation's precomputed cost
impact and recompute cash, lifecycle cost, shortage, and service from the finalized
action quantities plus the exact calculation trace.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from trax_io_reco.candidate.reconcile import (
    build_no_change_candidate,
    reconcile_candidate,
)
from trax_io_reco.contracts.candidate import (
    CandidateActionLine,
    CandidateEvidence,
    CandidateKind,
    CandidateTargetLevels,
    ConstraintEvidence,
    LifecycleEconomics,
    ModelIdentity,
    PolicyCandidate,
)
from trax_io_reco.contracts.enums import RecommendationType
from trax_io_reco.contracts.policy import AppliedConstraint
from trax_io_reco.contracts.recommendation import CalculationEvidence, Recommendation


def _decimal(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("candidate quantities cannot be booleans")
    parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("candidate quantities must be finite")
    return parsed


def _trace_and_key(
    recommendations: tuple[Recommendation, ...],
) -> tuple[Recommendation, CalculationEvidence, str, tuple[str, ...]]:
    if not recommendations:
        raise ValueError("at least one finalized recommendation is required")
    first = recommendations[0]
    trace = first.calculation_evidence
    if trace is None:
        raise ValueError("candidate planning requires exact recommendation calculation evidence")
    decision_key = f"{first.part_number}@{first.current_location}"
    member_keys = tuple(
        sorted(f"{member.pn}@{member.location}" for member in trace.members)
    )
    for recommendation in recommendations[1:]:
        if recommendation.tenant_id != first.tenant_id:
            raise ValueError("cannot combine recommendations from different tenants")
        if (
            recommendation.part_number != first.part_number
            or recommendation.current_location != first.current_location
        ):
            raise ValueError("cannot combine recommendations from different decision keys")
        if recommendation.calculation_evidence != trace:
            raise ValueError("combined recommendations must use the same exact calculation trace")
        if recommendation.input_snapshot_hash != first.input_snapshot_hash:
            raise ValueError("combined recommendations must use the same input snapshot")
    return first, trace, decision_key, member_keys


def _target_levels(recommendation: Recommendation) -> CandidateTargetLevels | None:
    policy = recommendation.policy
    if policy is None:
        return None
    return CandidateTargetLevels(
        rop=policy.rop,
        eoq=policy.eoq,
        safety_stock=policy.safety_stock,
        max_stock=policy.max_stock,
    )


def _constraint_evidence(
    recommendations: tuple[Recommendation, ...],
) -> tuple[ConstraintEvidence, ...]:
    return _constraint_evidence_from_applied(
        constraint
        for recommendation in recommendations
        for constraint in recommendation.applied_constraints
    )


def _constraint_evidence_from_applied(
    constraints: Iterable[AppliedConstraint],
) -> tuple[ConstraintEvidence, ...]:
    by_id: dict[str, ConstraintEvidence] = {}
    for constraint in constraints:
        candidate_constraint = ConstraintEvidence(
            constraint_id=constraint.name,
            source=constraint.source,
            value=constraint.value,
            scope=constraint.scope,
            hard=True,
            satisfied=True,
            binding=constraint.binding,
        )
        previous = by_id.get(candidate_constraint.constraint_id)
        if previous is not None and previous != candidate_constraint:
            raise ValueError(
                f"conflicting finalized constraint {candidate_constraint.constraint_id}"
            )
        by_id[candidate_constraint.constraint_id] = candidate_constraint
    return tuple(by_id[key] for key in sorted(by_id))


def _candidate_evidence(
    recommendations: tuple[Recommendation, ...],
    *,
    snapshot_hash: str,
) -> tuple[CandidateEvidence, ...]:
    evidence: dict[tuple[str, str, str | None, str], CandidateEvidence] = {}

    def add(item: CandidateEvidence) -> None:
        key = (item.kind, item.source, item.reference_id, item.detail)
        evidence[key] = item

    add(
        CandidateEvidence(
            kind="planning_trace",
            source="recommendation.calculation_evidence",
            reference_id=snapshot_hash,
            detail="Exact demand, availability, and receipt operands used by the action",
        )
    )
    for recommendation in recommendations:
        add(
            CandidateEvidence(
                kind="recommendation_reason",
                source=f"recommender.{recommendation.type.value}",
                detail=recommendation.reason,
            )
        )
        for item in recommendation.supporting_evidence:
            add(
                CandidateEvidence(
                    kind=item.kind.value,
                    source="recommendation.supporting_evidence",
                    reference_id=item.ref_id,
                    detail=item.detail,
                )
            )
        for flag in recommendation.guardrail_flags:
            add(
                CandidateEvidence(
                    kind="guardrail_flag",
                    source="recommendation.guardrail_flags",
                    reference_id=flag,
                    detail=f"Advisory guardrail flag: {flag}",
                )
            )
    ordered_keys = sorted(
        evidence,
        key=lambda item: (item[0], item[1], item[2] or "", item[3]),
    )
    return tuple(evidence[key] for key in ordered_keys)


def _finalized_actions(
    recommendations: tuple[Recommendation, ...],
    *,
    currency: str,
    purchase_unit_cost: Decimal | int | str | None,
) -> tuple[CandidateKind, tuple[CandidateActionLine, ...], str]:
    by_type: dict[RecommendationType, Recommendation] = {}
    for recommendation in recommendations:
        if recommendation.type in by_type:
            raise ValueError(
                f"multiple finalized {recommendation.type.value} recommendations for one candidate"
            )
        by_type[recommendation.type] = recommendation

    physical_types = set(by_type) - {RecommendationType.ADJUST_MIN_MAX}
    allowed_composite = {RecommendationType.TRANSFER, RecommendationType.PURCHASE}
    if len(physical_types) > 1 and physical_types != allowed_composite:
        raise ValueError("finalized recommendations contain contradictory physical actions")
    if not physical_types and RecommendationType.ADJUST_MIN_MAX not in by_type:
        raise ValueError("finalized recommendations do not contain an action")

    actions: list[CandidateActionLine] = []
    transfer = by_type.get(RecommendationType.TRANSFER)
    if transfer is not None:
        if not transfer.recommended_location:
            raise ValueError("a finalized transfer must disclose its donor location")
        actions.append(
            CandidateActionLine(
                line_id=f"transfer-in:{transfer.recommended_location}->{transfer.current_location}",
                kind="transfer_in",
                quantity=_decimal(transfer.recommended_quantity),
                currency=currency,
                unit_acquisition_cash=0,
                source_location=transfer.recommended_location,
                destination_location=transfer.current_location,
            )
        )

    purchase = by_type.get(RecommendationType.PURCHASE)
    if purchase is not None:
        if purchase_unit_cost is None:
            raise ValueError("purchase_unit_cost is required for a purchase candidate")
        actions.append(
            CandidateActionLine(
                line_id=f"purchase:{purchase.current_location}",
                kind="purchase",
                quantity=_decimal(purchase.recommended_quantity),
                currency=currency,
                unit_acquisition_cash=_decimal(purchase_unit_cost),
                destination_location=purchase.current_location,
            )
        )

    reduce = by_type.get(RecommendationType.REDUCE_STOCK)
    if reduce is not None:
        actions.append(
            CandidateActionLine(
                line_id=f"reduce-stock:{reduce.current_location}",
                kind="reduce_stock",
                quantity=_decimal(reduce.recommended_quantity),
                currency=currency,
                unit_acquisition_cash=0,
                source_location=reduce.current_location,
            )
        )

    sell = by_type.get(RecommendationType.SELL)
    if sell is not None:
        actions.append(
            CandidateActionLine(
                line_id=f"sell:{sell.current_location}",
                kind="sell",
                quantity=_decimal(sell.recommended_quantity),
                currency=currency,
                unit_acquisition_cash=0,
                source_location=sell.current_location,
            )
        )

    adjust = by_type.get(RecommendationType.ADJUST_MIN_MAX)
    if adjust is not None:
        actions.append(
            CandidateActionLine(
                line_id=f"adjust-policy:{adjust.current_location}",
                kind="adjust_policy",
                quantity=0,
                currency=currency,
                unit_acquisition_cash=0,
            )
        )

    if physical_types == allowed_composite:
        return "transfer_purchase", tuple(actions), "Transfer first, purchase residual"
    if physical_types == {RecommendationType.TRANSFER}:
        return "transfer", tuple(actions), "Transfer"
    if physical_types == {RecommendationType.PURCHASE}:
        return "purchase", tuple(actions), "Purchase"
    if physical_types == {RecommendationType.REDUCE_STOCK}:
        return "reduce_stock", tuple(actions), "Reduce stock"
    if physical_types == {RecommendationType.SELL}:
        return "sell", tuple(actions), "Sell"
    return "adjust_policy", tuple(actions), "Adjust policy"


def candidate_from_finalized_recommendations(
    *,
    frontier_id: str,
    recommendations: Iterable[Recommendation],
    model_identity: ModelIdentity,
    current_levels: CandidateTargetLevels,
    target_levels: CandidateTargetLevels,
    economics: LifecycleEconomics,
    expected_aog_risk: Decimal | int | str,
    purchase_unit_cost: Decimal | int | str | None = None,
    confidence: Decimal | int | str | None = None,
    infeasibility_reasons: Iterable[str] = (),
) -> PolicyCandidate:
    """Reconcile one option from finalized, post-arbitration recommendations.

    A transfer plus its residual purchase must be passed together so the portfolio
    optimizer sees one selectable coordinated option.  An optional policy-adjustment
    recommendation may accompany any physical action when it uses the same trace.
    """

    finalized = tuple(recommendations)
    first, trace, decision_key, member_keys = _trace_and_key(finalized)
    if economics.horizon_days != trace.horizon_days:
        raise ValueError("candidate economics horizon must match the exact calculation trace")
    inferred_targets = {
        levels
        for recommendation in finalized
        if (levels := _target_levels(recommendation)) is not None
    }
    if inferred_targets and inferred_targets != {target_levels}:
        raise ValueError("target_levels do not match the finalized policy recommendation")

    confidence_value: Decimal
    if confidence is None:
        confidence_values = {_decimal(item.confidence_score) for item in finalized}
        if len(confidence_values) != 1:
            raise ValueError("combined recommendations must have one served confidence value")
        confidence_value = confidence_values.pop()
    else:
        confidence_value = _decimal(confidence)

    candidate_kind, actions, label = _finalized_actions(
        finalized,
        currency=economics.currency,
        purchase_unit_cost=purchase_unit_cost,
    )
    return reconcile_candidate(
        frontier_id=frontier_id,
        tenant_id=first.tenant_id,
        pn=first.part_number,
        location=first.current_location,
        decision_key=decision_key,
        member_keys=member_keys,
        candidate_kind=candidate_kind,
        label=label,
        is_no_change=False,
        model_identity=model_identity,
        current_levels=current_levels,
        target_levels=target_levels,
        actions=actions,
        available_before=_decimal(trace.dispatchable_available),
        expected_receipts_before=_decimal(trace.expected_receipts_due),
        projected_demand=_decimal(trace.projected_demand),
        economics=economics,
        expected_aog_risk=expected_aog_risk,
        confidence=confidence_value,
        constraints=_constraint_evidence(finalized),
        evidence=_candidate_evidence(
            finalized,
            snapshot_hash=first.input_snapshot_hash,
        ),
        infeasibility_reasons=infeasibility_reasons,
    )


def no_change_from_finalized_recommendation(
    *,
    frontier_id: str,
    recommendation: Recommendation,
    model_identity: ModelIdentity,
    current_levels: CandidateTargetLevels,
    economics: LifecycleEconomics,
    expected_aog_risk: Decimal | int | str,
    constraints: Iterable[ConstraintEvidence] = (),
    confidence: Decimal | int | str | None = None,
    infeasibility_reasons: Iterable[str] = (),
) -> PolicyCandidate:
    """Build the no-change baseline from an exact served calculation trace.

    Recommendation action constraints are intentionally not copied to no-change.
    Callers supply any hard constraints that apply to the current policy itself.
    """

    first, trace, decision_key, member_keys = _trace_and_key((recommendation,))
    if economics.horizon_days != trace.horizon_days:
        raise ValueError("candidate economics horizon must match the exact calculation trace")
    return build_no_change_candidate(
        frontier_id=frontier_id,
        tenant_id=first.tenant_id,
        pn=first.part_number,
        location=first.current_location,
        decision_key=decision_key,
        member_keys=member_keys,
        model_identity=model_identity,
        current_levels=current_levels,
        available_before=_decimal(trace.dispatchable_available),
        expected_receipts_before=_decimal(trace.expected_receipts_due),
        projected_demand=_decimal(trace.projected_demand),
        economics=economics,
        expected_aog_risk=expected_aog_risk,
        confidence=(
            _decimal(first.confidence_score) if confidence is None else _decimal(confidence)
        ),
        constraints=constraints,
        evidence=_candidate_evidence(
            (recommendation,),
            snapshot_hash=first.input_snapshot_hash,
        ),
        infeasibility_reasons=infeasibility_reasons,
    )


def no_change_from_calculation_evidence(
    *,
    frontier_id: str,
    tenant_id: str,
    pn: str,
    location: str,
    input_snapshot_hash: str,
    calculation_evidence: CalculationEvidence,
    model_identity: ModelIdentity,
    current_levels: CandidateTargetLevels,
    economics: LifecycleEconomics,
    expected_aog_risk: Decimal | int | str,
    confidence: Decimal | int | str,
    applied_policy_constraints: Iterable[AppliedConstraint] = (),
    infeasibility_reasons: Iterable[str] = (),
) -> PolicyCandidate:
    """Build no-change directly from the engine's exact served horizon.

    This path covers eligible keys for which no action recommendation fires.  It
    accepts only policy-scoped constraints, preventing action-specific limits from
    being misrepresented as constraints on the current state.
    """

    trace = calculation_evidence
    decision_key = f"{pn}@{location}"
    member_keys = tuple(sorted(f"{member.pn}@{member.location}" for member in trace.members))
    if decision_key not in member_keys:
        raise ValueError("calculation evidence must include the no-change decision key")
    if economics.horizon_days != trace.horizon_days:
        raise ValueError("candidate economics horizon must match the exact calculation trace")
    constraints = tuple(applied_policy_constraints)
    if any(constraint.scope != "policy" for constraint in constraints):
        raise ValueError("no-change accepts only policy-scoped applied constraints")
    return build_no_change_candidate(
        frontier_id=frontier_id,
        tenant_id=tenant_id,
        pn=pn,
        location=location,
        decision_key=decision_key,
        member_keys=member_keys,
        model_identity=model_identity,
        current_levels=current_levels,
        available_before=_decimal(trace.dispatchable_available),
        expected_receipts_before=_decimal(trace.expected_receipts_due),
        projected_demand=_decimal(trace.projected_demand),
        economics=economics,
        expected_aog_risk=expected_aog_risk,
        confidence=confidence,
        constraints=_constraint_evidence_from_applied(constraints),
        evidence=(
            CandidateEvidence(
                kind="planning_trace",
                source="recommendation.calculation_evidence",
                reference_id=input_snapshot_hash,
                detail="Exact demand, availability, and receipt operands used by no-change",
            ),
        ),
        infeasibility_reasons=infeasibility_reasons,
    )


__all__ = [
    "candidate_from_finalized_recommendations",
    "no_change_from_calculation_evidence",
    "no_change_from_finalized_recommendation",
]
