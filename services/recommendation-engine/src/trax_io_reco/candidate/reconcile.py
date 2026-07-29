"""Pure candidate construction from finalized actions and source arithmetic."""

from __future__ import annotations

import math
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from trax_io_reco.candidate.identity import candidate_identifier
from trax_io_reco.contracts.candidate import (
    CandidateActionLine,
    CandidateEvidence,
    CandidateKind,
    CandidateOutcome,
    CandidateReconciliation,
    CandidateTargetLevels,
    ConstraintEvidence,
    LifecycleCostComponents,
    LifecycleEconomics,
    ModelIdentity,
    PolicyCandidate,
)

_CENT = Decimal("0.01")
_ZERO = Decimal("0")
_ONE = Decimal("1")


def _decimal(value: Decimal | int | str) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ValueError("candidate arithmetic requires Decimal, integer, or decimal text")
    parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("candidate arithmetic requires finite decimals")
    return parsed


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _sorted_constraints(
    constraints: Iterable[ConstraintEvidence],
) -> tuple[ConstraintEvidence, ...]:
    return tuple(
        sorted(
            constraints,
            key=lambda item: (
                item.constraint_id,
                item.scope,
                item.source,
                item.value or "",
                item.detail or "",
            ),
        )
    )


def _sorted_evidence(evidence: Iterable[CandidateEvidence]) -> tuple[CandidateEvidence, ...]:
    return tuple(
        sorted(
            evidence,
            key=lambda item: (
                item.kind,
                item.source,
                item.reference_id or "",
                item.detail,
            ),
        )
    )


def _sorted_actions(actions: Iterable[CandidateActionLine]) -> tuple[CandidateActionLine, ...]:
    return tuple(
        sorted(
            actions,
            key=lambda item: (
                item.kind,
                item.line_id,
                item.source_location or "",
                item.destination_location or "",
                item.source_reference or "",
            ),
        )
    )


def _canonical_member_keys(member_keys: Iterable[str]) -> tuple[str, ...]:
    values = tuple(member_keys)
    if len(values) != len(set(values)):
        raise ValueError("member_keys must be unique")
    return tuple(sorted(values))


def _generated_hard_constraints(
    *,
    current: Iterable[ConstraintEvidence],
    target_levels: CandidateTargetLevels,
    available_before: Decimal,
    expected_receipts_before: Decimal,
    inbound_quantity: Decimal,
    outbound_quantity: Decimal,
) -> tuple[ConstraintEvidence, ...]:
    reserved_ids = {
        "reorder_point_floor",
        "maximum_stock_floor",
        "physical_outbound_limit",
    }
    constraints = [item for item in current if item.constraint_id not in reserved_ids]
    constraint_ids = {item.constraint_id for item in constraints}

    floor_checks = (
        (
            "reorder_point_floor",
            target_levels.rop >= target_levels.safety_stock,
            target_levels.rop == target_levels.safety_stock,
            f"{target_levels.rop}>={target_levels.safety_stock}",
            "target_levels.rop>=target_levels.safety_stock",
        ),
        (
            "maximum_stock_floor",
            target_levels.max_stock >= target_levels.rop + target_levels.eoq,
            target_levels.max_stock == target_levels.rop + target_levels.eoq,
            f"{target_levels.max_stock}>={target_levels.rop + target_levels.eoq}",
            "target_levels.max_stock>=target_levels.rop+target_levels.eoq",
        ),
    )
    for constraint_id, satisfied, binding, value, source in floor_checks:
        if constraint_id not in constraint_ids:
            constraints.append(
                ConstraintEvidence(
                    constraint_id=constraint_id,
                    source=source,
                    value=value,
                    scope="policy",
                    hard=True,
                    satisfied=satisfied,
                    binding=not satisfied or binding,
                )
            )

    if "physical_outbound_limit" not in constraint_ids:
        outbound_limit = available_before + expected_receipts_before + inbound_quantity
        constraints.append(
            ConstraintEvidence(
                constraint_id="physical_outbound_limit",
                source="candidate.reconciliation.available_plus_inbound",
                value=format(outbound_limit, "f"),
                scope="action",
                hard=True,
                satisfied=outbound_quantity <= outbound_limit,
                binding=outbound_quantity >= outbound_limit and outbound_quantity > 0,
            )
        )
    return _sorted_constraints(constraints)


