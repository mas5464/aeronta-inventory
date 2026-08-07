"""Tenant-scoped persistence for immutable advisory portfolio planning runs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import ExitStack
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from trax_io_reco.contracts.candidate import NonEmptyStr, NonNegativeDecimal
from trax_io_reco.contracts.planning import (
    MandatoryFloor,
    PortfolioKeyMenu,
    PortfolioSelection,
    PortfolioSolveRequest,
    PortfolioSolveResult,
    PortfolioSummary,
    SolverEvidence,
    TenantObjectiveWeights,
)
from trax_io_reco.contracts.planning_run import (
    PlanningAssumptionChange,
    PlanningSelectionDetail,
    PlanningWarning,
    _snapshot_matches,
)
from trax_io_reco.portfolio.identity import (
    planning_fingerprint,
    planning_menus_fingerprint,
)
from trax_io_reco.portfolio.optimizer import floor_states, objective_contribution
from trax_io_reco.portfolio.run import iter_planning_selection_details

from trax_io_spine.planning_inputs import planning_input_source_generation_hash

from .db import tenant_conn

PlanningRunStatus = Literal[
    "queued",
    "running",
    "completed",
    "infeasible",
    "failed",
]
PlanningScopeKind = Literal["explicit", "all_eligible"]
_DIFF_FIELDS = (
    "source_snapshot_hash",
    "source_generation_hash",
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
_HEADER_SAMPLE_LIMIT = 20
_HEADER_TEXT_LIMIT = 512

_RUN_COLUMNS = """
run_id::text, planning_fingerprint, contract_version, parent_run_id::text,
parent_planning_fingerprint, parent_source_snapshot_hash, assumption_diff,
status, scope_kind, scope_preview, source_snapshot_hash, explicit_scope,
source_generation_hash, key_count, menu_count, menus_fingerprint, candidate_count,
feasible_candidate_count, coverage, budget, horizon_days, currency,
model_profile, request, advisory_only, progress_completed, progress_total,
summary, result, detail, solver, warnings, skipped_keys, submitted_by,
warning_count, skipped_key_count, attempts, claimed_at, started_at,
finished_at, created_at, updated_at
"""

_SELECTION_COLUMNS = """
decision_key, current_candidate_id, selected_candidate_id,
selected_is_no_change, acquisition_cash, objective, selection, detail
"""
_SELECTION_COPY_SQL = """
copy planning_run_selections (
  tenant_id, run_id, decision_key, current_candidate_id,
  selected_candidate_id, selected_is_no_change, acquisition_cash,
  objective, selection, detail
) from stdin
"""


class _PlanningResultEnvelope(BaseModel):
    """Validate terminal result fields without materializing every selection."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    contract_version: Literal["planning.v1"] = "planning.v1"
    planning_fingerprint: str = Field(pattern=r"^planning_[0-9a-f]{64}$")
    tenant_id: NonEmptyStr
    status: Literal["completed", "infeasible", "failed"]
    summary: PortfolioSummary | None = None
    solver: SolverEvidence
    minimum_budget_required: NonNegativeDecimal | None = None
    budget_shortfall: NonNegativeDecimal | None = None
    infeasible_keys: tuple[NonEmptyStr, ...] = ()
    infeasible_floor_ids: tuple[NonEmptyStr, ...] = ()


class PlanningRunRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    planning_fingerprint: str
    contract_version: str
    parent_run_id: str | None
    parent_planning_fingerprint: str | None
    parent_source_snapshot_hash: str | None
    assumption_diff: tuple[dict[str, Any], ...]
    status: PlanningRunStatus
    scope_kind: PlanningScopeKind
    scope_preview: tuple[str, ...]
    source_snapshot_hash: str
    explicit_scope: tuple[str, ...]
    source_generation_hash: str
    key_count: int
    menu_count: int
    menus_fingerprint: str
    candidate_count: int
    feasible_candidate_count: int
    coverage: dict[str, Any]
    budget: Decimal
    horizon_days: int
    currency: str
    model_profile: dict[str, Any]
    request: dict[str, Any]
    advisory_only: bool
    progress_completed: int
    progress_total: int
    summary: dict[str, Any] | None
    result: dict[str, Any] | None
    detail: dict[str, Any]
    solver: dict[str, Any] | None
    warnings: tuple[dict[str, Any] | str, ...]
    skipped_keys: tuple[dict[str, Any] | str, ...]
    submitted_by: str
    warning_count: int
    skipped_key_count: int
    attempts: int
    claimed_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PlanningRunSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run: PlanningRunRecord
    created: bool


class PlanningRunSelectionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_key: str
    current_candidate_id: str
    selected_candidate_id: str
    selected_is_no_change: bool
    acquisition_cash: Decimal
    objective: Decimal
    selection: dict[str, Any]
    detail: dict[str, Any]


class PlanningRerunConfig(BaseModel):
    """Bounded browser-replayable assumptions from one immutable parent run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    scope_kind: PlanningScopeKind
    explicit_scope: tuple[str, ...]
    budget: Decimal
    horizon_days: int
    currency: str
    source_generation_hash: str
    model_profile: dict[str, Any]
    objective_weights: TenantObjectiveWeights
    mandatory_floors: dict[str, tuple[MandatoryFloor, ...]]
    time_limit_seconds: float


class PlanningRunWork(BaseModel):
    """Immutable optimizer input loaded by a claimed planning worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    planning_fingerprint: str
    request: PortfolioSolveRequest
    parent_run_id: str | None
    parent_planning_fingerprint: str | None
    parent_source_snapshot_hash: str | None
    assumption_diff: tuple[dict[str, Any], ...]


def _record(row: tuple[Any, ...]) -> PlanningRunRecord:
    return PlanningRunRecord.model_validate(
        dict(
            zip(
                PlanningRunRecord.model_fields,
                row,
                strict=True,
            )
        )
    )


def _selection_record(row: tuple[Any, ...]) -> PlanningRunSelectionRecord:
    return PlanningRunSelectionRecord.model_validate(
        dict(
            zip(
                PlanningRunSelectionRecord.model_fields,
                row,
                strict=True,
            )
        )
    )


def _menus_fingerprint(request: PortfolioSolveRequest) -> str:
    return planning_menus_fingerprint(request)


