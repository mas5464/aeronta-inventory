"""Immutable, explainable output contract for one advisory planning run."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trax_io_reco.contracts.candidate import (
    CandidateKind,
    CurrencyCode,
    NonEmptyStr,
    NonNegativeDecimal,
    UnitIntervalDecimal,
)
from trax_io_reco.contracts.planning import (
    MandatoryFloor,
    ObjectiveContribution,
    PortfolioKeyMenu,
    PortfolioSolveRequest,
    PortfolioSolveResult,
)

PLANNING_RUN_CONTRACT_VERSION = "planning-run.v1"


class _PlanningRunBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    contract_version: Literal["planning-run.v1"] = PLANNING_RUN_CONTRACT_VERSION


class PlanningWarning(_PlanningRunBase):
    """Stable, countable warning suitable for API, UI, and telemetry."""

    code: NonEmptyStr
    count: int = Field(ge=1)
    detail: NonEmptyStr


class CandidateChoiceSnapshot(_PlanningRunBase):
    """Comparison fields needed to explain one current or alternative choice."""

    candidate_id: NonEmptyStr
    label: NonEmptyStr
    candidate_kind: CandidateKind
    acquisition_cash: NonNegativeDecimal
    expected_shortage: NonNegativeDecimal
    expected_service_level: UnitIntervalDecimal
    expected_aog_risk: UnitIntervalDecimal
    objective: ObjectiveContribution
    confidence: UnitIntervalDecimal
    feasible: bool
    infeasibility_reasons: tuple[NonEmptyStr, ...] = ()
    hard_constraint_ids: tuple[NonEmptyStr, ...] = ()
    mandatory_floor_ids: tuple[NonEmptyStr, ...] = ()


class RejectedAlternative(_PlanningRunBase):
    """One unselected candidate and the first binding reason it was rejected."""

    candidate: CandidateChoiceSnapshot
    reason_code: Literal[
        "hard_constraint",
        "candidate_infeasible",
        "mandatory_floor",
        "budget",
        "lower_objective",
        "portfolio_tradeoff",
    ]
    reason: NonEmptyStr


class PlanningSelectionDetail(_PlanningRunBase):
    """Current, selected, and nearest rejected choices for one inventory key."""

    decision_key: NonEmptyStr
    current: CandidateChoiceSnapshot
    selected: CandidateChoiceSnapshot
    selected_reason: NonEmptyStr
    rejected_alternatives: tuple[RejectedAlternative, ...] = ()

    @model_validator(mode="after")
    def _coherent_selection(self) -> Self:
        if self.current.candidate_kind != "no_change":
            raise ValueError("current choice must be the no-change candidate")
        ids = [
            alternative.candidate.candidate_id
            for alternative in self.rejected_alternatives
        ]
        if self.selected.candidate_id in ids:
            raise ValueError("selected candidate cannot appear as rejected")
        if len(ids) != len(set(ids)):
            raise ValueError("rejected candidate ids must be unique")
        return self


class PlanningAssumptionChange(_PlanningRunBase):
    """One explicit material input change between a parent and rerun."""

    field: NonEmptyStr
    before: str
    after: str


class PlanningRunOutcome(_PlanningRunBase):
    """Terminal advisory result persisted by the planning-run lifecycle."""

    run_id: NonEmptyStr
    parent_run_id: NonEmptyStr | None = None
    parent_planning_fingerprint: str | None = Field(
        default=None,
        pattern=r"^planning_[0-9a-f]{64}$",
    )
    parent_source_snapshot_hash: NonEmptyStr | None = None
    planning_fingerprint: str = Field(pattern=r"^planning_[0-9a-f]{64}$")
    tenant_id: NonEmptyStr
    source_snapshot_hash: NonEmptyStr
    currency: CurrencyCode
    status: Literal["completed", "infeasible", "failed"]
    advisory_only: Literal[True] = True
    request: PortfolioSolveRequest
    result: PortfolioSolveResult
    selection_details: tuple[PlanningSelectionDetail, ...] = ()
    assumption_diff: tuple[PlanningAssumptionChange, ...] = ()
    warnings: tuple[PlanningWarning, ...] = ()

    @model_validator(mode="after")
    def _outcome_reconciles(self) -> Self:
        if self.request.tenant_id != self.tenant_id:
            raise ValueError("planning request tenant does not match run tenant")
        if self.result.tenant_id != self.tenant_id:
            raise ValueError("planning result tenant does not match run tenant")
        if self.request.source_snapshot_hash != self.source_snapshot_hash:
            raise ValueError("planning snapshot does not match run snapshot")
        if self.request.currency != self.currency:
            raise ValueError("planning request currency does not match run currency")
        if self.result.planning_fingerprint != self.planning_fingerprint:
            raise ValueError("planning result fingerprint does not match run")
        if self.result.status != self.status:
            raise ValueError("planning result status does not match run")
        if self.status == "completed":
            assert self.result.summary is not None
            if (
                self.result.summary.currency != self.request.currency
                or self.result.summary.budget != self.request.budget
            ):
                raise ValueError(
                    "planning result summary currency and budget must match request"
                )
            if len(self.selection_details) != len(self.result.selections):
                raise ValueError("selection details must cover every completed selection")
            menus = {
                menu.frontier.decision_key: menu for menu in self.request.menus
            }
            expected_keys = tuple(
                selection.decision_key for selection in self.result.selections
            )
            detail_keys = tuple(detail.decision_key for detail in self.selection_details)
            if detail_keys != expected_keys:
                raise ValueError("selection detail keys must match result order")
            for result_selection, detail in zip(
                self.result.selections,
                self.selection_details,
                strict=True,
            ):
                menu = menus[result_selection.decision_key]
                candidates = {
                    candidate.candidate_id: candidate
                    for candidate in menu.frontier.candidates
                }
                baseline = candidates[result_selection.current_candidate_id]
                selected = candidates[result_selection.selected_candidate_id]
                if (
                    not _snapshot_matches(
                        detail.current,
                        request=self.request,
                        menu=menu,
                        baseline=baseline,
                        candidate=baseline,
                    )
                    or not _snapshot_matches(
                        detail.selected,
                        request=self.request,
                        menu=menu,
                        baseline=baseline,
                        candidate=selected,
                    )
                    or detail.selected.objective != result_selection.objective
                    or detail.selected.acquisition_cash
                    != result_selection.acquisition_cash
                ):
                    raise ValueError("selection detail does not reconcile to solver result")
                rejected_ids = {
                    alternative.candidate.candidate_id
                    for alternative in detail.rejected_alternatives
                }
                if not rejected_ids <= set(candidates):
                    raise ValueError(
                        "rejected selection detail references an unknown candidate"
                    )
                for alternative in detail.rejected_alternatives:
                    candidate = candidates[alternative.candidate.candidate_id]
                    if not _snapshot_matches(
                        alternative.candidate,
                        request=self.request,
                        menu=menu,
                        baseline=baseline,
                        candidate=candidate,
                    ):
                        raise ValueError(
                            "rejected selection detail does not reconcile to frontier"
                        )
        elif self.selection_details:
            raise ValueError("non-completed run cannot expose actionable selection details")
        if self.parent_run_id is None and self.assumption_diff:
            raise ValueError("assumption diff requires a parent run")
        parent_fields = (
            self.parent_run_id,
            self.parent_planning_fingerprint,
            self.parent_source_snapshot_hash,
        )
        if any(value is None for value in parent_fields) != all(
            value is None for value in parent_fields
        ):
            raise ValueError(
                "parent run id, fingerprint, and snapshot must be supplied together"
            )
        warning_codes = [warning.code for warning in self.warnings]
        if len(warning_codes) != len(set(warning_codes)):
            raise ValueError("planning warning codes must be unique")
        if self.status == "completed":
            assert self.result.summary is not None
            summary = self.result.summary
            if (
                summary.warning_count is not None
                and summary.warning_count
                != sum(warning.count for warning in self.warnings)
            ):
                raise ValueError("planning warning count does not reconcile")
            confidence = summary.confidence_summary
            if confidence is not None:
                selected_confidence_total = Decimal("0")
                minimum_selected_confidence: Decimal | None = None
                low_confidence_key_count = 0
                for detail in self.selection_details:
                    value = detail.selected.confidence
                    selected_confidence_total += value
                    minimum_selected_confidence = (
                        value
                        if minimum_selected_confidence is None
                        else min(minimum_selected_confidence, value)
                    )
                    low_confidence_key_count += int(
                        value < confidence.low_confidence_threshold
                    )
                if (
                    confidence.selected_confidence_total
                    != selected_confidence_total
                ):
                    raise ValueError(
                        "selected confidence total does not reconcile"
                    )
                if (
                    confidence.minimum_selected_confidence
                    != minimum_selected_confidence
                ):
                    raise ValueError(
                        "minimum selected confidence does not reconcile"
                    )
                if (
                    confidence.low_confidence_key_count
                    != low_confidence_key_count
                ):
                    raise ValueError(
                        "low-confidence selected key count does not reconcile"
                    )
        return self


def _objective_for(
    *,
    request: PortfolioSolveRequest,
    menu: PortfolioKeyMenu,
    baseline: object,
    candidate: object,
) -> ObjectiveContribution:
    """Recompute the public objective ledger at the immutable contract boundary."""

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


def _unsatisfied_floor_ids(
    candidate: object,
    floors: tuple[MandatoryFloor, ...],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            floor.floor_id for floor in floors if not floor.satisfied_by(candidate)
        )
    )


def _snapshot_matches(
    snapshot: CandidateChoiceSnapshot,
    *,
    request: PortfolioSolveRequest,
    menu: PortfolioKeyMenu,
    baseline: object,
    candidate: object,
) -> bool:
    hard_constraint_ids = tuple(
        sorted(
            constraint.constraint_id
            for constraint in candidate.constraints
            if constraint.hard and not constraint.satisfied
        )
    )
    return (
        snapshot.candidate_id == candidate.candidate_id
        and snapshot.label == candidate.label
        and snapshot.candidate_kind == candidate.candidate_kind
        and snapshot.acquisition_cash
        == candidate.lifecycle_costs.acquisition_cash
        and snapshot.expected_shortage == candidate.outcome.expected_shortage
        and snapshot.expected_service_level
        == candidate.outcome.expected_service_level
        and snapshot.expected_aog_risk == candidate.outcome.expected_aog_risk
        and snapshot.objective
        == _objective_for(
            request=request,
            menu=menu,
            baseline=baseline,
            candidate=candidate,
        )
        and snapshot.confidence == candidate.confidence
        and snapshot.feasible == candidate.feasible
        and snapshot.infeasibility_reasons == candidate.infeasibility_reasons
        and snapshot.hard_constraint_ids == hard_constraint_ids
        and snapshot.mandatory_floor_ids
        == _unsatisfied_floor_ids(candidate, menu.mandatory_floors)
    )


__all__ = [
    "CandidateChoiceSnapshot",
    "PLANNING_RUN_CONTRACT_VERSION",
    "PlanningAssumptionChange",
    "PlanningRunOutcome",
    "PlanningSelectionDetail",
    "PlanningWarning",
    "RejectedAlternative",
]