def reconcile_candidate(
    *,
    frontier_id: str,
    tenant_id: str,
    pn: str,
    location: str,
    decision_key: str,
    member_keys: Iterable[str],
    candidate_kind: CandidateKind,
    label: str,
    is_no_change: bool,
    model_identity: ModelIdentity,
    current_levels: CandidateTargetLevels,
    target_levels: CandidateTargetLevels,
    actions: Iterable[CandidateActionLine],
    available_before: Decimal | int | str,
    expected_receipts_before: Decimal | int | str,
    projected_demand: Decimal | int | str,
    economics: LifecycleEconomics,
    expected_aog_risk: Decimal | int | str,
    confidence: Decimal | int | str,
    constraints: Iterable[ConstraintEvidence],
    evidence: Iterable[CandidateEvidence],
    infeasibility_reasons: Iterable[str] = (),
) -> PolicyCandidate:
    """Build one fully reconciled candidate from finalized action lines.

    No recommendation output or precomputed cost is accepted by this API.  Quantity,
    acquisition cash, lifecycle cost, net position, shortage, and service are derived
    again from action/source inputs after arbitration.
    """

    finalized_actions = _sorted_actions(actions)
    if not finalized_actions:
        raise ValueError("at least one finalized action is required")
    if any(action.currency != economics.currency for action in finalized_actions):
        raise ValueError("mixed or blank action currencies are not allowed")

    available_value = _decimal(available_before)
    receipts_value = _decimal(expected_receipts_before)
    demand_value = _decimal(projected_demand)
    if min(available_value, receipts_value, demand_value) < 0:
        raise ValueError("available, receipts, and projected demand must be non-negative")

    purchase_quantity = sum(
        (action.quantity for action in finalized_actions if action.kind == "purchase"),
        _ZERO,
    )
    transfer_in_quantity = sum(
        (action.quantity for action in finalized_actions if action.kind == "transfer_in"),
        _ZERO,
    )
    outbound_quantity = sum(
        (
            action.quantity
            for action in finalized_actions
            if action.kind in {"transfer_out", "reduce_stock", "sell"}
        ),
        _ZERO,
    )
    inbound_quantity = purchase_quantity + transfer_in_quantity
    action_quantity = sum(
        (action.quantity for action in finalized_actions if action.kind != "adjust_policy"),
        _ZERO,
    )
    ending_net = (
        available_value + receipts_value + inbound_quantity - outbound_quantity - demand_value
    )
    shortage = max(_ZERO, -ending_net)
    excess = max(_ZERO, ending_net)
    if demand_value == 0:
        service_level = _ONE
    else:
        service_level = max(_ZERO, min(_ONE, _ONE - (shortage / demand_value)))

    acquisition_cash = sum(
        (action.acquisition_cash for action in finalized_actions),
        _ZERO,
    )
    holding_cost = _money(
        excess
        * economics.inventory_unit_value
        * economics.annual_holding_rate
        * Decimal(economics.horizon_days)
        / Decimal(365)
    )
    purchase_orders = sum(
        action.kind == "purchase" and action.quantity > 0 for action in finalized_actions
    )
    ordering_cost = _money(economics.ordering_cost_per_purchase * Decimal(purchase_orders))
    shortage_cost = _money(shortage * economics.shortage_cost_per_unit)
    other_cost = _money(economics.other_cost)
    lifecycle_costs = LifecycleCostComponents(
        currency=economics.currency,
        acquisition_cash=acquisition_cash,
        holding_cost=holding_cost,
        ordering_cost=ordering_cost,
        shortage_cost=shortage_cost,
        other_cost=other_cost,
        total_lifecycle_cost=(
            acquisition_cash + holding_cost + ordering_cost + shortage_cost + other_cost
        ),
    )

    finalized_constraints = _generated_hard_constraints(
        current=constraints,
        target_levels=target_levels,
        available_before=available_value,
        expected_receipts_before=receipts_value,
        inbound_quantity=inbound_quantity,
        outbound_quantity=outbound_quantity,
    )
    reasons = tuple(sorted(set(infeasibility_reasons)))
    hard_failures = tuple(
        item.constraint_id for item in finalized_constraints if item.hard and not item.satisfied
    )
    feasible = not reasons and not hard_failures

    outcome = CandidateOutcome(
        projected_demand=demand_value,
        available_before=available_value,
        expected_receipts_before=receipts_value,
        inbound_quantity=inbound_quantity,
        outbound_quantity=outbound_quantity,
        ending_net_position=ending_net,
        expected_shortage=shortage,
        expected_excess=excess,
        expected_service_level=service_level,
        expected_aog_risk=_decimal(expected_aog_risk),
    )
    reconciliation = CandidateReconciliation(
        currency=economics.currency,
        available_before=available_value,
        expected_receipts_before=receipts_value,
        projected_demand=demand_value,
        transfer_in_quantity=transfer_in_quantity,
        purchase_quantity=purchase_quantity,
        outbound_quantity=outbound_quantity,
        total_inbound_quantity=inbound_quantity,
        action_quantity=action_quantity,
        ending_net_position=ending_net,
        expected_shortage=shortage,
        acquisition_cash=acquisition_cash,
    )
    payload = {
        "tenant_id": tenant_id,
        "pn": pn,
        "location": location,
        "decision_key": decision_key,
        "member_keys": _canonical_member_keys(member_keys),
        "candidate_kind": candidate_kind,
        "label": label,
        "is_no_change": is_no_change,
        "feasible": feasible,
        "infeasibility_reasons": reasons,
        "model_identity": model_identity,
        "current_levels": current_levels,
        "target_levels": target_levels,
        "actions": finalized_actions,
        "action_quantity": action_quantity,
        "lifecycle_costs": lifecycle_costs,
        "outcome": outcome,
        "confidence": _decimal(confidence),
        "constraints": finalized_constraints,
        "evidence": _sorted_evidence(evidence),
        "reconciliation": reconciliation,
    }
    return PolicyCandidate(
        candidate_id=candidate_identifier(frontier_id, payload),
        **payload,
    )


