"""Tenant-scoped asynchronous historical replay and shadow-scorecard routes.

The route surface is intentionally narrow: submit immutable replay evidence,
list runs, and retrieve one run. There is no mutation, approval, commit, or
writeback endpoint for a replay result.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Never

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from trax_io_reco.contracts.replay import (
    ReplayEvaluationRequest,
    ReplayExclusionCount,
    ReplayMetricDefinition,
    ReplayMetricDelta,
    ReplayMetrics,
)

from trax_io_spine.bff.advisory_flags import advisory_enabled
from trax_io_spine.pg.replay import (
    PgReplayRunStore,
    ReplayCohortRecord,
    ReplayExclusionRecord,
    ReplayLineageRecord,
    ReplayRunRecord,
    ReplayUniverseRecord,
)

router = APIRouter()

REPLAY_BASE = "/v1/tenants/{tenant_id}/replay-runs"
_PLANNING_ROLES = frozenset({"planner", "admin", "owner"})
_Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class CreateReplayRunRequest(BaseModel):
    """Bounded browser input; historical facts stay behind the trusted resolver."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    universe_ref: str = Field(min_length=1, max_length=256)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    current_policy_label: str = Field(min_length=1, max_length=120)
    challenger_policy_label: str = Field(min_length=1, max_length=120)
    comparison_rule: Literal["matched_budget", "matched_service"]
    match_tolerance: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        max_digits=18,
        decimal_places=12,
    )