def _coverage_payload(
    request: PortfolioSolveRequest,
    *,
    input_coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_count = 0
    feasible_count = 0
    repair_model_keys = 0
    repair_credit_keys = 0
    low_confidence_keys = 0
    confidences: list[Decimal] = []
    repair_evidence_kinds = {
        "repair_credit",
        "repair_return_credit",
        "repair_return_profile",
    }
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
            str(evidence.kind).lower() in repair_evidence_kinds
            for candidate in candidates
            for evidence in candidate.evidence
        )
        low_confidence_keys += bool(
            key_confidences and min(key_confidences) < Decimal("0.5")
        )

    optimized_key_count = len(request.menus)

    if input_coverage is None:
        total_key_count = optimized_key_count
        missing_frontier_key_count = 0
        criticality_known_key_count = optimized_key_count
        criticality_unknown_key_count = 0
    else:
        normalized: dict[str, int] = {}
        for field in (
            "total_key_count",
            "returned_key_count",
            "eligible_key_count",
            "missing_frontier_key_count",
            "candidate_count",
            "feasible_candidate_count",
            "criticality_known_key_count",
            "criticality_unknown_key_count",
        ):
            value = input_coverage.get(field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(
                    f"planning input coverage {field} must be a non-negative integer"
                )
            normalized[field] = value
        total_key_count = normalized["total_key_count"]
        missing_frontier_key_count = normalized["missing_frontier_key_count"]
        criticality_known_key_count = normalized[
            "criticality_known_key_count"
        ]
        criticality_unknown_key_count = normalized[
            "criticality_unknown_key_count"
        ]
        if (
            total_key_count <= 0
            or normalized["returned_key_count"] != optimized_key_count
            or normalized["eligible_key_count"] != optimized_key_count
            or total_key_count
            != optimized_key_count + missing_frontier_key_count
            or normalized["candidate_count"] != candidate_count
            or normalized["feasible_candidate_count"] != feasible_count
            or criticality_known_key_count + criticality_unknown_key_count
            != total_key_count
        ):
            raise ValueError("planning input coverage does not reconcile to request menus")

    def _rate(count: int) -> str:
        return str(Decimal(count) / Decimal(total_key_count))

    return {
        "contract_version": "planning-coverage.v1",
        "scope_key_count": total_key_count,
        "optimized_key_count": optimized_key_count,
        "candidate_menu_key_count": optimized_key_count,
        "missing_candidate_frontier_key_count": missing_frontier_key_count,
        "skipped_key_count": missing_frontier_key_count,
        "skipped_reason_counts": {
            "missing_candidate_frontier": missing_frontier_key_count,
        },
        "candidate_count": candidate_count,
        "feasible_candidate_count": feasible_count,
        "candidate_menu_coverage_rate": _rate(optimized_key_count),
        "criticality_known_key_count": criticality_known_key_count,
        "criticality_unknown_key_count": criticality_unknown_key_count,
        "repair_model_key_count": repair_model_keys,
        "repair_model_coverage_rate": _rate(repair_model_keys),
        "repair_credit_key_count": repair_credit_keys,
        "repair_credit_coverage_rate": _rate(repair_credit_keys),
        "low_confidence_key_count": low_confidence_keys,
        "minimum_candidate_confidence": (
            str(min(confidences)) if confidences else None
        ),
        "tat_confidence_status": (
            "unavailable"
            if repair_model_keys == 0
            else "available"
            if repair_model_keys == total_key_count
            else "partial"
        ),
    }


def _request_payload(
    request: PortfolioSolveRequest,
    *,
    scope_kind: PlanningScopeKind,
    scope_preview: tuple[str, ...],
    menus_fingerprint: str,
    source_generation_hash: str,
    mandatory_floors: Mapping[str, tuple[MandatoryFloor, ...]],
) -> dict[str, Any]:
    payload = request.model_dump(mode="json", exclude={"menus"})
    floor_payload = {
        decision_key: [
            floor.model_dump(mode="json")
            for floor in floors
        ]
        for decision_key, floors in mandatory_floors.items()
    }
    payload.update(
        {
            "scope_kind": scope_kind,
            "scope_preview": list(scope_preview),
            "menu_count": len(request.menus),
            "menus_fingerprint": menus_fingerprint,
            "source_generation_hash": source_generation_hash,
            "mandatory_floors": floor_payload,
        }
    )
    return _deep_json_copy(payload)


def _bounded_rerun_mandatory_floors(
    request: PortfolioSolveRequest,
    *,
    scope_kind: PlanningScopeKind,
    supplied: Mapping[str, tuple[MandatoryFloor, ...]] | None,
) -> dict[str, tuple[MandatoryFloor, ...]]:
    """Retain only bounded browser-authored floors, never all system floors."""

    if supplied is None:
        if scope_kind == "all_eligible":
            return {}
        floors = {
            menu.frontier.decision_key: menu.mandatory_floors
            for menu in request.menus
            if menu.mandatory_floors
        }
    else:
        floors = {}
        for decision_key, raw_floors in supplied.items():
            if not isinstance(decision_key, str) or not decision_key:
                raise ValueError("planning mandatory floor key is invalid")
            floors[decision_key] = tuple(
                MandatoryFloor.model_validate(floor)
                for floor in raw_floors
            )
        requested_keys = set(floors)
        matched: dict[str, tuple[MandatoryFloor, ...]] = {}
        for menu in request.menus:
            decision_key = menu.frontier.decision_key
            if decision_key in requested_keys:
                matched[decision_key] = menu.mandatory_floors
        if matched != floors:
            raise ValueError(
                "planning rerun mandatory floors do not match immutable menus"
            )
    if len(floors) > 200 or any(len(items) > 20 for items in floors.values()):
        raise ValueError("planning mandatory floor config exceeds browser bounds")
    return floors


def _render_diff_value(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _bounded_assumption_diff(
    parent_config: Mapping[str, Any],
    rerun_config: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Diff immutable run inputs without embedding tens of thousands of menus."""

    if parent_config.get("tenant_id") != rerun_config.get("tenant_id"):
        raise ValueError("planning reruns cannot cross tenants")
    changes = [
        PlanningAssumptionChange(
            field=field_name,
            before=_render_diff_value(parent_config.get(field_name)),
            after=_render_diff_value(rerun_config.get(field_name)),
        )
        for field_name in _DIFF_FIELDS
        if parent_config.get(field_name) != rerun_config.get(field_name)
    ]
    if parent_config.get("menus_fingerprint") != rerun_config.get(
        "menus_fingerprint"
    ):
        changes.append(
            PlanningAssumptionChange(
                field="menus",
                before=_render_diff_value(
                    {
                        "menus_fingerprint": parent_config.get(
                            "menus_fingerprint"
                        ),
                        "menu_count": parent_config.get("menu_count"),
                    }
                ),
                after=_render_diff_value(
                    {
                        "menus_fingerprint": rerun_config.get(
                            "menus_fingerprint"
                        ),
                        "menu_count": rerun_config.get("menu_count"),
                    }
                ),
            )
        )
    return tuple(change.model_dump(mode="json") for change in changes)


def _deep_json_copy(value: Any) -> Any:
    """Detach persisted JSON from caller-owned mutable containers."""

    return json.loads(json.dumps(value, allow_nan=False))


def _model_profile(request: PortfolioSolveRequest) -> dict[str, Any]:
    return {
        "tenant_policy_version": request.tenant_policy_version,
        "forecast_version": request.forecast_version,
        "repair_model_version": request.repair_model_version,
        "candidate_planner_version": request.candidate_planner_version,
        "optimizer_version": request.optimizer_version,
        "objective_weights": request.objective_weights.model_dump(mode="json"),
        "time_limit_seconds": request.time_limit_seconds,
    }


def _json_object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return dict(value)


def _json_array(value: object, *, label: str) -> list[Any]:
    return list(_json_sequence(value, label=label))


def _json_sequence(
    value: object,
    *,
    label: str,
) -> list[Any] | tuple[Any, ...]:
    """Validate an existing JSON array without duplicating a large payload."""

    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _json_decimal(value: object, *, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite decimal")
    try:
        parsed = Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - normalize generic JSON failures
        raise ValueError(f"{label} must be a finite decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{label} must be a finite decimal")
    return parsed


def _json_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _bounded_text(value: object) -> str:
    return str(value)[:_HEADER_TEXT_LIMIT]


def _bounded_evidence_item(value: object) -> object:
    if isinstance(value, str):
        return _bounded_text(value)
    if not isinstance(value, Mapping):
        return _bounded_text(type(value).__name__)
    bounded = {}
    for key in ("code", "reason_code", "count", "decision_key", "detail"):
        if key not in value:
            continue
        item = value[key]
        bounded[key] = item if isinstance(item, (int, bool)) else _bounded_text(item)
    return bounded or {"code": "unclassified"}


def _bounded_terminal_result(
    result: Mapping[str, Any],
    *,
    solver: Mapping[str, Any],
    selection_count: int | None = None,
) -> dict[str, Any]:
    infeasible_keys = _json_sequence(
        result.get("infeasible_keys", []),
        label="planning infeasible keys",
    )
    infeasible_floor_ids = _json_sequence(
        result.get("infeasible_floor_ids", []),
        label="planning infeasible floor ids",
    )
    bounded_solver = dict(solver)
    if "message" in bounded_solver:
        bounded_solver["message"] = _bounded_text(bounded_solver["message"])
    return {
        "contract_version": result.get("contract_version"),
        "planning_fingerprint": result.get("planning_fingerprint"),
        "tenant_id": result.get("tenant_id"),
        "status": result.get("status"),
        "summary": result.get("summary"),
        "solver": bounded_solver,
        "minimum_budget_required": result.get("minimum_budget_required"),
        "budget_shortfall": result.get("budget_shortfall"),
        "selection_count": (
            selection_count
            if selection_count is not None
            else len(
                _json_sequence(
                    result.get("selections", []),
                    label="planning result selections",
                )
            )
        ),
        "infeasible_key_count": len(infeasible_keys),
        "infeasible_floor_count": len(infeasible_floor_ids),
        "infeasible_key_sample": [
            _bounded_text(value)
            for value in infeasible_keys[:_HEADER_SAMPLE_LIMIT]
        ],
        "infeasible_floor_sample": [
            _bounded_text(value)
            for value in infeasible_floor_ids[:_HEADER_SAMPLE_LIMIT]
        ],
    }


def load_planning_run_work(
    conn,
    *,
    tenant_uuid: str,
    run_id: str,
) -> PlanningRunWork:
    """Load only the immutable stored request used by the planning worker."""

    row = conn.execute(
        """
        select r.run_id::text, r.planning_fingerprint, r.request,
               r.menu_count, r.menus_fingerprint,
               r.parent_run_id::text, r.parent_planning_fingerprint,
               r.parent_source_snapshot_hash, r.assumption_diff
        from planning_runs r
        where r.tenant_id = %s::uuid and r.run_id = %s::uuid
          and r.status = 'running'
        """,
        (tenant_uuid, run_id),
    ).fetchone()
    if row is None:
        raise LookupError("claimed planning run is missing or not running")
    with conn.cursor(name="planning_run_menu_loader") as cursor:
        cursor.execute(
            """
            select menu
            from planning_run_menus
            where tenant_id = %s::uuid and run_id = %s::uuid
            order by ordinal
            """,
            (tenant_uuid, run_id),
        )
        menus = [
            PortfolioKeyMenu.model_validate(menu)
            for (menu,) in cursor
        ]
    if len(menus) != row[3]:
        raise ValueError("planning run menu count does not reconcile")
    request_payload = {
        field_name: row[2][field_name]
        for field_name in PortfolioSolveRequest.model_fields
        if field_name in row[2]
    }
    request_payload["menus"] = tuple(menus)
    request = PortfolioSolveRequest.model_validate(request_payload)
    if _menus_fingerprint(request) != row[4]:
        raise ValueError("planning run menus fingerprint does not reconcile")
    if planning_fingerprint(request) != row[1]:
        raise ValueError("planning run fingerprint does not reconcile")
    return PlanningRunWork(
        run_id=row[0],
        planning_fingerprint=row[1],
        request=request,
        parent_run_id=row[5],
        parent_planning_fingerprint=row[6],
        parent_source_snapshot_hash=row[7],
        assumption_diff=tuple(row[8]),
    )


def mark_planning_run_claimed(
    conn,
    *,
    tenant_uuid: str,
    run_id: str,
    attempts: int,
) -> None:
    """Move a run to running in the same transaction as its job claim."""

    if attempts < 1:
        raise ValueError("planning run attempts must be positive")
    row = conn.execute(
        """
        update planning_runs
        set status = 'running',
            attempts = %s,
            claimed_at = now(),
            started_at = coalesce(started_at, now())
        where tenant_id = %s::uuid and run_id = %s::uuid
          and status in ('queued', 'running')
          and planning_runs.attempts = %s - 1
          and (
            select count(*)
            from planning_run_menus m
            where m.tenant_id = planning_runs.tenant_id
              and m.run_id = planning_runs.run_id
          ) = planning_runs.menu_count
        returning run_id
        """,
        (attempts, tenant_uuid, run_id, attempts),
    ).fetchone()
    if row is None:
        raise LookupError("planning job does not reference a claimable run")


def mark_planning_run_retry(
    conn,
    *,
    tenant_uuid: str,
    run_id: str,
    attempts: int,
    error: str,
) -> None:
    """Requeue an operationally failed attempt with stable error evidence."""

    del error  # raw infrastructure text must not enter the tenant-visible record
    detail = {
        "error_code": "planning_worker_attempt_failed",
        "failed_attempt": attempts,
        "retryable": True,
    }
    row = conn.execute(
        """
        update planning_runs
        set status = 'queued',
            attempts = %s,
            claimed_at = null,
            detail = %s::jsonb
        where tenant_id = %s::uuid and run_id = %s::uuid
          and status = 'running'
          and attempts = %s
        returning run_id
        """,
        (attempts, json.dumps(detail), tenant_uuid, run_id, attempts),
    ).fetchone()
    if row is None:
        raise LookupError("planning run is not retryable")


def mark_planning_run_failed(
    conn,
    *,
    tenant_uuid: str,
    run_id: str,
    attempts: int,
    error: str,
) -> None:
    """Persist a terminal operational failure without actionable output."""

    interrupted = error.startswith("planning worker lease expired")
    detail = {
        "error_code": (
            "planning_worker_interrupted"
            if interrupted
            else "planning_worker_failed"
        ),
        "failed_attempt": attempts,
        "retryable": False,
        "guidance": (
            "Submit a new immutable planning run after verifying worker health."
            if interrupted
            else "Review the planning inputs and retry as a new immutable run."
        ),
    }
    row = conn.execute(
        """
        update planning_runs
        set status = 'failed',
            attempts = %s,
            detail = %s::jsonb,
            finished_at = now()
        where tenant_id = %s::uuid and run_id = %s::uuid
          and status = 'running'
          and attempts = %s
        returning run_id
        """,
        (attempts, json.dumps(detail), tenant_uuid, run_id, attempts),
    ).fetchone()
    if row is None:
        raise LookupError("planning run is not fail-able")


def persist_planning_result(
    conn,
    *,
    tenant_uuid: str,
    run_id: str,
    attempts: int,
    result: Mapping[str, Any] | PortfolioSolveResult,
    detail: Mapping[str, Any] | None = None,
    trusted_request: PortfolioSolveRequest | None = None,
) -> dict[str, Any]:
    """Atomically persist opaque terminal result/detail JSON and query scalars.

    The persistence boundary intentionally accepts mappings rather than a core
    model type. It still checks the tenant, fingerprint, status, immutable menu scope,
    and completed-selection reconciliation before any row becomes terminal.
    """

    # Keep caller-owned immutable models/large selection arrays by reference.
    # The worker passes its already-validated database request and solve result
    # directly, avoiding full result/detail/request JSON round-trips. Direct
    # callers retain the defensive database reconstruction below.
    if isinstance(result, PortfolioSolveResult):
        result_envelope = _PlanningResultEnvelope.model_validate(
            result.model_dump(mode="python", exclude={"selections"})
        )
        selections: list[Any] | tuple[Any, ...] = result.selections
    else:
        result_input = _json_object(result, label="planning result")
        result_envelope = _PlanningResultEnvelope.model_validate(
            {
                key: value
                for key, value in result_input.items()
                if key != "selections"
            }
        )
        selections = _json_sequence(
            result_input.get("selections", []),
            label="planning result selections",
        )
    result_payload = result_envelope.model_dump(mode="json")
    detail_payload = _json_object(detail or {}, label="planning detail")
    state = conn.execute(
        """
        select planning_fingerprint, request, assumption_diff, status,
               parent_run_id::text, parent_planning_fingerprint,
               parent_source_snapshot_hash, budget, currency, key_count,
               coverage, skipped_keys, skipped_key_count, menus_fingerprint,
               attempts
        from planning_runs
        where tenant_id = %s::uuid and run_id = %s::uuid
        for update
        """,
        (tenant_uuid, run_id),
    ).fetchone()
    if state is None:
        raise LookupError("planning run does not exist")
    (
        fingerprint,
        request,
        assumption_diff,
        status,
        parent_run_id,
        parent_fingerprint,
        parent_snapshot_hash,
        budget,
        currency,
        key_count,
        run_coverage,
        persisted_skipped_keys,
        persisted_skipped_key_count,
        stored_menus_fingerprint,
        current_attempts,
    ) = state
    if status != "running":
        raise ValueError("planning result can only finalize a running run")
    if current_attempts != attempts:
        raise ValueError("planning result claim attempt is stale")

    request_payload = _json_object(request, label="stored planning request")
    terminal_status = result_envelope.status
    if result_envelope.planning_fingerprint != fingerprint:
        raise ValueError("planning result fingerprint does not match run")
    if result_envelope.tenant_id != request_payload.get("tenant_id"):
        raise ValueError("planning result tenant does not match stored request")
    if result_envelope.contract_version != request_payload.get(
        "contract_version"
    ):
        raise ValueError("planning result contract does not match stored request")

    if trusted_request is None:
        menus: list[PortfolioKeyMenu] = []
        with conn.cursor(name="planning_result_scope_loader") as cursor:
            cursor.execute(
                """
                select menu
                from planning_run_menus
                where tenant_id = %s::uuid and run_id = %s::uuid
                order by ordinal
                """,
                (tenant_uuid, run_id),
            )
            for (menu,) in cursor:
                menus.append(PortfolioKeyMenu.model_validate(menu))
        full_request_payload = {
            field_name: request_payload[field_name]
            for field_name in PortfolioSolveRequest.model_fields
            if field_name in request_payload
        }
        full_request_payload["menus"] = tuple(menus)
        full_request = PortfolioSolveRequest.model_validate(
            full_request_payload
        )
        del full_request_payload, menus
    else:
        full_request = PortfolioSolveRequest.model_validate(trusted_request)
    if len(full_request.menus) != key_count:
        raise ValueError("planning run menu scope does not reconcile")
    if _menus_fingerprint(full_request) != stored_menus_fingerprint:
        raise ValueError("planning run menus fingerprint does not reconcile")
    if planning_fingerprint(full_request) != fingerprint:
        raise ValueError("planning run fingerprint does not reconcile")

    summary_model = result_envelope.summary
    if terminal_status == "completed":
        if summary_model is None or not selections:
            raise ValueError("completed planning result requires a summary")
        if result_envelope.solver.termination not in {"optimal", "not_proven"}:
            raise ValueError("completed planning result requires a feasible solver")
        if len(selections) != key_count:
            raise ValueError(
                "completed selection count does not match immutable menu scope"
            )
    elif selections or summary_model is not None:
        raise ValueError("non-completed planning result cannot be actionable")
    if terminal_status == "infeasible" and (
        result_envelope.minimum_budget_required is None
        or result_envelope.budget_shortfall is None
    ):
        raise ValueError("infeasible planning result requires budget guidance")

    derive_selection_details = (
        detail_payload.get("_derive_selection_details") is True
    )
    if derive_selection_details and not (
        terminal_status == "completed"
        and isinstance(result, PortfolioSolveResult)
        and trusted_request is not None
    ):
        raise ValueError(
            "derived planning details require the trusted worker boundary"
        )
    supplied_selection_details = _json_sequence(
        detail_payload.get("selection_details", []),
        label="planning selection details",
    )
    if (
        terminal_status == "completed"
        and not derive_selection_details
        and len(supplied_selection_details) != len(selections)
    ):
        raise ValueError("planning selection details do not match result order")
    if terminal_status != "completed" and supplied_selection_details:
        raise ValueError("non-completed planning result cannot expose selection details")
    selection_details = (
        iter_planning_selection_details(
            request=full_request,
            result=result,
        )
        if derive_selection_details
        else iter(supplied_selection_details)
    )

    supplied_diff = detail_payload.get("assumption_diff")
    if supplied_diff is not None and _json_array(
        supplied_diff,
        label="planning assumption diff",
    ) != list(assumption_diff):
        raise ValueError("planning assumption diff does not match submitted run")
    for field_name, expected in (
        ("parent_run_id", parent_run_id),
        ("parent_planning_fingerprint", parent_fingerprint),
        ("parent_source_snapshot_hash", parent_snapshot_hash),
    ):
        if (
            field_name in detail_payload
            and detail_payload[field_name] != expected
        ):
            raise ValueError(f"planning {field_name} does not match submitted run")
    warnings = _json_sequence(
        detail_payload.get("warnings", []),
        label="planning warnings",
    )
    warning_total = 0
    for warning in warnings:
        if isinstance(warning, Mapping) and {
            "code",
            "count",
            "detail",
        } <= set(warning):
            warning_total += PlanningWarning.model_validate(warning).count
        else:
            warning_total += 1
    if (
        summary_model is not None
        and summary_model.warning_count is not None
        and summary_model.warning_count != warning_total
    ):
        raise ValueError("planning summary warning count does not reconcile")
    skipped_keys = _json_sequence(
        detail_payload.get("skipped_keys", []),
        label="planning skipped keys",
    )
    solver = result_envelope.solver.model_dump(mode="json")

    total_cash = Decimal("0")
    total_objective = Decimal("0")
    total_shortage = Decimal("0")
    total_service_level = Decimal("0")
    total_selected_confidence = Decimal("0")
    minimum_selected_confidence: Decimal | None = None
    low_confidence_key_count = 0
    maximum_aog_risk: Decimal | None = None
    no_change_count = 0
    selection_rows: list[tuple[Any, ...]] = []
    selected_menus = (
        full_request.menus if terminal_status == "completed" else ()
    )
    if terminal_status == "completed":
        for raw_selection, raw_detail, menu in zip(
            selections,
            selection_details,
            selected_menus,
            strict=True,
        ):
            typed_selection = PortfolioSelection.model_validate(raw_selection)
            typed_detail = PlanningSelectionDetail.model_validate(raw_detail)
            candidates = {
                candidate.candidate_id: candidate
                for candidate in menu.frontier.candidates
            }
            baselines = [
                candidate
                for candidate in menu.frontier.candidates
                if candidate.is_no_change
            ]
            if len(baselines) != 1:
                raise ValueError(
                    "planning frontier must contain one current candidate"
                )
            baseline = baselines[0]
            selected_candidate = candidates.get(
                typed_selection.selected_candidate_id
            )
            if selected_candidate is None:
                raise ValueError(
                    "planning selection references an unknown candidate"
                )
            expected_objective = objective_contribution(
                request=full_request,
                menu=menu,
                baseline=baseline,
                candidate=selected_candidate,
            )
            if (
                typed_selection.tenant_id != full_request.tenant_id
                or typed_selection.decision_key != menu.frontier.decision_key
                or typed_selection.current_candidate_id != baseline.candidate_id
                or typed_selection.selected_is_no_change
                != selected_candidate.is_no_change
                or typed_selection.acquisition_cash
                != selected_candidate.lifecycle_costs.acquisition_cash
                or typed_selection.expected_shortage
                != selected_candidate.outcome.expected_shortage
                or typed_selection.expected_service_level
                != selected_candidate.outcome.expected_service_level
                or typed_selection.expected_aog_risk
                != selected_candidate.outcome.expected_aog_risk
                or typed_selection.objective != expected_objective
                or typed_selection.floor_states
                != floor_states(menu, selected_candidate)
            ):
                raise ValueError(
                    "planning selection does not reconcile to immutable frontier"
                )
            if (
                typed_detail.decision_key != typed_selection.decision_key
                or not _snapshot_matches(
                    typed_detail.current,
                    request=full_request,
                    menu=menu,
                    baseline=baseline,
                    candidate=baseline,
                )
                or not _snapshot_matches(
                    typed_detail.selected,
                    request=full_request,
                    menu=menu,
                    baseline=baseline,
                    candidate=selected_candidate,
                )
            ):
                raise ValueError(
                    "planning selection detail does not reconcile "
                    "to immutable frontier"
                )
            for alternative in typed_detail.rejected_alternatives:
                rejected = candidates.get(alternative.candidate.candidate_id)
                if rejected is None or not _snapshot_matches(
                    alternative.candidate,
                    request=full_request,
                    menu=menu,
                    baseline=baseline,
                    candidate=rejected,
                ):
                    raise ValueError(
                        "planning rejected detail does not reconcile "
                        "to immutable frontier"
                    )

            total_cash += typed_selection.acquisition_cash
            total_objective += typed_selection.objective.total
            total_shortage += typed_selection.expected_shortage
            total_service_level += typed_selection.expected_service_level
            selected_confidence = typed_detail.selected.confidence
            total_selected_confidence += selected_confidence
            minimum_selected_confidence = (
                selected_confidence
                if minimum_selected_confidence is None
                else min(minimum_selected_confidence, selected_confidence)
            )
            if (
                summary_model is not None
                and summary_model.confidence_summary is not None
                and selected_confidence
                < summary_model.confidence_summary.low_confidence_threshold
            ):
                low_confidence_key_count += 1
            no_change_count += int(typed_selection.selected_is_no_change)
            maximum_aog_risk = (
                typed_selection.expected_aog_risk
                if maximum_aog_risk is None
                else max(maximum_aog_risk, typed_selection.expected_aog_risk)
            )
            selection_rows.append(
                (
                    tenant_uuid,
                    run_id,
                    typed_selection.decision_key,
                    typed_selection.current_candidate_id,
                    typed_selection.selected_candidate_id,
                    typed_selection.selected_is_no_change,
                    typed_selection.acquisition_cash,
                    typed_selection.objective.total,
                    typed_selection.model_dump_json(),
                    typed_detail.model_dump_json(),
                )
            )
    summary_payload = (
        summary_model.model_dump(mode="json")
        if summary_model is not None
        else None
    )
    if summary_model is not None:
        if summary_model.currency != currency:
            raise ValueError("planning summary currency does not match run")
        if summary_model.budget != budget:
            raise ValueError("planning summary budget does not match run")
        if summary_model.selected_acquisition_cash != total_cash:
            raise ValueError("planning summary spend does not reconcile")
        if total_cash > budget:
            raise ValueError("planning result exceeds the hard budget")
        if summary_model.selected_objective != total_objective:
            raise ValueError("planning summary objective does not reconcile")
        if summary_model.selected_key_count != key_count:
            raise ValueError("planning summary key count does not reconcile")
        if summary_model.no_change_key_count != no_change_count:
            raise ValueError("planning summary no-change count does not reconcile")
        if summary_model.expected_shortage != total_shortage:
            raise ValueError("planning summary shortage does not reconcile")
        if (
            summary_model.average_service_level
            != total_service_level / Decimal(key_count)
        ):
            raise ValueError("planning summary service level does not reconcile")
        if summary_model.maximum_aog_risk != maximum_aog_risk:
            raise ValueError("planning summary AOG risk does not reconcile")
        confidence = summary_model.confidence_summary
        if confidence is not None:
            if (
                confidence.selected_confidence_total
                != total_selected_confidence
            ):
                raise ValueError(
                    "planning summary selected confidence total "
                    "does not reconcile"
                )
            if (
                confidence.minimum_selected_confidence
                != minimum_selected_confidence
            ):
                raise ValueError(
                    "planning summary minimum selected confidence "
                    "does not reconcile"
                )
            if (
                confidence.low_confidence_key_count
                != low_confidence_key_count
            ):
                raise ValueError(
                    "planning summary low-confidence key count "
                    "does not reconcile"
                )
        if result_envelope.solver.objective != total_objective:
            raise ValueError("planning solver objective does not reconcile")
    if terminal_status == "completed":
        with ExitStack() as stack:
            selection_cursor = stack.enter_context(conn.cursor())
            selection_copy = stack.enter_context(
                selection_cursor.copy(_SELECTION_COPY_SQL)
            )
            for selection_row in selection_rows:
                selection_copy.write_row(selection_row)
        selection_rows.clear()
    persisted_selection_count = conn.execute(
        """
        select count(*)
        from planning_run_selections
        where tenant_id = %s::uuid and run_id = %s::uuid
        """,
        (tenant_uuid, run_id),
    ).fetchone()[0]
    expected_selection_count = key_count if terminal_status == "completed" else 0
    if persisted_selection_count != expected_selection_count:
        raise ValueError("normalized planning selection count does not reconcile")

    bounded_result = _bounded_terminal_result(
        result_payload,
        solver=solver,
        selection_count=len(selections),
    )
    bounded_warnings = [
        _bounded_evidence_item(item)
        for item in warnings[:_HEADER_SAMPLE_LIMIT]
    ]
    bounded_skipped_keys = [
        _bounded_evidence_item(item)
        for item in skipped_keys[:_HEADER_SAMPLE_LIMIT]
    ]
    source_skipped_keys = [
        _bounded_evidence_item(item)
        for item in _json_array(
            persisted_skipped_keys,
            label="persisted planning skipped keys",
        )
    ]
    remaining_skip_slots = max(
        0,
        _HEADER_SAMPLE_LIMIT - len(source_skipped_keys),
    )
    merged_skipped_keys = [
        *source_skipped_keys,
        *bounded_skipped_keys[:remaining_skip_slots],
    ]
    total_skipped_key_count = persisted_skipped_key_count + len(skipped_keys)
    input_skipped_key_count = _json_integer(
        _json_object(
            run_coverage,
            label="persisted planning coverage",
        ).get("skipped_key_count"),
        label="persisted input skipped key count",
    )
    if input_skipped_key_count != persisted_skipped_key_count:
        raise ValueError("persisted planning skip coverage does not reconcile")
    detail_contract_version = detail_payload.get("contract_version")
    if detail_contract_version not in {None, "planning-run.v1"}:
        raise ValueError("planning detail contract does not reconcile")
    bounded_detail = {
        "contract_version": detail_contract_version,
        "selection_count": len(selections),
        "warning_count": warning_total,
        "input_skipped_key_count": input_skipped_key_count,
        "worker_skipped_key_count": len(skipped_keys),
        "skipped_key_count": total_skipped_key_count,
    }
    row = conn.execute(
        """
        update planning_runs
        set status = %s,
            progress_completed = progress_total,
            summary = %s::jsonb,
            result = %s::jsonb,
            detail = %s::jsonb,
            solver = %s::jsonb,
            warnings = %s::jsonb,
            warning_count = %s,
            skipped_keys = %s::jsonb,
            skipped_key_count = %s,
            finished_at = now()
        where tenant_id = %s::uuid and run_id = %s::uuid
          and status = 'running'
          and attempts = %s
        returning run_id
        """,
        (
            terminal_status,
            json.dumps(summary_payload) if summary_payload is not None else None,
            json.dumps(bounded_result),
            json.dumps(bounded_detail),
            json.dumps(bounded_result["solver"]),
            json.dumps(bounded_warnings),
            warning_total,
            json.dumps(merged_skipped_keys),
            total_skipped_key_count,
            tenant_uuid,
            run_id,
            attempts,
        ),
    ).fetchone()
    if row is None:  # pragma: no cover - row is locked and checked above
        raise RuntimeError("planning run changed while persisting result")
    return bounded_result


class PgPlanningRunStore:
    """Small public API over atomic run/job persistence and tenant-scoped reads."""

    def __init__(
        self,
        pool,
        *,
        tenant_slug: str,
        tenant_uuid: str,
        principal: str = "planner",
        role: str = "planner",
    ) -> None:
        self._pool = pool
        self.tenant_id = tenant_slug
        self._uuid = tenant_uuid
        self._principal = principal
        self._role = role

    def _conn(self):
        return tenant_conn(
            self._pool,
            tenant_uuid=self._uuid,
            role=self._role,
            sub=self._principal,
        )

    def submit(
        self,
        request: PortfolioSolveRequest,
        parent_run_id: str | None = None,
        *,
        scope_kind: PlanningScopeKind = "explicit",
        input_coverage: Mapping[str, Any] | None = None,
        source_generation_hash: str | None = None,
        rerun_mandatory_floors: (
            Mapping[str, tuple[MandatoryFloor, ...]] | None
        ) = None,
    ) -> PlanningRunSubmission:
        if request.tenant_id != self.tenant_id:
            raise ValueError("planning request tenant does not match store tenant")
        if scope_kind not in {"explicit", "all_eligible"}:
            raise ValueError("planning scope kind is invalid")
        if scope_kind == "all_eligible" and input_coverage is None:
            raise ValueError(
                "all-eligible planning submission requires authoritative input coverage"
            )

        fingerprint = planning_fingerprint(request)
        all_decision_keys = tuple(
            menu.frontier.decision_key for menu in request.menus
        )
        explicit_scope = all_decision_keys if scope_kind == "explicit" else ()
        scope_preview = (
            all_decision_keys
            if scope_kind == "explicit"
            else all_decision_keys[:10]
        )
        menus_fingerprint = _menus_fingerprint(request)
        if source_generation_hash is None:
            source_generation_hash = planning_input_source_generation_hash(
                request.source_snapshot_hash
            )
        if (
            not isinstance(source_generation_hash, str)
            or not source_generation_hash.startswith("planning_generation_")
            or len(source_generation_hash) != len("planning_generation_") + 64
        ):
            raise ValueError("planning source generation hash is invalid")
        coverage = _coverage_payload(
            request,
            input_coverage=input_coverage,
        )
        input_skipped_key_count = coverage["skipped_key_count"]
        input_skipped_keys = (
            [
                {
                    "reason_code": "missing_candidate_frontier",
                    "count": input_skipped_key_count,
                }
            ]
            if input_skipped_key_count
            else []
        )
        bounded_rerun_floors = _bounded_rerun_mandatory_floors(
            request,
            scope_kind=scope_kind,
            supplied=rerun_mandatory_floors,
        )
        payload = _request_payload(
            request,
            scope_kind=scope_kind,
            scope_preview=scope_preview,
            menus_fingerprint=menus_fingerprint,
            source_generation_hash=source_generation_hash,
            mandatory_floors=bounded_rerun_floors,
        )
        with self._conn() as conn:
            assumption_diff: tuple[dict[str, Any], ...] = ()
            parent_fingerprint: str | None = None
            parent_snapshot_hash: str | None = None
            if parent_run_id is not None:
                parent = conn.execute(
                    """
                    select request, planning_fingerprint, source_snapshot_hash,
                           status, menus_fingerprint, menu_count,
                           source_generation_hash
                    from planning_runs
                    where tenant_id = %s::uuid and run_id = %s::uuid
                    """,
                    (self._uuid, parent_run_id),
                ).fetchone()
                if parent is None:
                    raise ValueError("planning parent run does not exist for tenant")
                parent_config = _json_object(
                    parent[0],
                    label="planning parent request config",
                )
                parent_fingerprint = parent[1]
                parent_snapshot_hash = parent[2]
                if parent[3] not in {"completed", "infeasible", "failed"}:
                    raise ValueError("planning parent run must be terminal")
                if (
                    parent_config.get("menus_fingerprint") != parent[4]
                    or parent_config.get("menu_count") != parent[5]
                ):
                    raise ValueError("planning parent menu evidence is corrupt")
                if (
                    parent_config.get("source_snapshot_hash")
                    != parent_snapshot_hash
                ):
                    raise ValueError("planning parent snapshot evidence is corrupt")
                if (
                    parent_config.get("source_generation_hash")
                    != parent[6]
                ):
                    raise ValueError("planning parent generation evidence is corrupt")
                assumption_diff = _bounded_assumption_diff(
                    parent_config,
                    payload,
                )
            row = conn.execute(
                f"""
                insert into planning_runs (
                  tenant_id, planning_fingerprint, contract_version,
                  parent_run_id, parent_planning_fingerprint,
                  parent_source_snapshot_hash, assumption_diff,
                  scope_kind, scope_preview, source_snapshot_hash,
                  explicit_scope, source_generation_hash, key_count,
                  menu_count, menus_fingerprint, candidate_count,
                  feasible_candidate_count, coverage,
                  budget, horizon_days, currency, model_profile, request,
                  progress_total, skipped_keys, skipped_key_count, submitted_by
                ) values (
                  %s::uuid, %s, %s, %s::uuid, %s, %s, %s::jsonb, %s,
                  %s::jsonb, %s, %s::jsonb, %s, %s, %s, %s, %s, %s,
                  %s::jsonb,
                  %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s
                )
                on conflict (
                  tenant_id, planning_fingerprint, source_generation_hash
                ) do nothing
                returning {_RUN_COLUMNS}
                """,
                (
                    self._uuid,
                    fingerprint,
                    request.contract_version,
                    parent_run_id,
                    parent_fingerprint,
                    parent_snapshot_hash,
                    json.dumps(assumption_diff),
                    scope_kind,
                    json.dumps(scope_preview),
                    request.source_snapshot_hash,
                    json.dumps(explicit_scope),
                    source_generation_hash,
                    len(all_decision_keys),
                    len(request.menus),
                    menus_fingerprint,
                    coverage["candidate_count"],
                    coverage["feasible_candidate_count"],
                    json.dumps(coverage),
                    request.budget,
                    request.horizon_days,
                    request.currency,
                    json.dumps(_model_profile(request)),
                    json.dumps(payload),
                    len(all_decision_keys),
                    json.dumps(input_skipped_keys),
                    input_skipped_key_count,
                    self._principal,
                ),
            ).fetchone()
            created = row is not None
            if created:
                run = _record(row)
                conn.cursor().executemany(
                    """
                    insert into planning_run_menus (
                      tenant_id, run_id, ordinal, decision_key,
                      candidate_count, menu
                    ) values (%s::uuid, %s::uuid, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        (
                            self._uuid,
                            run.run_id,
                            ordinal,
                            menu.frontier.decision_key,
                            len(menu.frontier.candidates),
                            json.dumps(menu.model_dump(mode="json")),
                        )
                        for ordinal, menu in enumerate(request.menus)
                    ),
                )
                conn.execute(
                    "insert into jobs (tenant_id, kind, payload) "
                    "values (%s::uuid, 'planning', %s::jsonb)",
                    (self._uuid, json.dumps({"run_id": run.run_id})),
                )
            else:
                existing = conn.execute(
                    f"""
                    select {_RUN_COLUMNS}
                    from planning_runs
                    where tenant_id = %s::uuid and planning_fingerprint = %s
                      and source_generation_hash = %s
                    """,
                    (self._uuid, fingerprint, source_generation_hash),
                ).fetchone()
                if existing is None:  # pragma: no cover - uniqueness race safety
                    raise RuntimeError("idempotent planning run disappeared")
                run = _record(existing)
                if run.scope_kind != scope_kind or run.coverage != coverage:
                    raise ValueError(
                        "idempotent planning input coverage does not reconcile"
                    )
        return PlanningRunSubmission(run=run, created=created)

    def get(self, run_id: str) -> PlanningRunRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                f"""
                select {_RUN_COLUMNS}
                from planning_runs
                where tenant_id = %s::uuid and run_id = %s::uuid
                """,
                (self._uuid, run_id),
            ).fetchone()
        return _record(row) if row is not None else None

    def list_recent(self, *, limit: int = 20) -> tuple[PlanningRunRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("planning run limit must be between 1 and 100")
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                select {_RUN_COLUMNS}
                from planning_runs
                where tenant_id = %s::uuid
                order by created_at desc, run_id desc
                limit %s
                """,
                (self._uuid, limit),
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def rerun_config(self, run_id: str) -> PlanningRerunConfig | None:
        """Return bounded parent assumptions without loading normalized menus."""

        with self._conn() as conn:
            row = conn.execute(
                """
                select run_id::text, status, scope_kind, explicit_scope,
                       budget, horizon_days, currency, source_generation_hash,
                       model_profile, request
                from planning_runs
                where tenant_id = %s::uuid and run_id = %s::uuid
                """,
                (self._uuid, run_id),
            ).fetchone()
        if row is None:
            return None
        if row[1] not in {"completed", "infeasible", "failed"}:
            raise ValueError("planning rerun parent must be terminal")
        request = _json_object(row[9], label="planning rerun request")
        explicit_scope = tuple(row[3])
        mandatory_floors = request.get("mandatory_floors", {})
        if (
            len(explicit_scope) > 200
            or not isinstance(mandatory_floors, Mapping)
            or len(mandatory_floors) > 200
        ):
            raise ValueError("planning rerun config exceeds browser bounds")
        return PlanningRerunConfig(
            run_id=row[0],
            scope_kind=row[2],
            explicit_scope=explicit_scope,
            budget=row[4],
            horizon_days=row[5],
            currency=row[6],
            source_generation_hash=row[7],
            model_profile=_json_object(
                row[8],
                label="planning rerun model profile",
            ),
            objective_weights=request.get("objective_weights"),
            mandatory_floors=dict(mandatory_floors),
            time_limit_seconds=request.get("time_limit_seconds"),
        )

    def selections(
        self,
        run_id: str,
    ) -> tuple[PlanningRunSelectionRecord, ...]:
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                select {_SELECTION_COLUMNS}
                from planning_run_selections
                where tenant_id = %s::uuid and run_id = %s::uuid
                order by decision_key
                """,
                (self._uuid, run_id),
            ).fetchall()
        return tuple(_selection_record(row) for row in rows)

    def selection_page(
        self,
        run_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        decision_key: str | None = None,
        selected_is_no_change: bool | None = None,
    ) -> tuple[tuple[PlanningRunSelectionRecord, ...], int]:
        """Return a bounded selection page and filtered total."""

        if not 1 <= limit <= 100:
            raise ValueError("planning selection limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("planning selection offset must be non-negative")
        if decision_key is not None and not decision_key:
            raise ValueError("planning selection decision_key must be non-empty")

        clauses = [
            "tenant_id = %s::uuid",
            "run_id = %s::uuid",
        ]
        params: list[Any] = [self._uuid, run_id]
        if decision_key is not None:
            clauses.append("decision_key = %s")
            params.append(decision_key)
        if selected_is_no_change is not None:
            clauses.append("selected_is_no_change = %s")
            params.append(selected_is_no_change)
        where = " and ".join(clauses)

        with self._conn() as conn:
            total = conn.execute(
                f"""
                select count(*)
                from planning_run_selections
                where {where}
                """,  # noqa: S608 - clauses are fixed literals
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                select {_SELECTION_COLUMNS}
                from planning_run_selections
                where {where}
                order by decision_key
                limit %s offset %s
                """,  # noqa: S608 - clauses and columns are fixed literals
                [*params, limit, offset],
            ).fetchall()
        return tuple(_selection_record(row) for row in rows), int(total)


__all__ = [
    "PgPlanningRunStore",
    "PlanningRerunConfig",
    "PlanningRunRecord",
    "PlanningRunSelectionRecord",
    "PlanningRunStatus",
    "PlanningRunSubmission",
    "PlanningRunWork",
    "load_planning_run_work",
    "mark_planning_run_claimed",
    "mark_planning_run_failed",
    "mark_planning_run_retry",
    "persist_planning_result",
]