def build_no_change_candidate(
    *,
    frontier_id: str,
    tenant_id: str,
    pn: str,
    location: str,
    decision_key: str,
    member_keys: Iterable[str],
    model_identity: ModelIdentity,
    current_levels: CandidateTargetLevels,
    available_before: Decimal | int | str,
    expected_receipts_before: Decimal | int | str,
    projected_demand: Decimal | int | str,
    economics: LifecycleEconomics,
    expected_aog_risk: Decimal | int | str,
    confidence: Decimal | int | str,
    constraints: Iterable[ConstraintEvidence],
    evidence: Iterable[CandidateEvidence],
    infeasibility_reasons: Iterable[str] = (),
) -> PolicyCandidate:
    """Represent current state with zero acquisition cash but real consequences."""

    return reconcile_candidate(
        frontier_id=frontier_id,
        tenant_id=tenant_id,
        pn=pn,
        location=location,
        decision_key=decision_key,
        member_keys=member_keys,
        candidate_kind="no_change",
        label="No change",
        is_no_change=True,
        model_identity=model_identity,
        current_levels=current_levels,
        target_levels=current_levels,
        actions=(
            CandidateActionLine(
                line_id="no-change",
                kind="no_change",
                quantity=0,
                currency=economics.currency,
                unit_acquisition_cash=0,
            ),
        ),
        available_before=available_before,
        expected_receipts_before=expected_receipts_before,
        projected_demand=projected_demand,
        economics=economics,
        expected_aog_risk=expected_aog_risk,
        confidence=confidence,
        constraints=constraints,
        evidence=evidence,
        infeasibility_reasons=infeasibility_reasons,
    )


