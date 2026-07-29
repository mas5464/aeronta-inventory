"""Deterministic hard-budget multiple-choice portfolio optimizer."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import scipy
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csr_matrix

from trax_io_reco.contracts.candidate import PolicyCandidate
from trax_io_reco.contracts.planning import (
    FloorState,
    MandatoryFloor,
    ObjectiveContribution,
    PortfolioKeyMenu,
    PortfolioSelection,
    PortfolioSolveRequest,
    PortfolioSolveResult,
    PortfolioSummary,
    SolverEvidence,
)
from trax_io_reco.portfolio.identity import planning_fingerprint

_ZERO = Decimal("0")
_FLOAT_TOLERANCE = 1e-8
_INTEGRALITY_TOLERANCE = 1e-6


@dataclass(frozen=True)
class _Variable:
    menu: PortfolioKeyMenu
    candidate: PolicyCandidate
    baseline: PolicyCandidate
    objective: ObjectiveContribution
    spend: Decimal


def _decimal_from_float(value: float | int | None) -> Decimal | None:
    if value is None:
        return None
    parsed = float(value)
    if not math.isfinite(parsed):
        return None
    return Decimal(str(round(parsed, 12)))


def _eligible(candidate: PolicyCandidate, floors: tuple[MandatoryFloor, ...]) -> bool:
    return (
        candidate.feasible
        and all(
            not constraint.hard or constraint.satisfied
            for constraint in candidate.constraints
        )
        and all(floor.satisfied_by(candidate) for floor in floors)
    )


def objective_contribution(
    *,
    request: PortfolioSolveRequest,
    menu: PortfolioKeyMenu,
    baseline: PolicyCandidate,
    candidate: PolicyCandidate,
) -> ObjectiveContribution:
    weights = request.objective_weights
    criticality = weights.criticality_weights[menu.criticality_tier]
    shortage_reduction = (
        baseline.outcome.expected_shortage - candidate.outcome.expected_shortage
    )
    aog_reduction = (
        baseline.outcome.expected_aog_risk - candidate.outcome.expected_aog_risk
    )
    holding_increment = (
        candidate.lifecycle_costs.holding_cost
        - baseline.lifecycle_costs.holding_cost
    )
    ordering_increment = (
        candidate.lifecycle_costs.ordering_cost
        - baseline.lifecycle_costs.ordering_cost
    )
    shortage_value = (
        criticality * weights.shortage_reduction_weight * shortage_reduction
    )
    aog_value = criticality * weights.aog_risk_reduction_weight * aog_reduction
    holding_penalty = weights.holding_cost_penalty_weight * holding_increment
    ordering_penalty = weights.ordering_cost_penalty_weight * ordering_increment
    return ObjectiveContribution(
        currency=request.currency,
        criticality_weight=criticality,
        shortage_reduction=shortage_reduction,
        aog_risk_reduction=aog_reduction,
        incremental_holding_cost=holding_increment,
        incremental_ordering_cost=ordering_increment,
        shortage_value=shortage_value,
        aog_value=aog_value,
        holding_penalty=holding_penalty,
        ordering_penalty=ordering_penalty,
        total=shortage_value + aog_value - holding_penalty - ordering_penalty,
    )


def _solver_evidence(
    *,
    termination: str,
    duration_ms: Decimal,
    message: str,
    objective: Decimal | None = None,
    objective_bound: Decimal | None = None,
    gap: Decimal | None = None,
    node_count: int | None = None,
) -> SolverEvidence:
    return SolverEvidence(
        implementation="scipy.optimize.milp/highs",
        implementation_version=scipy.__version__,
        termination=termination,
        optimality_proven=termination == "optimal",
        objective=objective,
        objective_bound=objective_bound,
        relative_gap=gap,
        duration_ms=duration_ms,
        node_count=node_count,
        message=message,
    )


def floor_states(
    menu: PortfolioKeyMenu,
    candidate: PolicyCandidate,
) -> tuple[FloorState, ...]:
    return tuple(
        FloorState(
            floor_id=floor.floor_id,
            source=floor.source,
            satisfied=floor.satisfied_by(candidate),
            binding=(
                (
                    floor.min_service_level is not None
                    and candidate.outcome.expected_service_level
                    == floor.min_service_level
                )
                or (
                    floor.max_expected_shortage is not None
                    and candidate.outcome.expected_shortage
                    == floor.max_expected_shortage
                )
                or (
                    floor.max_aog_risk is not None
                    and candidate.outcome.expected_aog_risk
                    == floor.max_aog_risk
                )
            ),
            detail=floor.detail,
        )
        for floor in menu.mandatory_floors
    )


class PortfolioOptimizer:
    """Select exactly one feasible candidate per key under a hard cash budget."""

    def solve(self, request: PortfolioSolveRequest) -> PortfolioSolveResult:
        started = time.perf_counter()
        fingerprint = planning_fingerprint(request)
        variables: list[_Variable] = []
        ranges: list[tuple[int, int]] = []
        infeasible_keys: list[str] = []
        infeasible_floors: set[str] = set()
        minimum_budget = _ZERO

        for menu in request.menus:
            baseline = next(
                candidate
                for candidate in menu.frontier.candidates
                if candidate.is_no_change
            )
            eligible = [
                candidate
                for candidate in menu.frontier.candidates
                if _eligible(candidate, menu.mandatory_floors)
            ]
            if not eligible:
                infeasible_keys.append(menu.frontier.decision_key)
                infeasible_floors.update(
                    floor.floor_id
                    for floor in menu.mandatory_floors
                    if not any(
                        candidate.feasible and floor.satisfied_by(candidate)
                        for candidate in menu.frontier.candidates
                    )
                )
                continue
            start = len(variables)
            for candidate in sorted(eligible, key=lambda item: item.candidate_id):
                variables.append(
                    _Variable(
                        menu=menu,
                        candidate=candidate,
                        baseline=baseline,
                        objective=objective_contribution(
                            request=request,
                            menu=menu,
                            baseline=baseline,
                            candidate=candidate,
                        ),
                        spend=candidate.lifecycle_costs.acquisition_cash,
                    )
                )
            ranges.append((start, len(variables)))
            minimum_budget += min(
                candidate.lifecycle_costs.acquisition_cash
                for candidate in eligible
            )

        def elapsed() -> Decimal:
            return Decimal(
                str(round((time.perf_counter() - started) * 1000, 6))
            )
        if infeasible_keys:
            return PortfolioSolveResult(
                planning_fingerprint=fingerprint,
                tenant_id=request.tenant_id,
                status="infeasible",
                solver=_solver_evidence(
                    termination="infeasible",
                    duration_ms=elapsed(),
                    message="One or more keys have no candidate satisfying mandatory floors.",
                ),
                minimum_budget_required=minimum_budget,
                budget_shortfall=max(_ZERO, minimum_budget - request.budget),
                infeasible_keys=tuple(sorted(infeasible_keys)),
                infeasible_floor_ids=tuple(sorted(infeasible_floors)),
            )
        if minimum_budget > request.budget:
            return PortfolioSolveResult(
                planning_fingerprint=fingerprint,
                tenant_id=request.tenant_id,
                status="infeasible",
                solver=_solver_evidence(
                    termination="infeasible",
                    duration_ms=elapsed(),
                    message="Budget is below the minimum feasible mandatory-floor spend.",
                ),
                minimum_budget_required=minimum_budget,
                budget_shortfall=minimum_budget - request.budget,
            )

        size = len(variables)
        objective_coefficients = np.array(
            [float(variable.objective.total) for variable in variables],
            dtype=float,
        )
        spend_coefficients = np.array(
            [float(variable.spend) for variable in variables],
            dtype=float,
        )
        # One nonzero per candidate. A dense key-by-candidate matrix would
        # require tens of gigabytes at the 59K-key launch workload.
        one_per_key = csr_matrix(
            (
                np.ones(size, dtype=float),
                (
                    np.repeat(
                        np.arange(len(ranges), dtype=int),
                        [end - start for start, end in ranges],
                    ),
                    np.arange(size, dtype=int),
                ),
            ),
            shape=(len(ranges), size),
        )
        constraints: list[LinearConstraint] = [
            LinearConstraint(
                one_per_key,
                np.ones(len(ranges)),
                np.ones(len(ranges)),
            ),
            LinearConstraint(
                spend_coefficients,
                -np.inf,
                float(request.budget),
            ),
        ]
        def solver_options() -> dict[str, float | bool]:
            remaining = request.time_limit_seconds - (
                time.perf_counter() - started
            )
            return {
                "time_limit": max(1e-6, remaining),
                "presolve": True,
            }

        primary = milp(
            c=-objective_coefficients,
            integrality=np.ones(size),
            bounds=Bounds(np.zeros(size), np.ones(size)),
            constraints=constraints,
            options=solver_options(),
        )
        incumbent = primary
        tie_break_proven = False
        if primary.x is not None and primary.status == 0:
            primary_choice = (np.asarray(primary.x, dtype=float) >= 0.5).astype(
                float
            )
            best_objective = float(
                np.dot(objective_coefficients, primary_choice)
            )
            objective_tolerance = max(
                _FLOAT_TOLERANCE,
                abs(best_objective) * 1e-9,
            )
            objective_floor = LinearConstraint(
                objective_coefficients,
                best_objective - objective_tolerance,
                np.inf,
            )
            if time.perf_counter() - started < request.time_limit_seconds:
                secondary = milp(
                    c=spend_coefficients,
                    integrality=np.ones(size),
                    bounds=Bounds(np.zeros(size), np.ones(size)),
                    constraints=[*constraints, objective_floor],
                    options=solver_options(),
                )
                if secondary.x is not None and secondary.status == 0:
                    incumbent = secondary
                    secondary_choice = (
                        np.asarray(secondary.x, dtype=float) >= 0.5
                    ).astype(float)
                    best_spend = float(
                        np.dot(spend_coefficients, secondary_choice)
                    )
                    spend_ceiling = LinearConstraint(
                        spend_coefficients,
                        -np.inf,
                        best_spend + _FLOAT_TOLERANCE,
                    )
                    if (
                        time.perf_counter() - started
                        < request.time_limit_seconds
                    ):
                        # A summed rank is deterministic but not a total
                        # lexicographic order: two multi-key portfolios can
                        # have the same rank sum. Prove the stable
                        # key/candidate ordering one key at a time instead.
                        # Bounds fix each proven prefix choice, so we avoid an
                        # ever-growing dense constraint matrix. At full-network
                        # scale the configured time limit can stop this proof;
                        # the feasible incumbent is then explicitly
                        # ``not_proven`` rather than mislabeled exact.
                        lexicographic_lower = np.zeros(size)
                        lexicographic_upper = np.ones(size)
                        lexicographic_complete = True
                        lexicographic_constraints = [
                            *constraints,
                            objective_floor,
                            spend_ceiling,
                        ]
                        for start, end in ranges:
                            if end - start <= 1:
                                continue
                            if (
                                time.perf_counter() - started
                                >= request.time_limit_seconds
                            ):
                                lexicographic_complete = False
                                break
                            local_ranks = np.zeros(size)
                            local_ranks[start:end] = np.arange(
                                end - start,
                                dtype=float,
                            )
                            lexicographic = milp(
                                c=local_ranks,
                                integrality=np.ones(size),
                                bounds=Bounds(
                                    lexicographic_lower,
                                    lexicographic_upper,
                                ),
                                constraints=lexicographic_constraints,
                                options=solver_options(),
                            )
                            if (
                                lexicographic.x is None
                                or lexicographic.status != 0
                            ):
                                lexicographic_complete = False
                                break
                            incumbent = lexicographic
                            chosen = np.flatnonzero(
                                np.asarray(
                                    lexicographic.x[start:end],
                                    dtype=float,
                                )
                                >= 0.5
                            )
                            if len(chosen) != 1:
                                lexicographic_complete = False
                                break
                            chosen_index = start + int(chosen[0])
                            lexicographic_lower[chosen_index] = 1.0
                            lexicographic_upper[chosen_index] = 1.0
                        tie_break_proven = lexicographic_complete

        if incumbent.x is None:
            termination = "infeasible" if primary.status == 2 else "failed"
            status = "infeasible" if termination == "infeasible" else "failed"
            return PortfolioSolveResult(
                planning_fingerprint=fingerprint,
                tenant_id=request.tenant_id,
                status=status,
                solver=_solver_evidence(
                    termination=termination,
                    duration_ms=elapsed(),
                    message=str(primary.message or "Solver returned no feasible incumbent."),
                ),
                minimum_budget_required=minimum_budget if status == "infeasible" else None,
                budget_shortfall=(
                    max(_ZERO, minimum_budget - request.budget)
                    if status == "infeasible"
                    else None
                ),
            )

        incumbent_vector = np.asarray(incumbent.x, dtype=float)
        incumbent_spend = float(np.dot(spend_coefficients, incumbent_vector))
        exactly_one = all(
            abs(float(np.sum(incumbent_vector[start:end])) - 1.0)
            <= _INTEGRALITY_TOLERANCE
            for start, end in ranges
        )
        binary = all(
            min(abs(value), abs(value - 1.0)) <= _INTEGRALITY_TOLERANCE
            for value in incumbent_vector
        )
        if (
            not exactly_one
            or not binary
            or incumbent_spend > float(request.budget) + _FLOAT_TOLERANCE
        ):
            return PortfolioSolveResult(
                planning_fingerprint=fingerprint,
                tenant_id=request.tenant_id,
                status="failed",
                solver=_solver_evidence(
                    termination="failed",
                    duration_ms=elapsed(),
                    message="Solver returned an incumbent that violates hard constraints.",
                ),
            )

        selected_variables = [
            variable
            for variable, chosen in zip(variables, incumbent_vector, strict=True)
            if chosen >= 0.5
        ]
        if len(selected_variables) != len(request.menus):
            return PortfolioSolveResult(
                planning_fingerprint=fingerprint,
                tenant_id=request.tenant_id,
                status="failed",
                solver=_solver_evidence(
                    termination="failed",
                    duration_ms=elapsed(),
                    message="Solver incumbent violated the exactly-one candidate contract.",
                ),
            )

        selections = tuple(
            PortfolioSelection(
                tenant_id=request.tenant_id,
                decision_key=variable.menu.frontier.decision_key,
                current_candidate_id=variable.baseline.candidate_id,
                selected_candidate_id=variable.candidate.candidate_id,
                selected_is_no_change=variable.candidate.is_no_change,
                acquisition_cash=variable.spend,
                expected_shortage=variable.candidate.outcome.expected_shortage,
                expected_service_level=variable.candidate.outcome.expected_service_level,
                expected_aog_risk=variable.candidate.outcome.expected_aog_risk,
                objective=variable.objective,
                floor_states=floor_states(variable.menu, variable.candidate),
            )
            for variable in sorted(
                selected_variables,
                key=lambda item: item.menu.frontier.decision_key,
            )
        )
        selected_spend = sum(
            (selection.acquisition_cash for selection in selections),
            _ZERO,
        )
        if selected_spend > request.budget:
            return PortfolioSolveResult(
                planning_fingerprint=fingerprint,
                tenant_id=request.tenant_id,
                status="failed",
                solver=_solver_evidence(
                    termination="failed",
                    duration_ms=elapsed(),
                    message=(
                        "Rounded solver selection exceeds the exact Decimal "
                        "acquisition budget."
                    ),
                ),
            )
        selected_objective = sum(
            (selection.objective.total for selection in selections),
            _ZERO,
        )
        shortage = sum(
            (selection.expected_shortage for selection in selections),
            _ZERO,
        )
        average_service = sum(
            (selection.expected_service_level for selection in selections),
            _ZERO,
        ) / Decimal(len(selections))
        maximum_aog = max(
            (selection.expected_aog_risk for selection in selections),
            default=_ZERO,
        )
        primary_optimal = primary.status == 0 and tie_break_proven
        dual_bound = _decimal_from_float(
            -primary.mip_dual_bound
            if getattr(primary, "mip_dual_bound", None) is not None
            else None
        )
        gap = _decimal_from_float(getattr(primary, "mip_gap", None))
        node_count_value = getattr(primary, "mip_node_count", None)
        node_count = (
            int(node_count_value)
            if node_count_value is not None and math.isfinite(float(node_count_value))
            else None
        )
        return PortfolioSolveResult(
            planning_fingerprint=fingerprint,
            tenant_id=request.tenant_id,
            status="completed",
            selections=selections,
            summary=PortfolioSummary(
                currency=request.currency,
                budget=request.budget,
                selected_acquisition_cash=selected_spend,
                budget_slack=request.budget - selected_spend,
                selected_key_count=len(selections),
                no_change_key_count=sum(
                    selection.selected_is_no_change for selection in selections
                ),
                selected_objective=selected_objective,
                expected_shortage=shortage,
                average_service_level=average_service,
                maximum_aog_risk=maximum_aog,
            ),
            solver=_solver_evidence(
                termination="optimal" if primary_optimal else "not_proven",
                duration_ms=elapsed(),
                message=str(primary.message or "Solver finished."),
                objective=selected_objective,
                objective_bound=dual_bound,
                gap=gap,
                node_count=node_count,
            ),
            minimum_budget_required=minimum_budget,
            budget_shortfall=_ZERO,
        )


__all__ = [
    "PortfolioOptimizer",
    "floor_states",
    "objective_contribution",
]