class ReplayCapability(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["replay-capability.v1"] = "replay-capability.v1"
    enabled: bool
    advisory_only: Literal[True] = True
    can_read: bool
    can_submit: bool
    reason_code: Literal[
        "enabled",
        "feature_disabled",
        "insufficient_role",
    ]


class ReplayScorecardHeader(BaseModel):
    """Bounded aggregate scorecard; row evidence is served by page resources."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["replay.v1"]
    tenant_id: str
    currency: str
    universe_id: str
    universe_sha256: _Sha256
    current_policy_label: str
    challenger_policy_label: str
    comparison_rule: Literal["matched_budget", "matched_service"]
    comparison_rule_definition: str
    match_tolerance: Decimal = Field(ge=0)
    advisory_only: Literal[True]
    observation_count: int = Field(ge=0)
    total_observation_count: int = Field(ge=1)
    excluded_observation_count: int = Field(ge=0)
    coverage_rate: Decimal = Field(ge=0, le=1)
    exclusions_by_reason: tuple[ReplayExclusionCount, ...]
    current: ReplayMetrics
    challenger: ReplayMetrics
    delta: ReplayMetricDelta
    metric_definitions: tuple[ReplayMetricDefinition, ...]
    universe_decision_count: int = Field(ge=0)
    cohort_count: int = Field(ge=0)
    lineage_count: int = Field(ge=0)
    source_snapshot_hash_count: int = Field(ge=0)
    planning_fingerprint_count: int = Field(ge=0)
    universe_decisions_sha256: _Sha256
    exclusions_sha256: _Sha256
    observation_lineage_sha256: _Sha256
    cohorts_sha256: _Sha256
    source_snapshot_hashes_sha256: _Sha256
    planning_fingerprints_sha256: _Sha256


class ReplayReviewPackageHeader(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    input_sha256: _Sha256
    universe_sha256: _Sha256
    trusted_input_sha256: _Sha256
    lineage_count: int = Field(ge=0)
    exclusion_count: int = Field(ge=0)
    cohort_count: int = Field(ge=0)


class ReplayRunDetailView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    advisory_only: bool | None = None
    writeback_capability: Literal["none"] | None = None
    comparison_rule_definition: str | None = None
    review_package: ReplayReviewPackageHeader | None = None
    error_code: str | None = None
    guidance: str | None = None
    failed_attempt: int | None = Field(default=None, ge=0)
    retryable: bool | None = None


class ReplayRunView(BaseModel):
    """Public replay header with normalized row collections kept out-of-line."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    replay_id: str
    replay_fingerprint: str
    input_sha256: _Sha256
    contract_version: Literal["replay.v1"]
    status: Literal["queued", "running", "completed", "failed"]
    universe_ref: str
    universe_id: str
    universe_sha256: _Sha256
    comparison_rule: Literal["matched_budget", "matched_service"]
    expected_decision_count: int = Field(ge=1)
    advisory_only: Literal[True]
    scorecard: ReplayScorecardHeader | None
    coverage_rate: Decimal | None = Field(default=None, ge=0, le=1)
    detail: ReplayRunDetailView
    submitted_by: str
    attempts: int = Field(ge=0)
    claimed_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReplayRunSubmissionView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run: ReplayRunView
    created: bool


class ReplayLineagePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ReplayLineageRecord, ...] = Field(max_length=100)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ReplayExclusionPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ReplayExclusionRecord, ...] = Field(max_length=100)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ReplayCohortPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ReplayCohortRecord, ...] = Field(max_length=100)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ReplayUniverseMetadata(BaseModel):
    """Opaque trusted-universe metadata without historical facts or secret digests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    universe_ref: str = Field(min_length=1, max_length=256)
    universe_id: str = Field(min_length=1, max_length=256)
    universe_sha256: _Sha256
    contract_version: str = Field(min_length=1, max_length=64)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    expected_decision_count: int = Field(ge=1)
    observation_count: int = Field(ge=0)
    exclusion_count: int = Field(ge=0)
    created_at: datetime


class ReplayUniversePage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    items: tuple[ReplayUniverseMetadata, ...] = Field(max_length=100)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


def _safe_error(
    status_code: int,
    *,
    code: str,
    message: str,
    retryable: bool = False,
) -> None:
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
        _safe_error(
            401,
            code="replay_auth_required",
            message="Verified authentication is required.",
        )
    return claims


def _require_planner(claims: dict) -> None:
    if claims.get("tenant_role") not in _PLANNING_ROLES:
        _safe_error(
            403,
            code="replay_role_required",
            message="A planner, admin, or owner role is required.",
        )


def _require_feature(request: Request, tenant_id: str) -> None:
    if not advisory_enabled(request, tenant_id):
        _safe_error(
            404,
            code="replay_feature_disabled",
            message="Historical replay is not enabled for this tenant.",
        )


def _store(request: Request, tenant_id: str, claims: dict):
    stores = getattr(request.app.state, "replay_stores", {})
    configured = stores.get(tenant_id)
    if configured is not None:
        if callable(configured):
            return configured(
                principal=claims["sub"],
                role=claims.get("tenant_role", "viewer"),
            )
        return configured
    registry = getattr(request.app.state, "registry", None)
    if registry is not None and hasattr(registry, "replay_store_for"):
        resolved = registry.replay_store_for(
            tenant_id,
            principal=claims["sub"],
            role=claims.get("tenant_role", "viewer"),
        )
        if resolved is not None:
            return resolved
    _safe_error(
        503,
        code="replay_store_unavailable",
        message="Historical replay is temporarily unavailable.",
        retryable=True,
    )


def _trusted_request(
    request: Request,
    tenant_id: str,
    claims: dict,
    body: CreateReplayRunRequest,
    store,
) -> ReplayEvaluationRequest:
    resolvers = getattr(request.app.state, "replay_universe_resolvers", {})
    resolver = resolvers.get(tenant_id) if isinstance(resolvers, dict) else None
    if resolver is None:
        resolver = getattr(store, "resolve_request", None)
    if resolver is None:
        _safe_error(
            503,
            code="replay_universe_resolver_unavailable",
            message="The trusted historical universe resolver is unavailable.",
            retryable=True,
        )
    try:
        resolved = resolver(
            body,
            principal=claims["sub"],
            role=claims.get("tenant_role", "viewer"),
        )
    except (LookupError, ValueError):
        _safe_error(
            422,
            code="replay_universe_invalid",
            message="The trusted historical universe reference is invalid or unavailable.",
        )
    except Exception:  # noqa: BLE001 - redact resolver/storage internals
        _safe_error(
            503,
            code="replay_universe_resolver_unavailable",
            message="The trusted historical universe resolver is unavailable.",
            retryable=True,
        )
    if not isinstance(resolved, ReplayEvaluationRequest):
        _safe_error(
            503,
            code="replay_universe_contract_invalid",
            message="The trusted historical universe could not be validated.",
        )
    if resolved.tenant_id != tenant_id:
        _safe_error(
            422,
            code="replay_universe_invalid",
            message="The trusted historical universe reference is invalid or unavailable.",
        )
    return resolved


def _safe_detail(value: object) -> ReplayRunDetailView:
    raw = value if isinstance(value, dict) else {}
    review = raw.get("review_package")
    safe_review = (
        ReplayReviewPackageHeader.model_validate(review)
        if isinstance(review, dict)
        else None
    )
    return ReplayRunDetailView(
        advisory_only=(
            raw.get("advisory_only")
            if isinstance(raw.get("advisory_only"), bool)
            else None
        ),
        writeback_capability=(
            "none" if raw.get("writeback_capability") == "none" else None
        ),
        comparison_rule_definition=(
            str(raw["comparison_rule_definition"])[:1_000]
            if isinstance(raw.get("comparison_rule_definition"), str)
            else None
        ),
        review_package=safe_review,
        error_code=(
            str(raw["error_code"])[:120]
            if isinstance(raw.get("error_code"), str)
            else None
        ),
        guidance=(
            str(raw["guidance"])[:1_000]
            if isinstance(raw.get("guidance"), str)
            else None
        ),
        failed_attempt=(
            raw["failed_attempt"]
            if isinstance(raw.get("failed_attempt"), int)
            and not isinstance(raw.get("failed_attempt"), bool)
            and raw["failed_attempt"] >= 0
            else None
        ),
        retryable=(
            raw.get("retryable")
            if isinstance(raw.get("retryable"), bool)
            else None
        ),
    )


def _view(run: ReplayRunRecord | Mapping[str, object]) -> ReplayRunView:
    record = (
        run
        if isinstance(run, ReplayRunRecord)
        else ReplayRunRecord.model_validate(run)
    )
    scorecard = (
        ReplayScorecardHeader.model_validate(record.scorecard)
        if record.scorecard is not None
        else None
    )
    return ReplayRunView(
        replay_id=record.replay_id,
        replay_fingerprint=record.replay_fingerprint,
        input_sha256=record.input_sha256,
        contract_version=record.contract_version,
        status=record.status,
        universe_ref=record.universe_ref,
        universe_id=record.universe_id,
        universe_sha256=record.universe_sha256,
        comparison_rule=record.comparison_rule,
        expected_decision_count=record.expected_decision_count,
        advisory_only=True,
        scorecard=scorecard,
        coverage_rate=record.coverage_rate,
        detail=_safe_detail(record.detail),
        submitted_by=record.submitted_by,
        attempts=max(record.attempts, 0),
        claimed_at=record.claimed_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _submit_replay(
    *,
    request: Request,
    tenant_id: str,
    claims: dict,
    body: CreateReplayRunRequest,
    store,
):
    # The PostgreSQL store resolves and fingerprints the trusted universe in
    # the same transaction as submission. Test/in-memory stores retain the
    # explicit resolver preflight contract used by direct router tests.
    resolves_during_submit = isinstance(store, PgReplayRunStore)
    if not resolves_during_submit:
        _trusted_request(request, tenant_id, claims, body, store)
    try:
        return store.submit(
            body.universe_ref,
            currency=body.currency,
            current_policy_label=body.current_policy_label,
            challenger_policy_label=body.challenger_policy_label,
            comparison_rule=body.comparison_rule,
            match_tolerance=body.match_tolerance,
        )
    except (LookupError, ValueError):
        if resolves_during_submit:
            _safe_error(
                422,
                code="replay_universe_invalid",
                message=(
                    "The trusted historical universe reference is invalid "
                    "or unavailable."
                ),
            )
        _safe_error(
            409,
            code="replay_submission_conflict",
            message=(
                "The immutable replay conflicts with existing lineage or "
                "tenant-scoped submission state."
            ),
        )
    except Exception:  # noqa: BLE001 - redact persistence/driver internals
        _safe_error(
            503,
            code="replay_submission_unavailable",
            message="The replay run could not be submitted safely.",
            retryable=True,
        )


@router.post(REPLAY_BASE, status_code=status.HTTP_201_CREATED)
def create_replay_run(
    tenant_id: str,
    body: CreateReplayRunRequest,
    request: Request,
) -> ReplayRunSubmissionView:
    claims = _claims(request)
    _require_feature(request, tenant_id)
    _require_planner(claims)
    store = _store(request, tenant_id, claims)
    submission = _submit_replay(
        request=request,
        tenant_id=tenant_id,
        claims=claims,
        body=body,
        store=store,
    )
    return ReplayRunSubmissionView(
        run=_view(submission.run),
        created=submission.created,
    )


def _run_or_404(store, replay_id: str) -> ReplayRunRecord:
    run = store.get(replay_id)
    if run is None:
        _safe_error(
            404,
            code="replay_run_not_found",
            message="The requested replay run was not found.",
        )
    return run


def _universe_view(universe: ReplayUniverseRecord) -> ReplayUniverseMetadata:
    return ReplayUniverseMetadata(
        universe_ref=universe.universe_ref,
        universe_id=universe.universe_id,
        universe_sha256=universe.universe_sha256,
        contract_version=universe.contract_version,
        currency=universe.currency,
        expected_decision_count=universe.expected_decision_count,
        observation_count=universe.observation_count,
        exclusion_count=universe.exclusion_count,
        created_at=universe.created_at,
    )


def _page_error(code: str, message: str) -> Never:
    _safe_error(
        503,
        code=code,
        message=message,
        retryable=True,
    )


@router.get(REPLAY_BASE + "/{replay_id}/lineage")
def get_replay_lineage(
    tenant_id: str,
    replay_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    observation_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=256,
    ),
) -> ReplayLineagePage:
    claims = _claims(request)
    _require_feature(request, tenant_id)
    store = _store(request, tenant_id, claims)
    try:
        _run_or_404(store, replay_id)
        items, total = store.lineage_page(
            replay_id,
            limit=limit,
            offset=offset,
            observation_id=observation_id,
        )
    except HTTPException:
        raise
    except ValueError:
        _safe_error(
            422,
            code="replay_lineage_query_invalid",
            message="The replay lineage query is invalid.",
        )
    except Exception:  # noqa: BLE001 - redact persistence/driver internals
        _page_error(
            "replay_lineage_unavailable",
            "Replay lineage is temporarily unavailable.",
        )
    return ReplayLineagePage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(REPLAY_BASE + "/{replay_id}/exclusions")
def get_replay_exclusions(
    tenant_id: str,
    replay_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    observation_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=256,
    ),
    reason_code: str | None = Query(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9_]+$",
    ),
) -> ReplayExclusionPage:
    claims = _claims(request)
    _require_feature(request, tenant_id)
    store = _store(request, tenant_id, claims)
    try:
        _run_or_404(store, replay_id)
        items, total = store.exclusion_page(
            replay_id,
            limit=limit,
            offset=offset,
            observation_id=observation_id,
            reason_code=reason_code,
        )
    except HTTPException:
        raise
    except ValueError:
        _safe_error(
            422,
            code="replay_exclusion_query_invalid",
            message="The replay exclusion query is invalid.",
        )
    except Exception:  # noqa: BLE001 - redact persistence/driver internals
        _page_error(
            "replay_exclusions_unavailable",
            "Replay exclusions are temporarily unavailable.",
        )
    return ReplayExclusionPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(REPLAY_BASE + "/{replay_id}/cohorts")
def get_replay_cohorts(
    tenant_id: str,
    replay_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ReplayCohortPage:
    claims = _claims(request)
    _require_feature(request, tenant_id)
    store = _store(request, tenant_id, claims)
    try:
        _run_or_404(store, replay_id)
        items, total = store.cohort_page(
            replay_id,
            limit=limit,
            offset=offset,
        )
    except HTTPException:
        raise
    except ValueError:
        _safe_error(
            422,
            code="replay_cohort_query_invalid",
            message="The replay cohort query is invalid.",
        )
    except Exception:  # noqa: BLE001 - redact persistence/driver internals
        _page_error(
            "replay_cohorts_unavailable",
            "Replay cohorts are temporarily unavailable.",
        )
    return ReplayCohortPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(REPLAY_BASE + "/capabilities")
def replay_capabilities(
    tenant_id: str,
    request: Request,
) -> ReplayCapability:
    claims = _claims(request)
    enabled = advisory_enabled(request, tenant_id)
    planner = claims.get("tenant_role") in _PLANNING_ROLES
    return ReplayCapability(
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


@router.get(REPLAY_BASE + "/universes")
def list_replay_universes(
    tenant_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReplayUniversePage:
    claims = _claims(request)
    _require_feature(request, tenant_id)
    _require_planner(claims)
    store = _store(request, tenant_id, claims)
    reader = getattr(store, "list_universes", None)
    if not callable(reader):
        _safe_error(
            503,
            code="replay_universe_metadata_unavailable",
            message="Trusted replay universe metadata is temporarily unavailable.",
            retryable=True,
        )
    try:
        rows, total = reader(limit=limit, offset=offset)
        items = tuple(_universe_view(row) for row in rows)
    except Exception:  # noqa: BLE001 - redact storage and tenant internals
        _safe_error(
            503,
            code="replay_universe_metadata_unavailable",
            message="Trusted replay universe metadata is temporarily unavailable.",
            retryable=True,
        )
    return ReplayUniversePage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(REPLAY_BASE)
def list_replay_runs(
    tenant_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
) -> tuple[ReplayRunView, ...]:
    claims = _claims(request)
    _require_feature(request, tenant_id)
    try:
        runs = _store(request, tenant_id, claims).list_recent(limit=limit)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - redact persistence/driver internals
        _safe_error(
            503,
            code="replay_history_unavailable",
            message="Replay history is temporarily unavailable.",
            retryable=True,
        )
    return tuple(_view(run) for run in runs)


@router.get(REPLAY_BASE + "/{replay_id}")
def get_replay_run(
    tenant_id: str,
    replay_id: str,
    request: Request,
) -> ReplayRunView:
    claims = _claims(request)
    _require_feature(request, tenant_id)
    try:
        run = _store(request, tenant_id, claims).get(replay_id)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - redact persistence/driver internals
        _safe_error(
            503,
            code="replay_run_unavailable",
            message="The replay run is temporarily unavailable.",
            retryable=True,
        )
    if run is None:
        _safe_error(
            404,
            code="replay_run_not_found",
            message="The requested replay run was not found.",
        )
    return _view(run)


__all__ = [
    "CreateReplayRunRequest",
    "REPLAY_BASE",
    "ReplayCapability",
    "ReplayCohortPage",
    "ReplayExclusionPage",
    "ReplayLineagePage",
    "ReplayRunSubmissionView",
    "ReplayRunView",
    "ReplayScorecardHeader",
    "router",
]
