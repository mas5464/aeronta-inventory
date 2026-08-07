"""Resource-oriented BFF routes for advisory portfolio planning runs."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Never
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from trax_io_reco.contracts.planning import (
    MAX_PLANNING_SOLVER_SECONDS,
    MandatoryFloor,
    PortfolioKeyMenu,
    PortfolioSolveRequest,
    PortfolioSummary,
    TenantObjectiveWeights,
)

from trax_io_spine.bff.advisory_flags import advisory_enabled
from trax_io_spine.bff.store import RecommendationNotFound
from trax_io_spine.pg.planning import (
    PlanningRerunConfig,
    PlanningRunRecord,
    PlanningRunSelectionRecord,
    PlanningRunSubmission,
)
from trax_io_spine.planning_inputs import PlanningInputSnapshot

router = APIRouter()

PLANNING_BASE = "/v1/tenants/{tenant_id}/planning-runs"
_PLANNING_ROLES = frozenset({"planner", "admin", "owner"})
MAX_PLANNING_HORIZON_DAYS = 3_650
MAX_PLANNING_SELECTION_OFFSET = 1_000_000
_Currency = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_upper=True,
        pattern=r"^[A-Z]{3}$",
    ),
]
_BudgetDecimal = Annotated[
    Decimal,
    Field(
        ge=Decimal("0"),
        max_digits=18,
        decimal_places=2,
    ),
]
_BoundedNonNegativeDecimal = Annotated[
    Decimal,
    Field(
        ge=Decimal("0"),
        max_digits=18,
        decimal_places=6,
    ),
]
_BoundedUnitIntervalDecimal = Annotated[
    Decimal,
    Field(
        ge=Decimal("0"),
        le=Decimal("1"),
        max_digits=7,
        decimal_places=6,
    ),
]
_DecisionKey = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=257,
        pattern=r"^[^@\r\n\t]+@[^@\r\n\t]+$",
    ),
]


class _PlanningBffModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class PlanningScopeKey(_PlanningBffModel):
    pn: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[^@\r\n\t]+$",
    )
    location: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[^@\r\n\t]+$",
    )

    @property
    def decision_key(self) -> str:
        return f"{self.pn}@{self.location}"


class PlanningObjectiveWeightsInput(_PlanningBffModel):
    """Bounded browser objective weights converted to the domain contract."""

    contract_version: Literal["planning.v1"] = "planning.v1"
    version: Literal["criticality-shortage-aog-cost-v1"] = (
        "criticality-shortage-aog-cost-v1"
    )
    shortage_reduction_weight: _BoundedNonNegativeDecimal = Decimal("1")
    aog_risk_reduction_weight: _BoundedNonNegativeDecimal = Decimal("1")
    holding_cost_penalty_weight: _BoundedNonNegativeDecimal = Decimal("0.01")
    ordering_cost_penalty_weight: _BoundedNonNegativeDecimal = Decimal("0.01")
    criticality_weights: dict[
        Annotated[int, Field(ge=1, le=5)],
        _BoundedNonNegativeDecimal,
    ] = Field(
        default_factory=lambda: {
            1: Decimal("5"),
            2: Decimal("3"),
            3: Decimal("2"),
            4: Decimal("1"),
            5: Decimal("1"),
        },
        min_length=5,
        max_length=5,
    )

    @model_validator(mode="after")
    def _valid_weights(self) -> PlanningObjectiveWeightsInput:
        if set(self.criticality_weights) != {1, 2, 3, 4, 5}:
            raise ValueError(
                "criticality weights must define tiers 1 through 5"
            )
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

    def to_domain(self) -> TenantObjectiveWeights:
        return TenantObjectiveWeights.model_validate(
            self.model_dump(mode="python")
        )


class PlanningMandatoryFloorInput(_PlanningBffModel):
    """Bounded planner-authored floor converted to the domain contract."""

    floor_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[^\r\n\t]+$",
    )
    source: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[^\r\n\t]+$",
    )
    min_service_level: _BoundedUnitIntervalDecimal | None = None
    max_expected_shortage: _BoundedNonNegativeDecimal | None = None
    max_aog_risk: _BoundedUnitIntervalDecimal | None = None
    detail: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _has_threshold(self) -> PlanningMandatoryFloorInput:
        if (
            self.min_service_level is None
            and self.max_expected_shortage is None
            and self.max_aog_risk is None
        ):
            raise ValueError(
                "mandatory floor must define at least one threshold"
            )
        return self

    def to_domain(self) -> MandatoryFloor:
        return MandatoryFloor(
            floor_id=self.floor_id,
            source=self.source,
            min_service_level=self.min_service_level,
            max_expected_shortage=self.max_expected_shortage,
            max_aog_risk=self.max_aog_risk,
            detail=self.detail,
        )


_MandatoryFloorList = Annotated[
    tuple[PlanningMandatoryFloorInput, ...],
    Field(max_length=20),
]


class CreatePlanningRunRequest(_PlanningBffModel):
    """Planner-controlled assumptions; authoritative candidate menus stay server-side."""

    scope_kind: Literal["explicit", "all_eligible"] = "explicit"
    keys: tuple[PlanningScopeKey, ...] = Field(default=(), max_length=200)
    budget: _BudgetDecimal
    horizon_days: int = Field(gt=0, le=MAX_PLANNING_HORIZON_DAYS)
    currency: _Currency = "USD"
    objective_weights: PlanningObjectiveWeightsInput = Field(
        default_factory=PlanningObjectiveWeightsInput
    )
    mandatory_floors: dict[_DecisionKey, _MandatoryFloorList] = Field(
        default_factory=dict,
        max_length=200,
    )
    time_limit_seconds: float = Field(
        default=30.0,
        gt=0.0,
        le=MAX_PLANNING_SOLVER_SECONDS,
    )
    parent_run_id: UUID | None = None

    @model_validator(mode="after")
    def _canonical_scope(self) -> CreatePlanningRunRequest:
        key_ids = [key.decision_key for key in self.keys]
        if self.scope_kind == "explicit" and not key_ids:
            raise ValueError("explicit planning scope requires at least one key")
        if self.scope_kind == "all_eligible" and key_ids:
            raise ValueError(
                "all_eligible scope is server-resolved and cannot include client keys"
            )
        if len(key_ids) != len(set(key_ids)):
            raise ValueError("planning scope keys must be unique")
        if key_ids != sorted(key_ids):
            raise ValueError("planning scope keys must use canonical order")
        total_floors = 0
        for floors in self.mandatory_floors.values():
            total_floors += len(floors)
        if total_floors > 500:
            raise ValueError(
                "a planning request cannot declare more than 500 mandatory floors"
            )
        return self


class PlanningCoverage(_PlanningBffModel):
    """Coverage derived only from the immutable submitted candidate menus."""

    scope_key_count: int = Field(ge=0)
    authoritative_key_count: int = Field(ge=0)
    eligible_key_count: int = Field(ge=0)
    missing_candidate_frontier_key_count: int = Field(ge=0)
    criticality_unknown_key_count: int = Field(ge=0)
    candidate_menu_key_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    feasible_candidate_count: int = Field(ge=0)
    candidate_menu_coverage_rate: Decimal = Field(ge=0, le=1)
    repair_model_key_count: int = Field(ge=0)
    repair_model_coverage_rate: Decimal = Field(ge=0, le=1)
    repair_credit_key_count: int = Field(ge=0)
    repair_credit_coverage_rate: Decimal = Field(ge=0, le=1)
    low_confidence_key_count: int = Field(ge=0)
    minimum_candidate_confidence: Decimal | None = Field(
        default=None,
        ge=0,
        le=1,
    )
    tat_confidence_status: Literal["available", "partial", "unavailable"]
    disclosure: str


class PlanningScopeSummary(_PlanningBffModel):
    kind: Literal["explicit", "all_eligible"]
    key_count: int = Field(ge=0)
    preview_keys: tuple[str, ...]
    preview_truncated: bool


class PlanningEvidenceCount(_PlanningBffModel):
    code: str
    count: int = Field(ge=1)


class PlanningEvidenceSummary(_PlanningBffModel):
    total: int = Field(ge=0)
    counted_items: int = Field(ge=0)
    by_code: tuple[PlanningEvidenceCount, ...]
    code_list_truncated: bool


class PlanningInfeasibilitySummary(_PlanningBffModel):
    minimum_budget_required: Decimal | None = Field(default=None, ge=0)
    budget_shortfall: Decimal | None = Field(default=None, ge=0)
    infeasible_key_count: int = Field(ge=0)
    infeasible_key_sample: tuple[str, ...]
    infeasible_floor_count: int = Field(ge=0)
    infeasible_floor_sample: tuple[str, ...]


class PlanningRunSafeDetail(_PlanningBffModel):
    error_code: str | None = None
    guidance: str | None = None
    retryable: bool | None = None
    failed_attempt: int | None = Field(default=None, ge=0)
    last_failed_attempt: int | None = Field(default=None, ge=0)


class PlanningRunView(_PlanningBffModel):
    """Bounded public run header; immutable menus remain server-side."""

    run_id: str
    planning_fingerprint: str
    contract_version: str
    parent_run_id: str | None
    parent_planning_fingerprint: str | None
    parent_source_snapshot_hash: str | None
    assumption_diff: tuple[dict[str, Any], ...]
    status: Literal["queued", "running", "completed", "infeasible", "failed"]
    source_snapshot_hash: str
    source_generation_hash: str
    scope: PlanningScopeSummary
    key_count: int = Field(ge=0)
    budget: Decimal = Field(ge=0)
    horizon_days: int = Field(gt=0)
    currency: str
    model_profile: dict[str, Any]
    advisory_only: bool
    progress_completed: int = Field(ge=0)
    progress_total: int = Field(ge=0)
    summary: PortfolioSummary | None
    infeasibility: PlanningInfeasibilitySummary | None
    solver: dict[str, Any] | None
    warnings: PlanningEvidenceSummary
    skipped_keys: PlanningEvidenceSummary
    detail: PlanningRunSafeDetail
    submitted_by: str
    attempts: int = Field(ge=0)
    claimed_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    coverage: PlanningCoverage | None = None
    stale: bool | None = None
    current_source_snapshot_hash: str | None = None
    current_source_generation_hash: str | None = None
    stale_reason: str | None = None


class PlanningRunSubmissionView(_PlanningBffModel):
    run: PlanningRunView
    created: bool


class PlanningRunSelectionsPage(_PlanningBffModel):
    items: tuple[PlanningRunSelectionRecord, ...] = Field(max_length=100)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class PlanningTrustedModelProfile(_PlanningBffModel):
    tenant_policy_version: str = Field(min_length=1, max_length=256)
    forecast_version: str = Field(min_length=1, max_length=256)
    repair_model_version: str = Field(min_length=1, max_length=256)
    candidate_planner_version: str = Field(min_length=1, max_length=256)


class PlanningSavedModelProfile(PlanningTrustedModelProfile):
    optimizer_version: str = Field(min_length=1, max_length=256)


class PlanningRerunConfigView(_PlanningBffModel):
    """Browser-replayable parent inputs plus current trusted model lineage."""

    contract_version: Literal["planning-rerun-config.v1"] = (
        "planning-rerun-config.v1"
    )
    parent_run_id: UUID
    scope_kind: Literal["explicit", "all_eligible"]
    keys: tuple[PlanningScopeKey, ...] = Field(max_length=200)
    budget: Decimal = Field(ge=0)
    horizon_days: int = Field(gt=0)
    currency: _Currency
    objective_weights: TenantObjectiveWeights
    mandatory_floors: dict[_DecisionKey, _MandatoryFloorList] = Field(
        max_length=200
    )
    time_limit_seconds: float = Field(
        gt=0,
        le=MAX_PLANNING_SOLVER_SECONDS,
    )
    source_generation_hash: str = Field(min_length=1, max_length=128)
    parent_model_profile: PlanningSavedModelProfile
    current_trusted_model_profile: PlanningTrustedModelProfile | None
    repair_assumption_change_available: bool
    repair_assumption_mode: Literal["current_trusted"] = "current_trusted"


class PlanningCapability(_PlanningBffModel):
    contract_version: Literal["planning-capability.v1"] = "planning-capability.v1"
    enabled: bool
    advisory_only: Literal[True] = True
    can_read: bool
    can_submit: bool
    reason_code: Literal[
        "enabled",
        "feature_disabled",
        "insufficient_role",
    ]


def _api_error(
    status_code: int,
    *,
    code: str,
    message: str,
    retryable: bool = False,
) -> Never:
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    )


def _claims(request: Request) -> dict:
    claims = getattr(request.state, "claims", None)
    if not claims:
        _api_error(
            401,
            code="planning_auth_required",
            message="Verified authentication is required.",
        )
    return claims


def _require_planner(claims: dict) -> None:
    if claims.get("tenant_role") not in _PLANNING_ROLES:
        _api_error(
            403,
            code="planning_role_required",
            message="A planner, admin, or owner role is required.",
        )


def _require_feature(request: Request, tenant_id: str) -> None:
    if not advisory_enabled(request, tenant_id):
        _api_error(
            404,
            code="planning_feature_disabled",
            message="Advisory portfolio planning is not enabled for this tenant.",
        )


def _planner_store(request: Request, tenant_id: str):
    store = request.app.state.planner_stores.get(tenant_id)
    if store is None:
        registry = getattr(request.app.state, "registry", None)
        if registry is not None:
            store = registry.store_for(tenant_id)
    if store is None:
        _api_error(
            404,
            code="planning_tenant_not_found",
            message="The requested planning tenant is unavailable.",
        )
    return store


def _planning_store(request: Request, tenant_id: str, claims: dict):
    configured = request.app.state.planning_stores.get(tenant_id)
    if configured is not None:
        if callable(configured):
            return configured(
                principal=claims["sub"],
                role=claims.get("tenant_role", "viewer"),
            )
        return configured
    registry = getattr(request.app.state, "registry", None)
    if registry is not None and hasattr(registry, "planning_store_for"):
        store = registry.planning_store_for(
            tenant_id,
            principal=claims["sub"],
            role=claims.get("tenant_role", "viewer"),
        )
        if store is not None:
            return store
    _api_error(
        503,
        code="planning_store_unavailable",
        message="Portfolio planning is temporarily unavailable.",
        retryable=True,
    )


def _single_version(values: set[str], *, label: str) -> str:
    if not values:
        return f"{label}-unavailable"
    return next(iter(values)) if len(values) == 1 else "+".join(sorted(values))


def _planning_input_snapshot(
    *,
    body: CreatePlanningRunRequest,
    planner_store,
) -> PlanningInputSnapshot:
    reader = getattr(planner_store, "planning_input_snapshot", None)
    if not callable(reader):
        _api_error(
            503,
            code="planning_snapshot_unavailable",
            message="The authoritative planning snapshot is temporarily unavailable.",
            retryable=True,
        )
    requested_keys = (
        None
        if body.scope_kind == "all_eligible"
        else tuple((key.pn, key.location) for key in body.keys)
    )
    try:
        snapshot = reader(requested_keys)
    except RecommendationNotFound:
        _api_error(
            422,
            code="planning_input_not_found",
            message="A selected key is not available in the tenant planning universe.",
        )
    except LookupError:
        _api_error(
            422,
            code="planning_scope_empty",
            message="No eligible planning keys are available for this tenant.",
        )
    except ValueError:
        _api_error(
            503,
            code="planning_snapshot_invalid",
            message="The authoritative planning snapshot is inconsistent.",
            retryable=True,
        )
    if not isinstance(snapshot, PlanningInputSnapshot):
        _api_error(
            503,
            code="planning_snapshot_invalid",
            message="The authoritative planning snapshot is inconsistent.",
            retryable=True,
        )
    if not snapshot.contexts:
        _api_error(
            422,
            code="planning_scope_empty",
            message="No eligible planning keys are available for this tenant.",
        )
    return snapshot


def _build_request(
    *,
    tenant_id: str,
    body: CreatePlanningRunRequest,
    planner_store,
 ) -> tuple[PortfolioSolveRequest, PlanningInputSnapshot]:
    snapshot = _planning_input_snapshot(body=body, planner_store=planner_store)
    menus: list[PortfolioKeyMenu] = []
    frontiers = []
    requested_floor_keys = set(body.mandatory_floors)

    for context in snapshot.contexts:
        context_key = f"{context.pn}@{context.location}"
        frontier = context.candidate_frontier
        if frontier is None:
            _api_error(
                422,
                code="planning_candidate_frontier_missing",
                message=(
                    f"{context_key} has no versioned candidate frontier; "
                    "recompute planning inputs before submitting"
                ),
            )
        if frontier.tenant_id != tenant_id:
            _api_error(
                422,
                code="planning_candidate_tenant_mismatch",
                message="A candidate frontier does not match the requested tenant.",
            )
        if frontier.currency != body.currency:
            _api_error(
                422,
                code="planning_currency_mismatch",
                message=(
                    f"{context_key} frontier currency "
                    f"{frontier.currency} does not match {body.currency}"
                ),
            )
        if context.planning_trace.horizon_days != body.horizon_days:
            _api_error(
                422,
                code="planning_horizon_mismatch",
                message=(
                    f"{context_key} candidate horizon "
                    f"{context.planning_trace.horizon_days} does not match "
                    f"{body.horizon_days}"
                ),
            )
        criticality_tier = context.attributes.criticality_tier
        if criticality_tier not in range(1, 6):
            _api_error(
                422,
                code="planning_criticality_unavailable",
                message=f"{context_key} has no valid criticality tier",
            )
        frontiers.append(frontier)
        requested_floor_keys.discard(frontier.decision_key)
        menus.append(
            PortfolioKeyMenu(
                frontier=frontier,
                criticality_tier=criticality_tier,
                mandatory_floors=tuple(
                    floor.to_domain()
                    for floor in body.mandatory_floors.get(
                        frontier.decision_key,
                        (),
                    )
                ),
            )
        )

    decision_keys = [menu.frontier.decision_key for menu in menus]
    if len(decision_keys) != len(set(decision_keys)):
        _api_error(
            422,
            code="planning_duplicate_scope",
            message="Planning scope resolves to duplicate candidate decision keys.",
        )
    if requested_floor_keys:
        ordered_keys = sorted(requested_floor_keys)
        sample = ", ".join(ordered_keys[:10])
        remaining = len(ordered_keys) - min(len(ordered_keys), 10)
        _api_error(
            422,
            code="planning_floor_outside_scope",
            message=(
                "mandatory floors reference keys outside the resolved scope: "
                + sample
                + (f" (+{remaining} more)" if remaining else "")
            ),
        )

    candidates = [
        candidate
        for frontier in frontiers
        for candidate in frontier.candidates
    ]
    solve_request = PortfolioSolveRequest(
        tenant_id=tenant_id,
        source_snapshot_hash=snapshot.source_snapshot_hash,
        horizon_days=body.horizon_days,
        currency=body.currency,
        budget=body.budget,
        menus=tuple(menus),
        objective_weights=body.objective_weights.to_domain(),
        tenant_policy_version=_single_version(
            {
                candidate.model_identity.policy_version
                for candidate in candidates
            },
            label="tenant-policy",
        ),
        forecast_version=_single_version(
            {
                candidate.model_identity.forecast_version
                for candidate in candidates
            },
            label="forecast",
        ),
        repair_model_version=_single_version(
            {
                candidate.model_identity.repair_version
                for candidate in candidates
                if candidate.model_identity.repair_version is not None
            },
            label="repair-model",
        ),
        candidate_planner_version=frontiers[0].planner_version,
        time_limit_seconds=body.time_limit_seconds,
    )
    return solve_request, snapshot


def _current_snapshot_hash(planner_store) -> str | None:
    """Read one precomputed tenant snapshot marker; never scan planning keys."""

    for name in (
        "planning_source_snapshot_hash",
        "current_planning_source_snapshot_hash",
    ):
        reader = getattr(planner_store, name, None)
        if not callable(reader):
            continue
        value = reader()
        return value if isinstance(value, str) and value else None
    return None


def _current_generation_hash(planner_store) -> str | None:
    """Read the common full-universe generation shared by every scope."""

    for name in (
        "planning_source_generation_hash",
        "current_planning_source_generation_hash",
    ):
        reader = getattr(planner_store, name, None)
        if not callable(reader):
            continue
        value = reader()
        return value if isinstance(value, str) and value else None
    return None


def _trusted_model_profile(
    value: object,
) -> PlanningTrustedModelProfile | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("planning model profile must be an object")
    return PlanningTrustedModelProfile(
        tenant_policy_version=value.get("tenant_policy_version"),
        forecast_version=value.get("forecast_version"),
        repair_model_version=value.get("repair_model_version"),
        candidate_planner_version=value.get("candidate_planner_version"),
    )


def _saved_model_profile(value: object) -> PlanningSavedModelProfile:
    if not isinstance(value, dict):
        raise ValueError("saved planning model profile must be an object")
    return PlanningSavedModelProfile(
        tenant_policy_version=value.get("tenant_policy_version"),
        forecast_version=value.get("forecast_version"),
        repair_model_version=value.get("repair_model_version"),
        candidate_planner_version=value.get("candidate_planner_version"),
        optimizer_version=value.get("optimizer_version"),
    )


def _rerun_config_view(
    config: PlanningRerunConfig,
    *,
    planner_store,
) -> PlanningRerunConfigView:
    keys = []
    for decision_key in config.explicit_scope:
        pn, separator, location = decision_key.rpartition("@")
        if not separator:
            raise ValueError("saved planning scope key is invalid")
        keys.append(PlanningScopeKey(pn=pn, location=location))
    current_reader = getattr(
        planner_store,
        "current_planning_model_profile",
        None,
    )
    current_profile = (
        _trusted_model_profile(current_reader())
        if callable(current_reader)
        else None
    )
    parent_profile = _saved_model_profile(config.model_profile)
    mandatory_floors = {
        decision_key: tuple(
            PlanningMandatoryFloorInput(
                floor_id=floor.floor_id,
                source=floor.source,
                min_service_level=floor.min_service_level,
                max_expected_shortage=floor.max_expected_shortage,
                max_aog_risk=floor.max_aog_risk,
                detail=floor.detail,
            )
            for floor in floors
        )
        for decision_key, floors in config.mandatory_floors.items()
    }
    return PlanningRerunConfigView(
        parent_run_id=config.run_id,
        scope_kind=config.scope_kind,
        keys=tuple(keys),
        budget=config.budget,
        horizon_days=config.horizon_days,
        currency=config.currency,
        objective_weights=config.objective_weights,
        mandatory_floors=mandatory_floors,
        time_limit_seconds=config.time_limit_seconds,
        source_generation_hash=config.source_generation_hash,
        parent_model_profile=parent_profile,
        current_trusted_model_profile=current_profile,
        repair_assumption_change_available=(
            current_profile is not None
            and (
                current_profile.repair_model_version
                != parent_profile.repair_model_version
            )
        ),
    )


def _coverage_from_request(
    request: PortfolioSolveRequest,
    input_coverage: dict[str, int],
) -> PlanningCoverage:
    """Compute immutable submission coverage once from typed in-memory menus."""

    candidate_count = 0
    feasible_count = 0
    repair_model_keys = 0
    repair_credit_keys = 0
    low_confidence_keys = 0
    confidences: list[Decimal] = []

    for menu in request.menus:
        candidates = menu.frontier.candidates
        candidate_count += len(candidates)
        feasible_count += sum(candidate.feasible for candidate in candidates)
        key_confidences = [candidate.confidence for candidate in candidates]
        confidences.extend(key_confidences)
        repair_model_keys += any(
            candidate.model_identity.repair_version not in {None, ""}
            for candidate in candidates
        )
        repair_credit_keys += any(
            str(evidence.kind).lower()
            in {
                "repair_credit",
                "repair_return_credit",
                "repair_return_profile",
            }
            for candidate in candidates
            for evidence in candidate.evidence
        )
        if key_confidences and min(key_confidences) < Decimal("0.5"):
            low_confidence_keys += 1

    scope = input_coverage.get("total_key_count")
    served_keys = input_coverage.get("eligible_key_count")
    missing_frontiers = input_coverage.get("missing_frontier_key_count")
    criticality_known = input_coverage.get("criticality_known_key_count")
    criticality_unknown = input_coverage.get("criticality_unknown_key_count")
    counts = (
        scope,
        served_keys,
        missing_frontiers,
        criticality_known,
        criticality_unknown,
    )
    if (
        any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for value in counts
        )
        or served_keys != len(request.menus)
        or scope != served_keys + missing_frontiers
        or scope != criticality_known + criticality_unknown
        or input_coverage.get("candidate_count") != candidate_count
        or input_coverage.get("feasible_candidate_count") != feasible_count
    ):
        _api_error(
            503,
            code="planning_snapshot_invalid",
            message="The authoritative planning snapshot is inconsistent.",
            retryable=True,
        )

    def rate(count: int) -> Decimal:
        return Decimal(count) / Decimal(scope) if scope else Decimal("0")

    tat_status: Literal["available", "partial", "unavailable"] = (
        "unavailable"
        if repair_model_keys == 0
        else "available"
        if repair_model_keys == scope and missing_frontiers == 0
        else "partial"
    )
    return PlanningCoverage(
        scope_key_count=scope,
        authoritative_key_count=scope,
        eligible_key_count=served_keys,
        missing_candidate_frontier_key_count=missing_frontiers,
        criticality_unknown_key_count=criticality_unknown,
        candidate_menu_key_count=served_keys,
        candidate_count=candidate_count,
        feasible_candidate_count=feasible_count,
        candidate_menu_coverage_rate=rate(served_keys),
        repair_model_key_count=repair_model_keys,
        repair_model_coverage_rate=rate(repair_model_keys),
        repair_credit_key_count=repair_credit_keys,
        repair_credit_coverage_rate=rate(repair_credit_keys),
        low_confidence_key_count=low_confidence_keys,
        minimum_candidate_confidence=min(confidences) if confidences else None,
        tat_confidence_status=tat_status,
        disclosure=(
            "Coverage is derived from the authoritative tenant key universe "
            "and immutable candidate menus captured at submission. Missing "
            "candidate frontiers remain explicit exclusions. Repair credit "
            "is counted only when repair-return evidence is present."
        ),
    )


def _persisted_coverage(run: PlanningRunRecord) -> PlanningCoverage | None:
    raw = getattr(run, "coverage", None)
    if not isinstance(raw, dict):
        detail = getattr(run, "detail", {})
        raw = detail.get("coverage") if isinstance(detail, dict) else None
    if not isinstance(raw, dict):
        return None
    total = raw.get(
        "authoritative_key_count",
        raw.get("total_key_count", raw.get("scope_key_count")),
    )
    eligible = raw.get(
        "eligible_key_count",
        raw.get(
            "returned_key_count",
            raw.get("optimized_key_count", raw.get("candidate_menu_key_count")),
        ),
    )
    missing = raw.get(
        "missing_candidate_frontier_key_count",
        raw.get(
            "missing_frontier_key_count",
            total - eligible
            if isinstance(total, int) and isinstance(eligible, int)
            else None,
        ),
    )
    criticality_unknown = raw.get("criticality_unknown_key_count", 0)
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
        or not isinstance(eligible, int)
        or isinstance(eligible, bool)
        or eligible < 0
        or not isinstance(missing, int)
        or isinstance(missing, bool)
        or missing < 0
        or total != eligible + missing
        or not isinstance(criticality_unknown, int)
        or isinstance(criticality_unknown, bool)
        or criticality_unknown < 0
    ):
        return None

    def rate(count: object) -> Decimal | None:
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return None
        return Decimal(count) / Decimal(total) if total else Decimal("0")

    allowed = {
        "scope_key_count": total,
        "authoritative_key_count": total,
        "eligible_key_count": eligible,
        "missing_candidate_frontier_key_count": missing,
        "criticality_unknown_key_count": criticality_unknown,
        "candidate_menu_key_count": eligible,
        "candidate_count": raw.get("candidate_count"),
        "feasible_candidate_count": raw.get("feasible_candidate_count"),
        "candidate_menu_coverage_rate": rate(eligible),
        "repair_model_key_count": raw.get("repair_model_key_count"),
        "repair_model_coverage_rate": rate(raw.get("repair_model_key_count")),
        "repair_credit_key_count": raw.get("repair_credit_key_count"),
        "repair_credit_coverage_rate": rate(raw.get("repair_credit_key_count")),
        "low_confidence_key_count": raw.get("low_confidence_key_count"),
        "minimum_candidate_confidence": raw.get(
            "minimum_candidate_confidence"
        ),
        "tat_confidence_status": raw.get("tat_confidence_status"),
        "disclosure": (
            "Coverage is derived from the authoritative tenant key universe "
            "and immutable candidate menus captured at submission. Missing "
            "candidate frontiers remain explicit exclusions. Repair credit "
            "is counted only when repair-return evidence is present."
        ),
    }
    try:
        return PlanningCoverage.model_validate(allowed)
    except Exception:  # noqa: BLE001 - legacy coverage is additive evidence
        return None


def _scope_summary(
    run: PlanningRunRecord,
    *,
    scope_kind_override: Literal["explicit", "all_eligible"] | None = None,
) -> PlanningScopeSummary:
    raw_kind = scope_kind_override or getattr(run, "scope_kind", "explicit")
    kind: Literal["explicit", "all_eligible"] = (
        raw_kind if raw_kind in {"explicit", "all_eligible"} else "explicit"
    )
    raw_preview = getattr(run, "scope_preview", None)
    if not isinstance(raw_preview, (tuple, list)):
        raw_preview = getattr(run, "explicit_scope", ())
    preview = tuple(
        value
        for value in raw_preview[:20]
        if isinstance(value, str) and 0 < len(value) <= 200
    )
    return PlanningScopeSummary(
        kind=kind,
        key_count=max(int(run.key_count), 0),
        preview_keys=preview,
        preview_truncated=int(run.key_count) > len(preview),
    )


_EVIDENCE_CODE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_MAX_EVIDENCE_SCAN = 1_000
_MAX_EVIDENCE_CODES = 50


def _evidence_code(value: object) -> str:
    candidate: object = value
    if isinstance(value, dict):
        candidate = next(
            (
                value.get(field)
                for field in ("code", "reason_code", "kind")
                if value.get(field) is not None
            ),
            None,
        )
    normalized = str(candidate or "").strip().lower()
    return normalized if _EVIDENCE_CODE.fullmatch(normalized) else "unspecified"


def _evidence_summary(
    values: object,
    *,
    total_override: int | None = None,
) -> PlanningEvidenceSummary:
    if not isinstance(values, (tuple, list)):
        return PlanningEvidenceSummary(
            total=max(total_override or 0, 0),
            counted_items=0,
            by_code=(),
            code_list_truncated=bool(total_override),
        )
    declared_total = (
        max(total_override, 0)
        if isinstance(total_override, int) and not isinstance(total_override, bool)
        else len(values)
    )
    scanned_values = values[:_MAX_EVIDENCE_SCAN]
    counts: Counter[str] = Counter()
    for value in scanned_values:
        weight = (
            value.get("count", 1)
            if isinstance(value, dict)
            else 1
        )
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 1:
            weight = 1
        counts[_evidence_code(value)] += weight
    counted_items = sum(counts.values())
    total = max(declared_total, counted_items)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    included = ordered[:_MAX_EVIDENCE_CODES]
    return PlanningEvidenceSummary(
        total=total,
        counted_items=counted_items,
        by_code=tuple(
            PlanningEvidenceCount(code=code, count=count)
            for code, count in included
        ),
        code_list_truncated=(
            len(scanned_values) < len(values)
            or counted_items < total
            or len(ordered) > len(included)
        ),
    )


def _with_input_exclusions(
    summary: PlanningEvidenceSummary,
    missing_frontier_count: int,
) -> PlanningEvidenceSummary:
    if missing_frontier_count <= 0:
        return summary
    counts = Counter({item.code: item.count for item in summary.by_code})
    counts["missing_candidate_frontier"] = max(
        counts["missing_candidate_frontier"],
        missing_frontier_count,
    )
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    included = ordered[:_MAX_EVIDENCE_CODES]
    total = max(summary.total, missing_frontier_count)
    return PlanningEvidenceSummary(
        total=total,
        counted_items=min(
            total,
            max(summary.counted_items, missing_frontier_count),
        ),
        by_code=tuple(
            PlanningEvidenceCount(code=code, count=count)
            for code, count in included
        ),
        code_list_truncated=(
            len(ordered) > len(included)
            or sum(counts.values()) < total
        ),
    )


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except Exception:  # noqa: BLE001 - legacy evidence remains unavailable
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def _bounded_string_sample(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        item
        for item in value[:20]
        if isinstance(item, str) and 0 < len(item) <= 200
    )


def _infeasibility(run: PlanningRunRecord) -> PlanningInfeasibilitySummary | None:
    if run.status != "infeasible":
        return None
    raw = getattr(run, "infeasibility", None)
    if not isinstance(raw, dict):
        result = getattr(run, "result", None)
        raw = result if isinstance(result, dict) else {}
    raw_keys = raw.get("infeasible_keys", raw.get("infeasible_key_sample", ()))
    raw_floors = raw.get(
        "infeasible_floor_ids",
        raw.get("infeasible_floor_sample", ()),
    )
    key_count = raw.get("infeasible_key_count")
    floor_count = raw.get("infeasible_floor_count")
    return PlanningInfeasibilitySummary(
        minimum_budget_required=_decimal_or_none(
            raw.get("minimum_budget_required")
        ),
        budget_shortfall=_decimal_or_none(raw.get("budget_shortfall")),
        infeasible_key_count=(
            max(key_count, 0)
            if isinstance(key_count, int) and not isinstance(key_count, bool)
            else len(raw_keys)
            if isinstance(raw_keys, (list, tuple))
            else 0
        ),
        infeasible_key_sample=_bounded_string_sample(raw_keys),
        infeasible_floor_count=(
            max(floor_count, 0)
            if isinstance(floor_count, int) and not isinstance(floor_count, bool)
            else len(raw_floors)
            if isinstance(raw_floors, (list, tuple))
            else 0
        ),
        infeasible_floor_sample=_bounded_string_sample(raw_floors),
    )


def _safe_detail(run: PlanningRunRecord) -> PlanningRunSafeDetail:
    raw = getattr(run, "detail", {})
    raw = raw if isinstance(raw, dict) else {}
    raw_code = raw.get("error_code")
    code = (
        str(raw_code).strip().lower()
        if isinstance(raw_code, str)
        and _EVIDENCE_CODE.fullmatch(str(raw_code).strip().lower())
        else None
    )
    if run.status == "failed" and code is None:
        code = "planning_run_failed"
    guidance = raw.get("guidance")
    if not isinstance(guidance, str) or not 0 < len(guidance) <= 300:
        guidance = (
            "Review the planning inputs and submit a new immutable run."
            if run.status == "failed"
            else None
        )

    def nonnegative_int(field: str) -> int | None:
        value = raw.get(field)
        return (
            value
            if isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
            else None
        )

    return PlanningRunSafeDetail(
        error_code=code,
        guidance=guidance,
        retryable=(
            raw.get("retryable")
            if isinstance(raw.get("retryable"), bool)
            else None
        ),
        failed_attempt=nonnegative_int("failed_attempt"),
        last_failed_attempt=nonnegative_int("last_failed_attempt"),
    )


def _safe_assumption_diff(run: PlanningRunRecord) -> tuple[dict[str, Any], ...]:
    raw = getattr(run, "assumption_diff", ())
    if not isinstance(raw, (tuple, list)):
        return ()
    safe: list[dict[str, Any]] = []
    for change in raw[:50]:
        if not isinstance(change, dict):
            continue
        item = {}
        for field in ("field", "before", "after"):
            value = change.get(field)
            if isinstance(value, str):
                item[field] = value[:500]
            elif value is None or isinstance(value, (int, float, bool)):
                item[field] = value
        safe.append(item)
    return tuple(safe)


def _view(
    run: PlanningRunRecord,
    *,
    current_generation_hash: str | None,
    current_snapshot_hash: str | None = None,
    current_snapshot_is_exact: bool = False,
    coverage_override: PlanningCoverage | None = None,
    scope_kind_override: Literal["explicit", "all_eligible"] | None = None,
) -> PlanningRunView:
    scope = _scope_summary(run, scope_kind_override=scope_kind_override)
    coverage = coverage_override or _persisted_coverage(run)
    comparable_snapshot_hash = (
        current_snapshot_hash
        if current_snapshot_is_exact or scope.kind == "all_eligible"
        else None
    )
    stale = (
        current_generation_hash != run.source_generation_hash
        if current_generation_hash is not None
        else None
    )
    return PlanningRunView(
        run_id=run.run_id,
        planning_fingerprint=run.planning_fingerprint,
        contract_version=run.contract_version,
        parent_run_id=run.parent_run_id,
        parent_planning_fingerprint=run.parent_planning_fingerprint,
        parent_source_snapshot_hash=run.parent_source_snapshot_hash,
        assumption_diff=_safe_assumption_diff(run),
        status=run.status,
        source_snapshot_hash=run.source_snapshot_hash,
        source_generation_hash=run.source_generation_hash,
        scope=scope,
        key_count=max(run.key_count, 0),
        budget=run.budget,
        horizon_days=run.horizon_days,
        currency=run.currency,
        model_profile=run.model_profile,
        advisory_only=run.advisory_only,
        progress_completed=max(run.progress_completed, 0),
        progress_total=max(run.progress_total, 0),
        summary=run.summary,
        infeasibility=_infeasibility(run),
        solver=run.solver,
        warnings=_evidence_summary(
            run.warnings,
            total_override=getattr(run, "warning_count", None),
        ),
        skipped_keys=_with_input_exclusions(
            _evidence_summary(
                run.skipped_keys,
                total_override=getattr(run, "skipped_key_count", None),
            ),
            (
                coverage.missing_candidate_frontier_key_count
                if coverage is not None and scope.kind == "all_eligible"
                else 0
            ),
        ),
        detail=_safe_detail(run),
        submitted_by=run.submitted_by,
        attempts=max(run.attempts, 0),
        claimed_at=run.claimed_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        coverage=coverage,
        stale=stale,
        current_source_snapshot_hash=comparable_snapshot_hash,
        current_source_generation_hash=current_generation_hash,
        stale_reason=(
            "Newer candidate inputs are available; this run remains "
            "reproducible from its immutable submitted snapshot."
            if stale
            else None
        ),
    )


def _observe_run(request: Request, run: PlanningRunView) -> None:
    telemetry = getattr(request.app.state, "planning_telemetry", None)
    if telemetry is None:
        return
    solver_termination = (
        str(run.solver.get("termination"))
        if run.solver and run.solver.get("termination")
        else None
    )
    telemetry.observe_run(
        status=run.status,
        stale=run.stale,
        solver_termination=solver_termination,
    )


@router.post(PLANNING_BASE, status_code=201)
def create_planning_run(
    tenant_id: str,
    body: CreatePlanningRunRequest,
    request: Request,
) -> PlanningRunSubmissionView:
    claims = _claims(request)
    _require_feature(request, tenant_id)
    _require_planner(claims)
    planner_store = _planner_store(request, tenant_id)
    planning_store = _planning_store(request, tenant_id, claims)
    try:
        solve_request, input_snapshot = _build_request(
            tenant_id=tenant_id,
            body=body,
            planner_store=planner_store,
        )
    except HTTPException:
        raise
    except ValueError:
        _api_error(
            422,
            code="planning_request_incompatible",
            message=(
                "The selected candidate menus do not form one compatible "
                "planning request."
            ),
        )
    except Exception:  # noqa: BLE001 - redact candidate/model internals
        _api_error(
            503,
            code="planning_input_unavailable",
            message="Authoritative planning inputs are temporarily unavailable.",
            retryable=True,
        )
    try:
        submission: PlanningRunSubmission = planning_store.submit(
            solve_request,
            parent_run_id=(
                str(body.parent_run_id)
                if body.parent_run_id is not None
                else None
            ),
            scope_kind=body.scope_kind,
            input_coverage=input_snapshot.coverage,
            source_generation_hash=input_snapshot.source_generation_hash,
            rerun_mandatory_floors={
                decision_key: tuple(
                    floor.to_domain() for floor in floors
                )
                for decision_key, floors in body.mandatory_floors.items()
            },
        )
    except ValueError:
        _api_error(
            409,
            code="planning_submission_conflict",
            message=(
                "The immutable planning run conflicts with existing lineage "
                "or tenant-scoped submission state."
            ),
        )
    except Exception:  # noqa: BLE001 - redact persistence/driver internals
        _api_error(
            503,
            code="planning_submission_unavailable",
            message="The planning run could not be submitted safely.",
            retryable=True,
        )
    view = _view(
        submission.run,
        current_generation_hash=input_snapshot.source_generation_hash,
        current_snapshot_hash=solve_request.source_snapshot_hash,
        current_snapshot_is_exact=True,
        coverage_override=_coverage_from_request(
            solve_request,
            input_snapshot.coverage,
        ),
        scope_kind_override=body.scope_kind,
    )
    _observe_run(request, view)
    telemetry = getattr(request.app.state, "planning_telemetry", None)
    if telemetry is not None:
        telemetry.observe_submission(created=submission.created)
    return PlanningRunSubmissionView(
        run=view,
        created=submission.created,
    )


@router.get(PLANNING_BASE + "/capabilities")
def planning_capabilities(
    tenant_id: str,
    request: Request,
) -> PlanningCapability:
    claims = _claims(request)
    enabled = advisory_enabled(request, tenant_id)
    planner = claims.get("tenant_role") in _PLANNING_ROLES
    return PlanningCapability(
        enabled=enabled,
        can_read=enabled,
        can_submit=enabled and planner,
        reason_code=(
            "feature_disabled"
            if not enabled
            else "enabled"
            if planner
            else "insufficient_role"
        ),
    )


@router.get(PLANNING_BASE)
def list_planning_runs(
    tenant_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> tuple[PlanningRunView, ...]:
    claims = _claims(request)
    _require_feature(request, tenant_id)
    planner_store = _planner_store(request, tenant_id)
    planning_store = _planning_store(request, tenant_id, claims)
    try:
        runs = planning_store.list_recent(limit=limit)
    except Exception:  # noqa: BLE001 - redact persistence/driver internals
        _api_error(
            503,
            code="planning_history_unavailable",
            message="Planning history is temporarily unavailable.",
            retryable=True,
        )
    try:
        current_generation_hash = _current_generation_hash(planner_store)
        current_snapshot_hash = _current_snapshot_hash(planner_store)
    except Exception:  # noqa: BLE001 - staleness is additive read evidence
        current_generation_hash = None
        current_snapshot_hash = None
    views = tuple(
        _view(
            run,
            current_generation_hash=current_generation_hash,
            current_snapshot_hash=current_snapshot_hash,
        )
        for run in runs
    )
    for view in views:
        _observe_run(request, view)
    return views


@router.get(PLANNING_BASE + "/{run_id}/rerun-config")
def get_planning_rerun_config(
    tenant_id: str,
    run_id: UUID,
    request: Request,
) -> PlanningRerunConfigView:
    run_id_text = str(run_id)
    claims = _claims(request)
    _require_feature(request, tenant_id)
    planner_store = _planner_store(request, tenant_id)
    planning_store = _planning_store(request, tenant_id, claims)
    try:
        parent = planning_store.get(run_id_text)
    except Exception:  # noqa: BLE001 - redact persistence/driver internals
        _api_error(
            503,
            code="planning_rerun_config_unavailable",
            message="The saved planning configuration is temporarily unavailable.",
            retryable=True,
        )
    if parent is None:
        _api_error(
            404,
            code="planning_run_not_found",
            message="The requested planning run was not found.",
        )
    if parent.status not in {"completed", "infeasible", "failed"}:
        _api_error(
            409,
            code="planning_rerun_parent_not_terminal",
            message="Only a terminal planning run can seed a saved rerun.",
        )
    reader = getattr(planning_store, "rerun_config", None)
    if not callable(reader):
        _api_error(
            503,
            code="planning_rerun_config_unavailable",
            message="The saved planning configuration is temporarily unavailable.",
            retryable=True,
        )
    try:
        config = reader(run_id_text)
        if config is None:
            _api_error(
                404,
                code="planning_run_not_found",
                message="The requested planning run was not found.",
            )
        if not isinstance(config, PlanningRerunConfig):
            config = PlanningRerunConfig.model_validate(config)
        return _rerun_config_view(config, planner_store=planner_store)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - redact stored/model internals
        _api_error(
            503,
            code="planning_rerun_config_unavailable",
            message="The saved planning configuration is temporarily unavailable.",
            retryable=True,
        )


@router.get(PLANNING_BASE + "/{run_id}")
def get_planning_run(
    tenant_id: str,
    run_id: UUID,
    request: Request,
) -> PlanningRunView:
    run_id_text = str(run_id)
    claims = _claims(request)
    _require_feature(request, tenant_id)
    planner_store = _planner_store(request, tenant_id)
    planning_store = _planning_store(request, tenant_id, claims)
    try:
        run = planning_store.get(run_id_text)
    except Exception:  # noqa: BLE001 - redact persistence/driver internals
        _api_error(
            503,
            code="planning_run_unavailable",
            message="The planning run is temporarily unavailable.",
            retryable=True,
        )
    if run is None:
        _api_error(
            404,
            code="planning_run_not_found",
            message="The requested planning run was not found.",
        )
    try:
        current_generation_hash = _current_generation_hash(planner_store)
        current_snapshot_hash = _current_snapshot_hash(planner_store)
    except Exception:  # noqa: BLE001 - staleness is additive read evidence
        current_generation_hash = None
        current_snapshot_hash = None
    view = _view(
        run,
        current_generation_hash=current_generation_hash,
        current_snapshot_hash=current_snapshot_hash,
    )
    _observe_run(request, view)
    return view


@router.get(PLANNING_BASE + "/{run_id}/selections")
def get_planning_run_selections(
    tenant_id: str,
    run_id: UUID,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(
        default=0,
        ge=0,
        le=MAX_PLANNING_SELECTION_OFFSET,
    ),
    decision_key: str | None = Query(
        default=None,
        min_length=1,
        max_length=257,
    ),
    selected_is_no_change: bool | None = Query(default=None),
) -> PlanningRunSelectionsPage:
    run_id_text = str(run_id)
    claims = _claims(request)
    _require_feature(request, tenant_id)
    planning_store = _planning_store(request, tenant_id, claims)
    try:
        run = planning_store.get(run_id_text)
        if run is None:
            page_rows: tuple[PlanningRunSelectionRecord, ...] = ()
            total = 0
        else:
            page_reader = getattr(planning_store, "selection_page", None)
            if not callable(page_reader):
                _api_error(
                    503,
                    code="planning_selection_paging_unavailable",
                    message="Paged planning selections are temporarily unavailable.",
                    retryable=True,
                )
            page_rows, total = page_reader(
                run_id_text,
                limit=limit,
                offset=offset,
                decision_key=decision_key,
                selected_is_no_change=selected_is_no_change,
            )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - redact persistence/driver internals
        _api_error(
            503,
            code="planning_selections_unavailable",
            message="Planning selections are temporarily unavailable.",
            retryable=True,
        )
    if run is None:
        _api_error(
            404,
            code="planning_run_not_found",
            message="The requested planning run was not found.",
        )
    return PlanningRunSelectionsPage(
        items=tuple(page_rows),
        total=total,
        limit=limit,
        offset=offset,
    )


__all__ = [
    "CreatePlanningRunRequest",
    "PlanningRunSubmissionView",
    "PlanningRunSelectionsPage",
    "PlanningCapability",
    "PlanningCoverage",
    "PlanningRunView",
    "PlanningScopeKey",
    "router",
]