def build_transfer_purchase_candidate(
    *,
    frontier_id: str,
    tenant_id: str,
    pn: str,
    location: str,
    decision_key: str,
    member_keys: Iterable[str],
    model_identity: ModelIdentity,
    current_levels: CandidateTargetLevels,
    target_levels: CandidateTargetLevels,
    available_before: Decimal | int | str,
    expected_receipts_before: Decimal | int | str,
    projected_demand: Decimal | int | str,
    requested_transfer_quantity: Decimal | int | str,
    donor_available_quantity: Decimal | int | str,
    donor_location: str,
    minimum_order_quantity: int,
    purchase_unit_cost: Decimal | int | str,
    economics: LifecycleEconomics,
    expected_aog_risk: Decimal | int | str,
    confidence: Decimal | int | str,
    constraints: Iterable[ConstraintEvidence],
    evidence: Iterable[CandidateEvidence],
) -> PolicyCandidate:
    """Apply transfer-first arbitration and purchase only the MOQ-adjusted residual."""

    if minimum_order_quantity < 1:
        raise ValueError("minimum_order_quantity must be positive")
    available_value = _decimal(available_before)
    receipts_value = _decimal(expected_receipts_before)
    demand_value = _decimal(projected_demand)
    requested_transfer = _decimal(requested_transfer_quantity)
    donor_available = _decimal(donor_available_quantity)
    if min(requested_transfer, donor_available) < 0:
        raise ValueError("transfer quantities must be non-negative")

    shortage_before = max(_ZERO, demand_value - available_value - receipts_value)
    finalized_transfer = min(requested_transfer, donor_available, shortage_before)
    residual = max(_ZERO, shortage_before - finalized_transfer)
    rounded_residual = Decimal(math.ceil(residual))
    purchase_quantity = (
        max(Decimal(minimum_order_quantity), rounded_residual) if residual > 0 else _ZERO
    )

    actions: list[CandidateActionLine] = []
    if finalized_transfer > 0:
        actions.append(
            CandidateActionLine(
                line_id="transfer-in",
                kind="transfer_in",
                quantity=finalized_transfer,
                currency=economics.currency,
                unit_acquisition_cash=0,
                source_location=donor_location,
                destination_location=location,
            )
        )
    if purchase_quantity > 0:
        actions.append(
            CandidateActionLine(
                line_id="residual-purchase",
                kind="purchase",
                quantity=purchase_quantity,
                currency=economics.currency,
                unit_acquisition_cash=_decimal(purchase_unit_cost),
                destination_location=location,
            )
        )
    if not actions:
        raise ValueError("transfer/purchase candidate has no residual action")

    generated_constraints = [
        constraint
        for constraint in constraints
        if constraint.constraint_id not in {"donor_availability", "minimum_order_quantity"}
    ]
    generated_constraints.extend(
        (
            ConstraintEvidence(
                constraint_id="donor_availability",
                source="donor_stock.available",
                value=format(donor_available, "f"),
                scope="action",
                hard=True,
                satisfied=finalized_transfer <= donor_available,
                binding=finalized_transfer > 0 and finalized_transfer == donor_available,
            ),
            ConstraintEvidence(
                constraint_id="minimum_order_quantity",
                source="vendor_economics.minimum_order_quantity",
                value=str(minimum_order_quantity),
                scope="action",
                hard=True,
                satisfied=purchase_quantity == 0 or purchase_quantity >= minimum_order_quantity,
                binding=purchase_quantity > rounded_residual,
            ),
        )
    )
    kind: CandidateKind
    if finalized_transfer > 0 and purchase_quantity > 0:
        kind = "transfer_purchase"
    elif finalized_transfer > 0:
        kind = "transfer"
    else:
        kind = "purchase"
    return reconcile_candidate(
        frontier_id=frontier_id,
        tenant_id=tenant_id,
        pn=pn,
        location=location,
        decision_key=decision_key,
        member_keys=member_keys,
        candidate_kind=kind,
        label="Transfer first, purchase residual",
        is_no_change=False,
        model_identity=model_identity,
        current_levels=current_levels,
        target_levels=target_levels,
        actions=actions,
        available_before=available_value,
        expected_receipts_before=receipts_value,
        projected_demand=demand_value,
        economics=economics,
        expected_aog_risk=expected_aog_risk,
        confidence=confidence,
        constraints=generated_constraints,
        evidence=evidence,
    )


__all__ = [
    "build_no_change_candidate",
    "build_transfer_purchase_candidate",
    "reconcile_candidate",
]
