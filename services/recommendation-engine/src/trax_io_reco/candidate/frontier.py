"""Safe Pareto dominance for budget-independent candidate menus."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trax_io_reco.contracts.candidate import PolicyCandidate


@dataclass(frozen=True)
class _Dimensions:
    minimize: tuple[Decimal, ...]
    maximize: tuple[Decimal, ...]


@dataclass(frozen=True)
class PrunedCandidates:
    candidates: tuple[PolicyCandidate, ...]
    removed_candidate_ids: tuple[str, ...]

    @property
    def removed_count(self) -> int:
        return len(self.removed_candidate_ids)


def _optimizer_dimensions(candidate: PolicyCandidate) -> _Dimensions:
    """Every resource/objective dimension exposed to the planned optimizer.

    Acquisition cash is the hard resource.  Shortage and AOG risk are minimized;
    service is maximized; each lifecycle component is compared separately so a
    candidate is never removed based on an unsafe scalar-cost collapse.
    """

    costs = candidate.lifecycle_costs
    outcome = candidate.outcome
    return _Dimensions(
        minimize=(
            costs.acquisition_cash,
            candidate.reconciliation.purchase_quantity,
            candidate.reconciliation.transfer_in_quantity,
            candidate.reconciliation.outbound_quantity,
            candidate.reconciliation.action_quantity,
            costs.holding_cost,
            costs.ordering_cost,
            costs.shortage_cost,
            costs.other_cost,
            outcome.expected_shortage,
            outcome.expected_excess,
            outcome.expected_aog_risk,
        ),
        maximize=(outcome.expected_service_level, candidate.confidence),
    )


def dominates(left: PolicyCandidate, right: PolicyCandidate) -> bool:
    """Whether ``left`` safely Pareto-dominates ``right``.

    Only feasible choices for the same decision/currency are comparable.  At least
    one dimension must be strictly better, preserving equal alternatives and their
    deterministic tie-break meaning.
    """

    if left.decision_key != right.decision_key:
        raise ValueError("cannot compare candidates from different decision keys")
    if left.lifecycle_costs.currency != right.lifecycle_costs.currency:
        raise ValueError("cannot compare candidates in different currencies")
    if not left.feasible or not right.feasible:
        return False

    left_dims = _optimizer_dimensions(left)
    right_dims = _optimizer_dimensions(right)
    no_worse = all(
        left_value <= right_value
        for left_value, right_value in zip(
            left_dims.minimize,
            right_dims.minimize,
            strict=True,
        )
    ) and all(
        left_value >= right_value
        for left_value, right_value in zip(
            left_dims.maximize,
            right_dims.maximize,
            strict=True,
        )
    )
    strictly_better = any(
        left_value < right_value
        for left_value, right_value in zip(
            left_dims.minimize,
            right_dims.minimize,
            strict=True,
        )
    ) or any(
        left_value > right_value
        for left_value, right_value in zip(
            left_dims.maximize,
            right_dims.maximize,
            strict=True,
        )
    )
    return no_worse and strictly_better


def prune_dominated(candidates: tuple[PolicyCandidate, ...]) -> PrunedCandidates:
    """Remove only safely dominated feasible alternatives.

    The no-change baseline and every infeasible candidate are always retained for
    audit/explanation even when another choice has better numbers.
    """

    retained: list[PolicyCandidate] = []
    removed: list[str] = []
    for candidate in candidates:
        if candidate.is_no_change or not candidate.feasible:
            retained.append(candidate)
            continue
        if any(
            other.candidate_id != candidate.candidate_id and dominates(other, candidate)
            for other in candidates
            if other.feasible
        ):
            removed.append(candidate.candidate_id)
        else:
            retained.append(candidate)
    retained.sort(key=lambda candidate: (not candidate.is_no_change, candidate.candidate_id))
    return PrunedCandidates(
        candidates=tuple(retained),
        removed_candidate_ids=tuple(sorted(removed)),
    )


__all__ = ["PrunedCandidates", "dominates", "prune_dominated"]
