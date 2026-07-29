"""Pure facade for immutable, explainable advisory planning-run outcomes."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

from trax_io_reco.contracts.candidate import CandidateEvidence, PolicyCandidate
from trax_io_reco.contracts.planning import (
    LOW_SELECTED_CONFIDENCE_THRESHOLD,
    MandatoryFloor,
    PortfolioConfidenceSummary,
    PortfolioKeyMenu,
    PortfolioSelection,
    PortfolioSolveRequest,
    PortfolioSolveResult,
)
from trax_io_reco.contracts.planning_run import (
    CandidateChoiceSnapshot,
    PlanningAssumptionChange,
    PlanningRunOutcome,
    PlanningSelectionDetail,
    PlanningWarning,
    RejectedAlternative,
)
from trax_io_reco.portfolio.identity import (
    planning_fingerprint,
    planning_menus_fingerprint,
)
from trax_io_reco.portfolio.optimizer import (
    floor_states,
    objective_contribution,
)

_DIFF_FIELDS = (
    "source_snapshot_hash",
    "horizon_days",
    "currency",
    "budget",
    "objective_weights",
    "tenant_policy_version",
    "forecast_version",
    "repair_model_version",
    "candidate_planner_version",
    "optimizer_version",
    "time_limit_seconds",
)


def _render(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def planning_assumption_diff(
    parent: PortfolioSolveRequest,
    rerun: PortfolioSolveRequest,
) -> tuple[PlanningAssumptionChange, ...]:
    """Return stable material changes; unchanged reruns have no diff."""

    if parent.tenant_id != rerun.tenant_id:
        raise ValueError("planning reruns cannot cross tenants")
    changes = []
    for field_name in _DIFF_FIELDS:
        before = getattr(parent, field_name)
        after = getattr(rerun, field_name)
        if before != after:
            changes.append(
                PlanningAssumptionChange(
                    field=field_name,
                    before=_render(before),
                    after=_render(after),
                )
            )
    if parent.menus != rerun.menus:
        changes.append(
            PlanningAssumptionChange(
                field="menus",
                before=_render(
                    {
                        "menus_fingerprint": planning_menus_fingerprint(parent),
                        "menu_count": len(parent.menus),
                    }
                ),
                after=_render(
                    {
                        "menus_fingerprint": planning_menus_fingerprint(rerun),
                        "menu_count": len(rerun.menus),
                    }
                ),
            )
        )
    return tuple(changes)


def _hard_constraint_ids(candidate: PolicyCandidate) -> tuple[str, ...]:
    return tuple(
        sorted(
            constraint.constraint_id
            for constraint in candidate.constraints
            if constraint.hard and not constraint.satisfied
        )
    )


def _unsatisfied_floor_ids(
    candidate: PolicyCandidate,
    floors: tuple[MandatoryFloor, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            floor.floor_id for floor in floors if not floor.satisfied_by(candidate)
        )
    )


def _choice(
    *,
    request: PortfolioSolveRequest,
    menu: PortfolioKeyMenu,
    baseline: PolicyCandidate,
    candidate: PolicyCandidate,
) -> CandidateChoiceSnapshot:
    return CandidateChoiceSnapshot(
        candidate_id=candidate.candidate_id,
        label=candidate.label,
        candidate_kind=candidate.candidate_kind,
        acquisition_cash=candidate.lifecycle_costs.acquisition_cash,
        expected_shortage=candidate.outcome.expected_shortage,
        expected_service_level=candidate.outcome.expected_service_level,
        expected_aog_risk=candidate.outcome.expected_aog_risk,
        objective=objective_contribution(
            request=request,
            menu=menu,
            baseline=baseline,
            candidate=candidate,
        ),
        confidence=candidate.confidence,
        feasible=candidate.feasible,
        infeasibility_reasons=candidate.infeasibility_reasons,
        hard_constraint_ids=_hard_constraint_ids(candidate),
        mandatory_floor_ids=_unsatisfied_floor_ids(
            candidate,
            menu.mandatory_floors,
        ),
    )


def _rejection(
    *,
    choice: CandidateChoiceSnapshot,
    replacement_spend: Decimal,
    budget: Decimal,
    selected_objective: Decimal,
) -> RejectedAlternative:
    if choice.hard_constraint_ids:
        joined = ", ".join(choice.hard_constraint_ids)
        return RejectedAlternative(
            candidate=choice,
            reason_code="hard_constraint",
            reason=f"Rejected because hard constraint(s) {joined} are not satisfied.",
        )
    if choice.mandatory_floor_ids:
        joined = ", ".join(choice.mandatory_floor_ids)
        return RejectedAlternative(
            candidate=choice,
            reason_code="mandatory_floor",
            reason=f"Rejected because mandatory floor(s) {joined} are not satisfied.",
        )
    if not choice.feasible:
        detail = ", ".join(choice.infeasibility_reasons) or "candidate infeasible"
        return RejectedAlternative(
            candidate=choice,
            reason_code="candidate_infeasible",
            reason=f"Rejected because the candidate is infeasible: {detail}.",
        )
    if replacement_spend > budget:
        shortfall = replacement_spend - budget
        return RejectedAlternative(
            candidate=choice,
            reason_code="budget",
            reason=(
                "Replacing the selected choice would exceed the hard acquisition "
                f"budget by {shortfall}."
            ),
        )
    if choice.objective.total < selected_objective:
        return RejectedAlternative(
            candidate=choice,
            reason_code="lower_objective",
            reason=(
                "This feasible replacement fits the budget but contributes a "
                "lower objective value for this key."
            ),
        )
    return RejectedAlternative(
        candidate=choice,
        reason_code="portfolio_tradeoff",
        reason=(
            "This candidate is individually feasible, but the complete portfolio "
            "combination produced the preferred objective and tie-break result."
        ),
    )


def _selected_reason(
    *,
    selected: CandidateChoiceSnapshot,
    budget_slack: Decimal,
    evidence: tuple[CandidateEvidence, ...],
) -> str:
    evidence_labels = tuple(
        sorted(
            {
                f"{item.kind} evidence from {item.source}"
                for item in evidence
            }
        )
    )
    evidence_preview = ", ".join(evidence_labels[:3])
    if len(evidence_labels) > 3:
        evidence_preview = (
            f"{evidence_preview}, plus "
            f"{len(evidence_labels) - 3} additional evidence source(s)"
        )
    evidence_text = (
        f" The selection carries confidence {selected.confidence} and is "
        f"supported by {evidence_preview}."
    )
    floor_text = (
        " All mandatory floors are satisfied."
        if not selected.mandatory_floor_ids
        else ""
    )
    if selected.candidate_kind == "no_change":
        return (
            "No change was selected for this key within the optimized portfolio; "
            f"its objective contribution is {selected.objective.total} and "
            f"portfolio budget slack is {budget_slack}.{floor_text}"
            f"{evidence_text}"
        )
    return (
        f"Selected {selected.label} with acquisition cash "
        f"{selected.acquisition_cash}, objective contribution "
        f"{selected.objective.total}, and portfolio budget slack "
        f"{budget_slack}.{floor_text}{evidence_text}"
    )


@dataclass(frozen=True)
class _SelectionCandidates:
    menu: PortfolioKeyMenu
    baseline: PolicyCandidate
    selected: PolicyCandidate


def _selection_candidates(
    *,
    request: PortfolioSolveRequest,
    menus: dict[str, PortfolioKeyMenu],
    selection: PortfolioSelection,
) -> _SelectionCandidates:
    menu = menus[selection.decision_key]
    candidates = {
        candidate.candidate_id: candidate
        for candidate in menu.frontier.candidates
    }
    baseline = candidates[selection.current_candidate_id]
    selected = candidates[selection.selected_candidate_id]
    expected_objective = objective_contribution(
        request=request,
        menu=menu,
        baseline=baseline,
        candidate=selected,
    )
    if (
        selection.tenant_id != request.tenant_id
        or selection.current_candidate_id != baseline.candidate_id
        or selection.selected_is_no_change != selected.is_no_change
        or selection.acquisition_cash
        != selected.lifecycle_costs.acquisition_cash
        or selection.expected_shortage != selected.outcome.expected_shortage
        or selection.expected_service_level
        != selected.outcome.expected_service_level
        or selection.expected_aog_risk != selected.outcome.expected_aog_risk
        or selection.objective != expected_objective
        or selection.floor_states != floor_states(menu, selected)
    ):
        raise ValueError(
            "solver selection does not reconcile to candidate frontier: "
            f"{selection.decision_key}"
        )
    return _SelectionCandidates(
        menu=menu,
        baseline=baseline,
        selected=selected,
    )


def enrich_planning_result_summary(
    *,
    request: PortfolioSolveRequest,
    result: PortfolioSolveResult,
    warnings: tuple[PlanningWarning, ...] = (),
) -> PortfolioSolveResult:
    """Add exact warning/confidence aggregates without building row details."""

    expected_fingerprint = planning_fingerprint(request)
    if result.planning_fingerprint != expected_fingerprint:
        raise ValueError("solver result does not belong to the planning request")
    if result.tenant_id != request.tenant_id:
        raise ValueError("solver result tenant does not match planning request")
    if result.status != "completed":
        return result

    assert result.summary is not None
    menus = {
        menu.frontier.decision_key: menu
        for menu in request.menus
    }
    selected_confidences: list[Decimal] = []
    for selection in result.selections:
        candidates = _selection_candidates(
            request=request,
            menus=menus,
            selection=selection,
        )
        selected_confidences.append(candidates.selected.confidence)
    confidence_summary = PortfolioConfidenceSummary(
        selected_confidence_total=sum(
            selected_confidences,
            Decimal("0"),
        ),
        minimum_selected_confidence=min(selected_confidences),
        low_confidence_key_count=sum(
            confidence < LOW_SELECTED_CONFIDENCE_THRESHOLD
            for confidence in selected_confidences
        ),
    )
    enriched_summary = result.summary.model_copy(
        update={
            "warning_count": sum(warning.count for warning in warnings),
            "confidence_summary": confidence_summary,
        }
    )
    return result.model_copy(update={"summary": enriched_summary})


def iter_planning_selection_details(
    *,
    request: PortfolioSolveRequest,
    result: PortfolioSolveResult,
    rejected_limit: int = 3,
) -> Iterator[PlanningSelectionDetail]:
    """Yield reconciled explanation rows without retaining the full ledger."""

    if rejected_limit < 0:
        raise ValueError("rejected_limit must be non-negative")
    if result.status != "completed":
        return
    assert result.summary is not None
    menus = {
        menu.frontier.decision_key: menu
        for menu in request.menus
    }
    minimum_spend_by_key = {
        menu.frontier.decision_key: min(
            candidate.lifecycle_costs.acquisition_cash
            for candidate in menu.frontier.candidates
            if (
                candidate.feasible
                and all(
                    not constraint.hard or constraint.satisfied
                    for constraint in candidate.constraints
                )
                and all(
                    floor.satisfied_by(candidate)
                    for floor in menu.mandatory_floors
                )
            )
        )
        for menu in request.menus
    }
    total_minimum_spend = sum(
        minimum_spend_by_key.values(),
        Decimal("0"),
    )
    for selection in result.selections:
        selected = _selection_candidates(
            request=request,
            menus=menus,
            selection=selection,
        )
        current_choice = _choice(
            request=request,
            menu=selected.menu,
            baseline=selected.baseline,
            candidate=selected.baseline,
        )
        selected_choice = _choice(
            request=request,
            menu=selected.menu,
            baseline=selected.baseline,
            candidate=selected.selected,
        )
        alternatives = []
        for candidate in selected.menu.frontier.candidates:
            if candidate.candidate_id in {
                selected.baseline.candidate_id,
                selected.selected.candidate_id,
            }:
                continue
            choice = _choice(
                request=request,
                menu=selected.menu,
                baseline=selected.baseline,
                candidate=candidate,
            )
            replacement_spend = (
                total_minimum_spend
                - minimum_spend_by_key[selection.decision_key]
                + choice.acquisition_cash
            )
            alternatives.append(
                _rejection(
                    choice=choice,
                    replacement_spend=replacement_spend,
                    budget=request.budget,
                    selected_objective=selected_choice.objective.total,
                )
            )
        alternatives.sort(
            key=lambda alternative: (
                -alternative.candidate.objective.total,
                alternative.candidate.acquisition_cash,
                alternative.candidate.candidate_id,
            )
        )
        yield PlanningSelectionDetail(
            decision_key=selection.decision_key,
            current=current_choice,
            selected=selected_choice,
            selected_reason=_selected_reason(
                selected=selected_choice,
                budget_slack=result.summary.budget_slack,
                evidence=selected.selected.evidence,
            ),
            rejected_alternatives=tuple(alternatives[:rejected_limit]),
        )


def build_planning_run_outcome(
    *,
    run_id: str,
    request: PortfolioSolveRequest,
    result: PortfolioSolveResult,
    parent_run_id: str | None = None,
    parent_request: PortfolioSolveRequest | None = None,
    warnings: tuple[PlanningWarning, ...] = (),
    rejected_limit: int = 3,
) -> PlanningRunOutcome:
    """Build a fully reconciled terminal outcome without I/O or mutable state."""

    if rejected_limit < 0:
        raise ValueError("rejected_limit must be non-negative")
    expected_fingerprint = planning_fingerprint(request)
    if result.planning_fingerprint != expected_fingerprint:
        raise ValueError("solver result does not belong to the planning request")
    if result.tenant_id != request.tenant_id:
        raise ValueError("solver result tenant does not match planning request")
    if (parent_run_id is None) != (parent_request is None):
        raise ValueError("parent_run_id and parent_request must be supplied together")

    result = enrich_planning_result_summary(
        request=request,
        result=result,
        warnings=warnings,
    )
    details = tuple(
        iter_planning_selection_details(
            request=request,
            result=result,
            rejected_limit=rejected_limit,
        )
    )

    assumption_diff = (
        planning_assumption_diff(parent_request, request)
        if parent_request is not None
        else ()
    )
    return PlanningRunOutcome(
        run_id=run_id,
        parent_run_id=parent_run_id,
        parent_planning_fingerprint=(
            planning_fingerprint(parent_request)
            if parent_request is not None
            else None
        ),
        parent_source_snapshot_hash=(
            parent_request.source_snapshot_hash
            if parent_request is not None
            else None
        ),
        planning_fingerprint=expected_fingerprint,
        tenant_id=request.tenant_id,
        source_snapshot_hash=request.source_snapshot_hash,
        currency=request.currency,
        status=result.status,
        request=request,
        result=result,
        selection_details=details,
        assumption_diff=assumption_diff,
        warnings=warnings,
    )


__all__ = [
    "build_planning_run_outcome",
    "enrich_planning_result_summary",
    "iter_planning_selection_details",
    "planning_assumption_diff",
]
