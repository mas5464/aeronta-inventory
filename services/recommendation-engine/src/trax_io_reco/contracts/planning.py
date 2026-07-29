"""Immutable contracts for tenant-scoped portfolio planning and optimization."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from trax_io_reco.contracts.candidate import (
    CandidateFrontier,
    CurrencyCode,
    FrozenDict,
    NonEmptyStr,
    NonNegativeDecimal,
    PolicyCandidate,
    UnitIntervalDecimal,
)

PLANNING_CONTRACT_VERSION = "planning.v1"
OPTIMIZER_VERSION = "portfolio-optimizer-v1"
OBJECTIVE_VERSION = "criticality-shortage-aog-cost-v1"
LOW_SELECTED_CONFIDENCE_THRESHOLD = Decimal("0.5")
# The shared job lease is 1,800 seconds. Keeping the solver at or below ten
# minutes leaves a conservative margin for fingerprinting, model construction,
# terminal validation, persistence, and scheduler jitter before stale recovery.
MAX_PLANNING_SOLVER_SECONDS = 600.0


def _immutable_weights(
    value: dict[int, NonNegativeDecimal],
) -> FrozenDict:
    return FrozenDict(value)


CriticalityWeights = Annotated[
    dict[int, NonNegativeDecimal],
    AfterValidator(_immutable_weights),
]


class _PlanningBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    contract_version: Literal["planning.v1"] = PLANNING_CONTRACT_VERSION


class TenantObjectiveWeights(_PlanningBase):
    """Versioned, unit-aware objective weights with safe deterministic defaults."""

    version: Literal["criticality-shortage-aog-cost-v1"] = OBJECTIVE_VERSION
    shortage_reduction_weight: NonNegativeDecimal = Decimal("1")
    aog_risk_reduction_weight: NonNegativeDecimal = Decimal("1")
    holding_cost_penalty_weight: NonNegativeDecimal = Decimal("0.01")
    ordering_cost_penalty_weight: NonNegativeDecimal = Decimal("0.01")
    criticality_weights: CriticalityWeights = Field(
        default_factory=lambda: FrozenDict(
            {
                1: Decimal("5"),
                2: Decimal("3"),
                3: Decimal("2"),
                4: Decimal("1"),
                5: Decimal("1"),
            }
        )
    )

    @model_validator(mode="after")
    def _valid_weights(self) -> Self:
        if set(self.criticality_weights) != {1, 2, 3, 4, 5}:
            raise ValueError("criticality weights must define tiers 1 through 5")
        if not any(
            (
                self.shortage_reduction_weight,
                self.aog_risk_reduction_weight,
                self.holding_cost_penalty_weight,
                self.ordering_cost_penalty_weight,
            )
        ):
            raise ValueError("at least one objective weight must be positive")
        return self


class MandatoryFloor(_PlanningBase):
    """One explicit hard service/risk floor for a decision key."""

    floor_id: NonEmptyStr
    source: NonEmptyStr
    min_service_level: UnitIntervalDecimal | None = None
    max_expected_shortage: NonNegativeDecimal | None = None
    max_aog_risk: UnitIntervalDecimal | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def _has_threshold(self) -> Self:
        if (
            self.min_service_level is None
            and self.max_expected_shortage is None
            and self.max_aog_risk is None
        ):
            raise ValueError("mandatory floor must define at least one threshold")
        return self

    def satisfied_by(self, candidate: PolicyCandidate) -> bool:
        outcome = candidate.outcome
        return (
            (
                self.min_service_level is None
                or outcome.expected_service_level >= self.min_service_level
            )
            and (
                self.max_expected_shortage is None
                or outcome.expected_shortage <= self.max_expected_shortage
            )
            and (
                self.max_aog_risk is None
                or outcome.expected_aog_risk <= self.max_aog_risk
            )
        )


class PortfolioKeyMenu(_PlanningBase):
    """One exactly-one candidate menu and its tenant criticality/floor context."""

    frontier: CandidateFrontier
    criticality_tier: int = Field(ge=1, le=5)
    mandatory_floors: tuple[MandatoryFloor, ...] = ()

    @model_validator(mode="after")
    def _unique_floors(self) -> Self:
        floor_ids = [floor.floor_id for floor in self.mandatory_floors]
        if len(floor_ids) != len(set(floor_ids)):
            raise ValueError("mandatory floor ids must be unique within a key")
        return self


class PortfolioSolveRequest(_PlanningBase):
    """Immutable optimizer input; operational request timestamps are excluded."""

    tenant_id: NonEmptyStr
    source_snapshot_hash: NonEmptyStr
    horizon_days: int = Field(gt=0)
    currency: CurrencyCode
    budget: NonNegativeDecimal
    menus: tuple[PortfolioKeyMenu, ...]
    objective_weights: TenantObjectiveWeights = TenantObjectiveWeights()
    tenant_policy_version: NonEmptyStr
    forecast_version: NonEmptyStr
    repair_model_version: NonEmptyStr
    candidate_planner_version: NonEmptyStr
    optimizer_version: Literal["portfolio-optimizer-v1"] = OPTIMIZER_VERSION
    time_limit_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=MAX_PLANNING_SOLVER_SECONDS,
    )

    @model_validator(mode="before")
    @classmethod
    def _canonical_menus(cls, value: object) -> object:
        if not isinstance(value, dict) or "menus" not in value:
            return value
        normalized = dict(value)
        normalized["menus"] = tuple(
            sorted(
                normalized["menus"],
                key=lambda menu: (
                    menu.frontier.decision_key
                    if isinstance(menu, PortfolioKeyMenu)
                    else menu["frontier"]["decision_key"]
                ),
            )
        )
        return normalized

    @model_validator(mode="after")
    def _coherent_request(self) -> Self:
        if not self.menus:
            raise ValueError("portfolio solve requires at least one key menu")
        keys = [menu.frontier.decision_key for menu in self.menus]
        if len(keys) != len(set(keys)):
            raise ValueError("portfolio decision keys must be unique")
        if keys != sorted(keys):
            raise ValueError("portfolio menus must use canonical key order")
        for menu in self.menus:
            frontier = menu.frontier
            if frontier.tenant_id != self.tenant_id:
                raise ValueError("frontier tenant does not match solve tenant")
            if frontier.currency != self.currency:
                raise ValueError("frontier currency does not match solve currency")
            if frontier.planner_version != self.candidate_planner_version:
                raise ValueError("frontier planner version does not match solve input")
        return self


class ObjectiveContribution(_PlanningBase):
    currency: CurrencyCode
    criticality_weight: NonNegativeDecimal
    shortage_reduction: Decimal
    aog_risk_reduction: Decimal
    incremental_holding_cost: Decimal
    incremental_ordering_cost: Decimal
    shortage_value: Decimal
    aog_value: Decimal
    holding_penalty: Decimal
    ordering_penalty: Decimal
    total: Decimal

    @model_validator(mode="after")
    def _total_reconciles(self) -> Self:
        expected = (
            self.shortage_value
            + self.aog_value
            - self.holding_penalty
            - self.ordering_penalty
        )
        if self.total != expected:
            raise ValueError("objective contribution does not reconcile")
        return self


class FloorState(_PlanningBase):
    floor_id: NonEmptyStr
    source: NonEmptyStr
    satisfied: bool
    binding: bool
    detail: str | None = None


class PortfolioSelection(_PlanningBase):
    tenant_id: NonEmptyStr
    decision_key: NonEmptyStr
    current_candidate_id: NonEmptyStr
    selected_candidate_id: NonEmptyStr
    selected_is_no_change: bool
    acquisition_cash: NonNegativeDecimal
    expected_shortage: NonNegativeDecimal
    expected_service_level: UnitIntervalDecimal
    expected_aog_risk: UnitIntervalDecimal
    objective: ObjectiveContribution
    floor_states: tuple[FloorState, ...] = ()

    @model_validator(mode="after")
    def _selection_reconciles(self) -> Self:
        floor_ids = [floor.floor_id for floor in self.floor_states]
        if len(floor_ids) != len(set(floor_ids)):
            raise ValueError("selection floor ids must be unique")
        if any(not floor.satisfied for floor in self.floor_states):
            raise ValueError("selected candidate cannot violate a mandatory floor")
        return self


class PortfolioConfidenceSummary(_PlanningBase):
    """Exactly reconcilable confidence evidence for selected candidates."""

    selected_confidence_total: NonNegativeDecimal
    minimum_selected_confidence: UnitIntervalDecimal
    low_confidence_threshold: UnitIntervalDecimal = (
        LOW_SELECTED_CONFIDENCE_THRESHOLD
    )
    low_confidence_key_count: int = Field(ge=0)


class PortfolioSummary(_PlanningBase):
    currency: CurrencyCode
    budget: NonNegativeDecimal
    selected_acquisition_cash: NonNegativeDecimal
    budget_slack: NonNegativeDecimal
    selected_key_count: int = Field(ge=0)
    no_change_key_count: int = Field(ge=0)
    selected_objective: Decimal
    expected_shortage: NonNegativeDecimal
    average_service_level: UnitIntervalDecimal
    maximum_aog_risk: UnitIntervalDecimal
    warning_count: int | None = Field(default=None, ge=0)
    confidence_summary: PortfolioConfidenceSummary | None = None

    @model_validator(mode="after")
    def _summary_reconciles(self) -> Self:
        if self.selected_acquisition_cash + self.budget_slack != self.budget:
            raise ValueError("portfolio budget does not reconcile")
        if self.no_change_key_count > self.selected_key_count:
            raise ValueError("no-change count cannot exceed selected key count")
        if self.confidence_summary is not None:
            if self.selected_key_count == 0:
                raise ValueError(
                    "confidence summary requires at least one selected key"
                )
            if (
                self.confidence_summary.selected_confidence_total
                > Decimal(self.selected_key_count)
            ):
                raise ValueError(
                    "selected confidence total cannot exceed selected key count"
                )
            if (
                self.confidence_summary.low_confidence_key_count
                > self.selected_key_count
            ):
                raise ValueError(
                    "low-confidence count cannot exceed selected key count"
                )
        return self


class SolverEvidence(_PlanningBase):
    implementation: Literal["scipy.optimize.milp/highs"]
    implementation_version: NonEmptyStr
    optimizer_version: Literal["portfolio-optimizer-v1"] = OPTIMIZER_VERSION
    termination: Literal["optimal", "not_proven", "infeasible", "failed"]
    optimality_proven: bool
    objective: Decimal | None = None
    objective_bound: Decimal | None = None
    relative_gap: NonNegativeDecimal | None = None
    duration_ms: NonNegativeDecimal
    node_count: int | None = Field(default=None, ge=0)
    message: NonEmptyStr

    @model_validator(mode="after")
    def _coherent_termination(self) -> Self:
        if self.optimality_proven != (self.termination == "optimal"):
            raise ValueError("optimality_proven must match optimal termination")
        if self.termination in {"optimal", "not_proven"} and self.objective is None:
            raise ValueError("feasible solver termination requires an objective")
        return self


class PortfolioSolveResult(_PlanningBase):
    planning_fingerprint: str = Field(pattern=r"^planning_[0-9a-f]{64}$")
    tenant_id: NonEmptyStr
    status: Literal["completed", "infeasible", "failed"]
    selections: tuple[PortfolioSelection, ...] = ()
    summary: PortfolioSummary | None = None
    solver: SolverEvidence
    minimum_budget_required: NonNegativeDecimal | None = None
    budget_shortfall: NonNegativeDecimal | None = None
    infeasible_keys: tuple[NonEmptyStr, ...] = ()
    infeasible_floor_ids: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _result_reconciles(self) -> Self:
        if self.status == "completed":
            if self.summary is None or not self.selections:
                raise ValueError("completed portfolio requires summary and selections")
            if self.solver.termination not in {"optimal", "not_proven"}:
                raise ValueError("completed portfolio requires a feasible solver result")
            if self.summary.selected_key_count != len(self.selections):
                raise ValueError("portfolio selection count does not reconcile")
            spend = sum(
                (selection.acquisition_cash for selection in self.selections),
                Decimal("0"),
            )
            objective = sum(
                (selection.objective.total for selection in self.selections),
                Decimal("0"),
            )
            if spend != self.summary.selected_acquisition_cash:
                raise ValueError("portfolio selection spend does not reconcile")
            if objective != self.summary.selected_objective:
                raise ValueError("portfolio selection objective does not reconcile")
            keys = [selection.decision_key for selection in self.selections]
            if len(keys) != len(set(keys)) or keys != sorted(keys):
                raise ValueError(
                    "portfolio selection keys must be unique and canonically ordered"
                )
            if any(
                selection.tenant_id != self.tenant_id
                for selection in self.selections
            ):
                raise ValueError("portfolio selection tenant does not match result")
            if self.summary.no_change_key_count != sum(
                selection.selected_is_no_change
                for selection in self.selections
            ):
                raise ValueError("portfolio no-change count does not reconcile")
            shortage = sum(
                (
                    selection.expected_shortage
                    for selection in self.selections
                ),
                Decimal("0"),
            )
            if shortage != self.summary.expected_shortage:
                raise ValueError("portfolio expected shortage does not reconcile")
            average_service = sum(
                (
                    selection.expected_service_level
                    for selection in self.selections
                ),
                Decimal("0"),
            ) / Decimal(len(self.selections))
            if average_service != self.summary.average_service_level:
                raise ValueError("portfolio average service level does not reconcile")
            maximum_aog = max(
                selection.expected_aog_risk
                for selection in self.selections
            )
            if maximum_aog != self.summary.maximum_aog_risk:
                raise ValueError("portfolio maximum AOG risk does not reconcile")
            if self.solver.objective != self.summary.selected_objective:
                raise ValueError("solver and portfolio objective do not reconcile")
        elif self.selections or self.summary is not None:
            raise ValueError("non-completed portfolio cannot expose actionable selections")
        if self.status == "infeasible" and (
            self.minimum_budget_required is None or self.budget_shortfall is None
        ):
            raise ValueError("infeasible portfolio requires budget guidance")
        return self
