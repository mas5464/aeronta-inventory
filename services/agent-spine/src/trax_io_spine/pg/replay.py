"""Trusted, tenant-scoped historical replay persistence and scorecards."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from trax_io_reco.contracts.replay import (
    MatchedReplayObservation,
    ReplayEvaluationRequest,
    ReplayExclusion,
    ReplayUniverseDecision,
    ShadowScorecard,
)
from trax_io_reco.replay import build_shadow_scorecard

from .db import tenant_conn

ReplayRunStatus = Literal["queued", "running", "completed", "failed"]
ReplayComparisonRule = Literal["matched_budget", "matched_service"]
_MAX_TOLERANCE_DIGITS = 18
_MAX_TOLERANCE_DECIMAL_PLACES = 12

_RUN_COLUMNS = """
replay_id::text, replay_fingerprint, input_sha256, contract_version, status,
universe_ref, universe_id, universe_sha256, comparison_rule,
expected_decision_count, advisory_only, scorecard, coverage_rate, detail,
submitted_by, attempts, claimed_at, started_at, finished_at, created_at,
updated_at
"""

_UNIVERSE_COLUMNS = """
universe_ref, universe_id, universe_sha256, trusted_input_sha256,
contract_version, currency, expected_decision_count, observation_count,
exclusion_count, created_at
"""

_SCORECARD_NORMALIZED_FIELDS = {
    "universe_decisions",
    "exclusions",
    "observation_lineage",
    "cohorts",
    "source_snapshot_hashes",
    "planning_fingerprints",
}


class ReplayRunConfig(BaseModel):
    """Bounded user-configurable portion of an otherwise trusted replay."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    universe_ref: str = Field(min_length=1, max_length=256)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    current_policy_label: str = Field(min_length=1, max_length=120)
    challenger_policy_label: str = Field(min_length=1, max_length=120)
    comparison_rule: ReplayComparisonRule
    match_tolerance: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        max_digits=_MAX_TOLERANCE_DIGITS,
        decimal_places=_MAX_TOLERANCE_DECIMAL_PLACES,
    )


class ReplayRunHeader(BaseModel):
    """Immutable bounded identity input stored on a replay-run header."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    contract_version: Literal["replay.v1"] = "replay.v1"
    tenant_id: str = Field(min_length=1, max_length=256)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    universe_ref: str = Field(min_length=1, max_length=256)
    universe_id: str = Field(min_length=1, max_length=256)
    universe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trusted_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_decision_count: int = Field(gt=0)
    observation_count: int = Field(ge=0)
    exclusion_count: int = Field(ge=0)
    current_policy_label: str = Field(min_length=1, max_length=120)
    challenger_policy_label: str = Field(min_length=1, max_length=120)
    comparison_rule: ReplayComparisonRule
    match_tolerance: Decimal = Field(
        ge=0,
        max_digits=_MAX_TOLERANCE_DIGITS,
        decimal_places=_MAX_TOLERANCE_DECIMAL_PLACES,
    )

    @model_validator(mode="after")
    def _counts_reconcile(self) -> ReplayRunHeader:
        if (
            self.observation_count + self.exclusion_count
            != self.expected_decision_count
        ):
            raise ValueError("trusted replay header counts do not reconcile")
        return self


class ReplayRunRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    replay_id: str
    replay_fingerprint: str
    input_sha256: str
    contract_version: str
    status: ReplayRunStatus
    universe_ref: str
    universe_id: str
    universe_sha256: str
    comparison_rule: str
    expected_decision_count: int
    advisory_only: bool
    scorecard: dict[str, Any] | None
    coverage_rate: Decimal | None
    detail: dict[str, Any]
    submitted_by: str
    attempts: int
    claimed_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReplayRunSubmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run: ReplayRunRecord
    created: bool


class ReplayRunWork(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    replay_id: str
    replay_fingerprint: str
    request: ReplayEvaluationRequest


class ReplayUniverseRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    universe_ref: str
    universe_id: str
    universe_sha256: str
    trusted_input_sha256: str
    contract_version: str
    currency: str
    expected_decision_count: int
    observation_count: int
    exclusion_count: int
    created_at: datetime


class ReplayLineageRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: str
    decision_key: str
    as_of: datetime
    horizon_end: datetime
    cohort_id: str
    lineage: dict[str, Any]


class ReplayExclusionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: str
    decision_key: str
    as_of: datetime
    horizon_end: datetime
    reason_code: str
    exclusion: dict[str, Any]


class ReplayCohortRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cohort_id: str
    observation_count: int
    cohort: dict[str, Any]


def _canonical_json(value: object) -> tuple[Any, str]:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return json.loads(encoded), hashlib.sha256(encoded).hexdigest()


def replay_fingerprint(request: ReplayEvaluationRequest) -> str:
    """Return the same v2 trusted-package/config identity stored for a run."""

    rows = _trusted_rows(request)
    trusted_digest = _trusted_input_sha256(
        tenant_id=request.tenant_id,
        currency=request.currency,
        universe_id=request.universe_id,
        universe_sha256=request.universe_sha256,
        expected_decision_count=request.expected_decision_count,
        rows=rows,
    )
    config = _normalize_config(
        "logical-evidence",
        currency=request.currency,
        current_policy_label=request.current_policy_label,
        challenger_policy_label=request.challenger_policy_label,
        comparison_rule=request.comparison_rule,
        match_tolerance=request.match_tolerance,
    )
    header = _request_header(
        request,
        config=config,
        trusted_input_sha256=trusted_digest,
    )
    _digest, fingerprint = _replay_run_identity(header)
    return fingerprint


def _trusted_rows(
    request: ReplayEvaluationRequest,
) -> tuple[dict[str, Any], ...]:
    rows = [
        {
            "row_kind": "observation",
            "payload": observation.model_dump(mode="json"),
        }
        for observation in request.observations
    ]
    rows.extend(
        {
            "row_kind": "exclusion",
            "payload": exclusion.model_dump(mode="json"),
        }
        for exclusion in request.exclusions
    )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row["payload"]["as_of"],
                row["payload"]["decision_key"],
                row["payload"]["observation_id"],
                row["row_kind"],
            ),
        )
    )


def _trusted_input_sha256(
    *,
    tenant_id: str,
    currency: str,
    universe_id: str,
    universe_sha256: str,
    expected_decision_count: int,
    rows: tuple[dict[str, Any], ...],
) -> str:
    _payload, digest = _canonical_json(
        {
            "namespace": "trax-io-trusted-replay-input-v1",
            "tenant_id": tenant_id,
            "currency": currency,
            "universe_id": universe_id,
            "universe_sha256": universe_sha256,
            "expected_decision_count": expected_decision_count,
            "rows": rows,
        }
    )
    return digest


def _record(row: tuple[Any, ...]) -> ReplayRunRecord:
    return ReplayRunRecord.model_validate(
        dict(zip(ReplayRunRecord.model_fields, row, strict=True))
    )


def _universe_record(row: tuple[Any, ...]) -> ReplayUniverseRecord:
    return ReplayUniverseRecord.model_validate(
        dict(zip(ReplayUniverseRecord.model_fields, row, strict=True))
    )


def _normalize_config(
    universe_ref: str,
    *,
    currency: str,
    current_policy_label: str,
    challenger_policy_label: str,
    comparison_rule: str,
    match_tolerance: Decimal | str | int = Decimal("0"),
) -> ReplayRunConfig:
    try:
        tolerance = Decimal(str(match_tolerance))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("replay match tolerance must be a finite decimal") from exc
    if not tolerance.is_finite():
        raise ValueError("replay match tolerance must be a bounded finite decimal")
    decimal_tuple = tolerance.as_tuple()
    decimal_places = max(-decimal_tuple.exponent, 0)
    whole_digits = 0 if tolerance == 0 else max(tolerance.adjusted() + 1, 0)
    expanded_digits = max(
        len(decimal_tuple.digits),
        whole_digits + decimal_places,
    )
    if (
        expanded_digits > _MAX_TOLERANCE_DIGITS
        or decimal_places > _MAX_TOLERANCE_DECIMAL_PLACES
    ):
        raise ValueError("replay match tolerance must be a bounded finite decimal")
    tolerance = Decimal("0") if tolerance == 0 else tolerance.normalize()
    return ReplayRunConfig(
        universe_ref=universe_ref,
        currency=currency,
        current_policy_label=current_policy_label,
        challenger_policy_label=challenger_policy_label,
        comparison_rule=comparison_rule,
        match_tolerance=tolerance,
    )


def _config_from_request_header(header: Mapping[str, Any]) -> ReplayRunConfig:
    parsed = ReplayRunHeader.model_validate(header)
    return _normalize_config(
        parsed.universe_ref,
        currency=parsed.currency,
        current_policy_label=parsed.current_policy_label,
        challenger_policy_label=parsed.challenger_policy_label,
        comparison_rule=parsed.comparison_rule,
        match_tolerance=parsed.match_tolerance,
    )


def _request_header(
    request: ReplayEvaluationRequest,
    *,
    config: ReplayRunConfig,
    trusted_input_sha256: str,
) -> ReplayRunHeader:
    return ReplayRunHeader(
        contract_version=request.contract_version,
        tenant_id=request.tenant_id,
        currency=request.currency,
        universe_ref=config.universe_ref,
        universe_id=request.universe_id,
        universe_sha256=request.universe_sha256,
        trusted_input_sha256=trusted_input_sha256,
        expected_decision_count=request.expected_decision_count,
        observation_count=len(request.observations),
        exclusion_count=len(request.exclusions),
        current_policy_label=config.current_policy_label,
        challenger_policy_label=config.challenger_policy_label,
        comparison_rule=config.comparison_rule,
        match_tolerance=config.match_tolerance,
    )


def _replay_run_identity(header: ReplayRunHeader) -> tuple[str, str]:
    """Bind trusted package identity and normalized run config, not raw rows."""

    payload = header.model_dump(mode="json", exclude={"universe_ref"})
    _canonical, digest = _canonical_json(
        {
            "namespace": "trax-io-replay-run-input-v2",
            "header": payload,
        }
    )
    return digest, f"replay_{digest}"


def _load_trusted_header(
    conn,
    *,
    tenant_uuid: str,
    tenant_slug: str,
    config: ReplayRunConfig,
) -> ReplayRunHeader:
    """Read only bounded universe metadata on the synchronous app path."""

    row = conn.execute(
        """
        select universe_id, universe_sha256, trusted_input_sha256,
               contract_version, currency, expected_decision_count,
               observation_count, exclusion_count
        from replay_universes
        where tenant_id = %s::uuid and universe_ref = %s
        """,
        (tenant_uuid, config.universe_ref),
    ).fetchone()
    if row is None:
        raise LookupError("trusted replay universe is unavailable")
    (
        universe_id,
        universe_sha256,
        trusted_digest,
        contract_version,
        currency,
        expected_decision_count,
        observation_count,
        exclusion_count,
    ) = row
    if contract_version != "replay.v1" or currency != config.currency:
        raise ValueError("trusted replay universe config is incompatible")
    return ReplayRunHeader(
        contract_version=contract_version,
        tenant_id=tenant_slug,
        currency=currency,
        universe_ref=config.universe_ref,
        universe_id=universe_id,
        universe_sha256=universe_sha256,
        trusted_input_sha256=trusted_digest,
        expected_decision_count=expected_decision_count,
        observation_count=observation_count,
        exclusion_count=exclusion_count,
        current_policy_label=config.current_policy_label,
        challenger_policy_label=config.challenger_policy_label,
        comparison_rule=config.comparison_rule,
        match_tolerance=config.match_tolerance,
    )


def seed_replay_universe(
    pool,
    *,
    tenant_uuid: str,
    universe_ref: str,
    request: ReplayEvaluationRequest,
) -> ReplayUniverseRecord:
    """Idempotently seed one immutable trusted universe through trax_seed."""

    if not isinstance(universe_ref, str) or not 1 <= len(universe_ref) <= 256:
        raise ValueError("trusted replay universe_ref must be between 1 and 256 chars")
    if len(request.universe_id) > 256:
        raise ValueError("trusted replay universe_id must be at most 256 chars")
    rows = _trusted_rows(request)
    trusted_digest = _trusted_input_sha256(
        tenant_id=request.tenant_id,
        currency=request.currency,
        universe_id=request.universe_id,
        universe_sha256=request.universe_sha256,
        expected_decision_count=request.expected_decision_count,
        rows=rows,
    )
    with pool.connection() as conn:
        tenant = conn.execute(
            "select slug from tenants where id = %s::uuid",
            (tenant_uuid,),
        ).fetchone()
        if tenant is None:
            raise LookupError("trusted replay tenant is unavailable")
        if tenant[0] != request.tenant_id:
            raise ValueError("trusted replay request tenant does not match seed tenant")
        inserted = conn.execute(
            f"""
            insert into replay_universes (
              tenant_id, universe_ref, universe_id, universe_sha256,
              trusted_input_sha256, contract_version, currency,
              expected_decision_count, observation_count, exclusion_count
            ) values (
              %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            on conflict (tenant_id, universe_ref) do nothing
            returning {_UNIVERSE_COLUMNS}
            """,
            (
                tenant_uuid,
                universe_ref,
                request.universe_id,
                request.universe_sha256,
                trusted_digest,
                request.contract_version,
                request.currency,
                request.expected_decision_count,
                len(request.observations),
                len(request.exclusions),
            ),
        ).fetchone()
        if inserted is not None:
            record = _universe_record(inserted)
            conn.cursor().executemany(
                """
                insert into replay_universe_rows (
                  tenant_id, universe_ref, ordinal, row_kind,
                  observation_id, decision_key, as_of, horizon_end, payload
                ) values (
                  %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    (
                        tenant_uuid,
                        universe_ref,
                        ordinal,
                        row["row_kind"],
                        row["payload"]["observation_id"],
                        row["payload"]["decision_key"],
                        row["payload"]["as_of"],
                        row["payload"]["horizon_end"],
                        json.dumps(row["payload"]),
                    )
                    for ordinal, row in enumerate(rows)
                ),
            )
            counts = conn.execute(
                """
                select
                  count(*),
                  count(*) filter (where row_kind = 'observation'),
                  count(*) filter (where row_kind = 'exclusion')
                from replay_universe_rows
                where tenant_id = %s::uuid and universe_ref = %s
                """,
                (tenant_uuid, universe_ref),
            ).fetchone()
            if counts != (
                request.expected_decision_count,
                len(request.observations),
                len(request.exclusions),
            ):
                raise ValueError("trusted replay universe rows do not reconcile")
        else:
            existing = conn.execute(
                f"""
                select {_UNIVERSE_COLUMNS}
                from replay_universes
                where tenant_id = %s::uuid and universe_ref = %s
                """,
                (tenant_uuid, universe_ref),
            ).fetchone()
            if existing is None:  # pragma: no cover - conflict row cannot disappear
                raise RuntimeError("trusted replay universe disappeared")
            record = _universe_record(existing)
            if (
                record.trusted_input_sha256 != trusted_digest
                or record.universe_id != request.universe_id
                or record.universe_sha256 != request.universe_sha256
                or record.expected_decision_count
                != request.expected_decision_count
                or record.observation_count != len(request.observations)
                or record.exclusion_count != len(request.exclusions)
            ):
                raise ValueError(
                    "trusted replay universe_ref already names different evidence"
                )
            counts = conn.execute(
                """
                select
                  count(*),
                  count(*) filter (where row_kind = 'observation'),
                  count(*) filter (where row_kind = 'exclusion')
                from replay_universe_rows
                where tenant_id = %s::uuid and universe_ref = %s
                """,
                (tenant_uuid, universe_ref),
            ).fetchone()
            if counts != (
                request.expected_decision_count,
                len(request.observations),
                len(request.exclusions),
            ):
                raise ValueError("trusted replay universe rows do not reconcile")
    return record


def _load_trusted_request(
    conn,
    *,
    tenant_uuid: str,
    tenant_slug: str,
    config: ReplayRunConfig,
) -> tuple[ReplayEvaluationRequest, str]:
    header = conn.execute(
        """
        select universe_id, universe_sha256, trusted_input_sha256,
               contract_version, currency, expected_decision_count,
               observation_count, exclusion_count
        from replay_universes
        where tenant_id = %s::uuid and universe_ref = %s
        """,
        (tenant_uuid, config.universe_ref),
    ).fetchone()
    if header is None:
        raise LookupError("trusted replay universe is unavailable")
    (
        universe_id,
        universe_sha256,
        trusted_digest,
        contract_version,
        currency,
        expected_decision_count,
        observation_count,
        exclusion_count,
    ) = header
    if contract_version != "replay.v1" or currency != config.currency:
        raise ValueError("trusted replay universe config is incompatible")

    observations: list[MatchedReplayObservation] = []
    exclusions: list[ReplayExclusion] = []
    canonical_rows: list[dict[str, Any]] = []
    decisions: list[ReplayUniverseDecision] = []
    with conn.cursor(name="trusted_replay_universe_reader") as cursor:
        cursor.execute(
            """
            select row_kind, observation_id, decision_key, as_of, horizon_end,
                   payload
            from replay_universe_rows
            where tenant_id = %s::uuid and universe_ref = %s
            order by ordinal
            """,
            (tenant_uuid, config.universe_ref),
        )
        for row_kind, observation_id, decision_key, as_of, horizon_end, payload in cursor:
            if row_kind == "observation":
                parsed: MatchedReplayObservation | ReplayExclusion = (
                    MatchedReplayObservation.model_validate(payload)
                )
                observations.append(parsed)
            elif row_kind == "exclusion":
                parsed = ReplayExclusion.model_validate(payload)
                exclusions.append(parsed)
            else:  # pragma: no cover - constrained by SQL
                raise ValueError("trusted replay row kind is invalid")
            if (
                parsed.tenant_id != tenant_slug
                or parsed.observation_id != observation_id
                or parsed.decision_key != decision_key
                or parsed.as_of != as_of
                or parsed.horizon_end != horizon_end
            ):
                raise ValueError("trusted replay row evidence does not reconcile")
            payload_json = parsed.model_dump(mode="json")
            canonical_rows.append({"row_kind": row_kind, "payload": payload_json})
            decisions.append(
                ReplayUniverseDecision(
                    observation_id=parsed.observation_id,
                    tenant_id=parsed.tenant_id,
                    decision_key=parsed.decision_key,
                    as_of=parsed.as_of,
                    horizon_end=parsed.horizon_end,
                )
            )

    if (
        len(decisions) != expected_decision_count
        or len(observations) != observation_count
        or len(exclusions) != exclusion_count
    ):
        raise ValueError("trusted replay universe counts do not reconcile")
    observed_digest = _trusted_input_sha256(
        tenant_id=tenant_slug,
        currency=currency,
        universe_id=universe_id,
        universe_sha256=universe_sha256,
        expected_decision_count=expected_decision_count,
        rows=tuple(canonical_rows),
    )
    if observed_digest != trusted_digest:
        raise ValueError("trusted replay universe fingerprint does not reconcile")

    request = ReplayEvaluationRequest(
        tenant_id=tenant_slug,
        currency=currency,
        universe_id=universe_id,
        universe_sha256=universe_sha256,
        expected_decision_count=expected_decision_count,
        universe_decisions=tuple(decisions),
        current_policy_label=config.current_policy_label,
        challenger_policy_label=config.challenger_policy_label,
        comparison_rule=config.comparison_rule,
        match_tolerance=config.match_tolerance,
        observations=tuple(observations),
        exclusions=tuple(exclusions),
    )
    return request, trusted_digest


def load_replay_run_work(
    conn,
    *,
    tenant_uuid: str,
    replay_id: str,
) -> ReplayRunWork:
    row = conn.execute(
        """
        select r.replay_id::text, r.replay_fingerprint, r.input_sha256,
               r.request, t.slug
        from replay_runs r
        join tenants t on t.id = r.tenant_id
        where r.tenant_id = %s::uuid and r.replay_id = %s::uuid
          and r.status = 'running'
        """,
        (tenant_uuid, replay_id),
    ).fetchone()
    if row is None:
        raise LookupError("claimed replay run is missing or not running")
    stored_header = ReplayRunHeader.model_validate(row[3])
    observed_input_sha256, observed_fingerprint = _replay_run_identity(
        stored_header
    )
    if (
        stored_header.tenant_id != row[4]
        or observed_input_sha256 != row[2]
        or observed_fingerprint != row[1]
    ):
        raise ValueError("stored replay run identity does not reconcile")
    config = _config_from_request_header(row[3])
    request, trusted_digest = _load_trusted_request(
        conn,
        tenant_uuid=tenant_uuid,
        tenant_slug=row[4],
        config=config,
    )
    reconstructed_header = _request_header(
        request,
        config=config,
        trusted_input_sha256=trusted_digest,
    )
    if (
        reconstructed_header != stored_header
        or stored_header.trusted_input_sha256 != trusted_digest
    ):
        raise ValueError("stored replay run does not reconcile to trusted universe")
    return ReplayRunWork(
        replay_id=row[0],
        replay_fingerprint=row[1],
        request=request,
    )


def mark_replay_run_claimed(
    conn,
    *,
    tenant_uuid: str,
    replay_id: str,
    attempts: int,
) -> None:
    if attempts < 1:
        raise ValueError("replay run attempts must be positive")
    row = conn.execute(
        """
        update replay_runs
        set status = 'running',
            attempts = %s,
            claimed_at = now(),
            started_at = coalesce(started_at, now())
        where tenant_id = %s::uuid and replay_id = %s::uuid
          and status in ('queued', 'running')
          and replay_runs.attempts = %s - 1
        returning replay_id
        """,
        (attempts, tenant_uuid, replay_id, attempts),
    ).fetchone()
    if row is None:
        raise LookupError("replay job does not reference a claimable run")


def mark_replay_run_retry(
    conn,
    *,
    tenant_uuid: str,
    replay_id: str,
    attempts: int,
    error: str,
) -> None:
    del error
    detail = {
        "error_code": "replay_worker_attempt_failed",
        "failed_attempt": attempts,
        "retryable": True,
    }
    row = conn.execute(
        """
        update replay_runs
        set status = 'queued',
            attempts = %s,
            claimed_at = null,
            detail = %s::jsonb
        where tenant_id = %s::uuid and replay_id = %s::uuid
          and status = 'running'
          and attempts = %s
        returning replay_id
        """,
        (attempts, json.dumps(detail), tenant_uuid, replay_id, attempts),
    ).fetchone()
    if row is None:
        raise LookupError("replay run is not retryable")


def mark_replay_run_failed(
    conn,
    *,
    tenant_uuid: str,
    replay_id: str,
    attempts: int,
    error: str,
) -> None:
    interrupted = error.startswith("replay worker lease expired")
    detail = {
        "error_code": (
            "replay_worker_interrupted"
            if interrupted
            else "replay_worker_failed"
        ),
        "failed_attempt": attempts,
        "retryable": False,
        "guidance": (
            "Submit a new immutable replay after verifying worker health."
            if interrupted
            else "Review the trusted replay configuration and retry as a new run."
        ),
    }
    row = conn.execute(
        """
        update replay_runs
        set status = 'failed',
            attempts = %s,
            detail = %s::jsonb,
            finished_at = now()
        where tenant_id = %s::uuid and replay_id = %s::uuid
          and status = 'running'
          and attempts = %s
        returning replay_id
        """,
        (attempts, json.dumps(detail), tenant_uuid, replay_id, attempts),
    ).fetchone()
    if row is None:
        raise LookupError("replay run is not fail-able")


def _bounded_scorecard(scorecard: ShadowScorecard) -> dict[str, Any]:
    payload = scorecard.model_dump(
        mode="json",
        exclude=_SCORECARD_NORMALIZED_FIELDS,
    )
    omitted = {
        "universe_decisions": [
            item.model_dump(mode="json")
            for item in scorecard.universe_decisions
        ],
        "exclusions": [
            item.model_dump(mode="json") for item in scorecard.exclusions
        ],
        "observation_lineage": [
            item.model_dump(mode="json")
            for item in scorecard.observation_lineage
        ],
        "cohorts": [
            item.model_dump(mode="json") for item in scorecard.cohorts
        ],
        "source_snapshot_hashes": list(scorecard.source_snapshot_hashes),
        "planning_fingerprints": list(scorecard.planning_fingerprints),
    }
    payload.update(
        {
            "universe_decision_count": len(scorecard.universe_decisions),
            "cohort_count": len(scorecard.cohorts),
            "lineage_count": len(scorecard.observation_lineage),
            "source_snapshot_hash_count": len(scorecard.source_snapshot_hashes),
            "planning_fingerprint_count": len(scorecard.planning_fingerprints),
            **{
                f"{field}_sha256": _canonical_json(value)[1]
                for field, value in omitted.items()
            },
        }
    )
    return payload


def persist_replay_scorecard(
    conn,
    *,
    tenant_uuid: str,
    replay_id: str,
    attempts: int,
    scorecard: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate full transient output, then persist normalized bounded evidence."""

    row = conn.execute(
        """
        select r.request, r.input_sha256, r.replay_fingerprint, r.status,
               t.slug, r.attempts
        from replay_runs r
        join tenants t on t.id = r.tenant_id
        where r.tenant_id = %s::uuid and r.replay_id = %s::uuid
        for update of r
        """,
        (tenant_uuid, replay_id),
    ).fetchone()
    if row is None:
        raise LookupError("replay run does not exist")
    (
        request_header,
        input_sha256,
        fingerprint,
        status,
        tenant_slug,
        current_attempts,
    ) = row
    if status != "running":
        raise ValueError("replay scorecard can only finalize a running run")
    if current_attempts != attempts:
        raise ValueError("replay scorecard claim attempt is stale")
    stored_header = ReplayRunHeader.model_validate(request_header)
    observed_input_sha256, observed_fingerprint = _replay_run_identity(
        stored_header
    )
    if (
        stored_header.tenant_id != tenant_slug
        or observed_input_sha256 != input_sha256
        or observed_fingerprint != fingerprint
    ):
        raise ValueError("stored replay request identity does not reconcile")
    config = _config_from_request_header(request_header)
    request, trusted_digest = _load_trusted_request(
        conn,
        tenant_uuid=tenant_uuid,
        tenant_slug=tenant_slug,
        config=config,
    )
    reconstructed_header = _request_header(
        request,
        config=config,
        trusted_input_sha256=trusted_digest,
    )
    if (
        reconstructed_header != stored_header
        or stored_header.trusted_input_sha256 != trusted_digest
    ):
        raise ValueError("stored replay request does not match trusted universe")

    supplied = ShadowScorecard.model_validate(dict(scorecard))
    expected = build_shadow_scorecard(request)
    if supplied != expected:
        raise ValueError("replay scorecard does not reconcile to stored request")

    lineage_by_id = {
        lineage.observation_id: lineage
        for lineage in supplied.observation_lineage
    }
    if request.observations:
        conn.cursor().executemany(
            """
            insert into replay_run_lineage (
              tenant_id, replay_id, observation_id, decision_key,
              as_of, horizon_end, cohort_id, lineage
            ) values (
              %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s::jsonb
            )
            """,
            (
                (
                    tenant_uuid,
                    replay_id,
                    observation.observation_id,
                    observation.decision_key,
                    observation.as_of,
                    observation.horizon_end,
                    lineage_by_id[observation.observation_id].cohort_id,
                    json.dumps(
                        {
                            "reference": lineage_by_id[
                                observation.observation_id
                            ].model_dump(mode="json"),
                            "current": observation.current_lineage.model_dump(
                                mode="json"
                            ),
                            "challenger": (
                                observation.challenger_lineage.model_dump(
                                    mode="json"
                                )
                            ),
                            "outcome": observation.outcome_lineage.model_dump(
                                mode="json"
                            ),
                        }
                    ),
                )
                for observation in request.observations
            ),
        )
    if supplied.exclusions:
        conn.cursor().executemany(
            """
            insert into replay_run_exclusions (
              tenant_id, replay_id, observation_id, decision_key,
              as_of, horizon_end, reason_code, exclusion
            ) values (
              %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s::jsonb
            )
            """,
            (
                (
                    tenant_uuid,
                    replay_id,
                    exclusion.observation_id,
                    exclusion.decision_key,
                    exclusion.as_of,
                    exclusion.horizon_end,
                    exclusion.reason_code,
                    json.dumps(exclusion.model_dump(mode="json")),
                )
                for exclusion in supplied.exclusions
            ),
        )
    if supplied.cohorts:
        conn.cursor().executemany(
            """
            insert into replay_run_cohorts (
              tenant_id, replay_id, cohort_id, observation_count, cohort
            ) values (%s::uuid, %s::uuid, %s, %s, %s::jsonb)
            """,
            (
                (
                    tenant_uuid,
                    replay_id,
                    cohort.cohort_id,
                    cohort.observation_count,
                    json.dumps(
                        cohort.model_dump(
                            mode="json",
                            exclude={"observation_ids"},
                        )
                    ),
                )
                for cohort in supplied.cohorts
            ),
        )

    persisted_counts = conn.execute(
        """
        select
          (
            select count(*) from replay_run_lineage
            where tenant_id = %s::uuid and replay_id = %s::uuid
          ),
          (
            select count(*) from replay_run_exclusions
            where tenant_id = %s::uuid and replay_id = %s::uuid
          ),
          (
            select count(*) from replay_run_cohorts
            where tenant_id = %s::uuid and replay_id = %s::uuid
          )
        """,
        (
            tenant_uuid,
            replay_id,
            tenant_uuid,
            replay_id,
            tenant_uuid,
            replay_id,
        ),
    ).fetchone()
    expected_counts = (
        len(supplied.observation_lineage),
        len(supplied.exclusions),
        len(supplied.cohorts),
    )
    if persisted_counts != expected_counts:
        raise ValueError("normalized replay evidence counts do not reconcile")

    bounded = _bounded_scorecard(supplied)
    detail = {
        "advisory_only": True,
        "writeback_capability": "none",
        "comparison_rule_definition": supplied.comparison_rule_definition,
        "review_package": {
            "input_sha256": input_sha256,
            "universe_sha256": request.universe_sha256,
            "trusted_input_sha256": trusted_digest,
            "lineage_count": len(supplied.observation_lineage),
            "exclusion_count": len(supplied.exclusions),
            "cohort_count": len(supplied.cohorts),
        },
    }
    completed = conn.execute(
        """
        update replay_runs
        set status = 'completed',
            scorecard = %s::jsonb,
            coverage_rate = %s,
            detail = %s::jsonb,
            finished_at = now()
        where tenant_id = %s::uuid and replay_id = %s::uuid
          and status = 'running'
          and attempts = %s
        returning replay_id
        """,
        (
            json.dumps(bounded),
            supplied.coverage_rate,
            json.dumps(detail),
            tenant_uuid,
            replay_id,
            attempts,
        ),
    ).fetchone()
    if completed is None:  # pragma: no cover - locked and checked above
        raise RuntimeError("replay run changed while persisting scorecard")
    return bounded


class PgReplayRunStore:
    """Bounded config submission over immutable trusted replay universes."""

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

    def resolve_request(
        self,
        body,
        *,
        principal: str | None = None,
        role: str | None = None,
    ) -> ReplayRunHeader:
        """Preflight bounded metadata without reading historical fact rows."""

        del principal, role
        value = (
            (lambda name: body.get(name))
            if isinstance(body, Mapping)
            else (lambda name: getattr(body, name))
        )
        config = _normalize_config(
            value("universe_ref"),
            currency=value("currency"),
            current_policy_label=value("current_policy_label"),
            challenger_policy_label=value("challenger_policy_label"),
            comparison_rule=value("comparison_rule"),
            match_tolerance=value("match_tolerance"),
        )
        with self._conn() as conn:
            header = _load_trusted_header(
                conn,
                tenant_uuid=self._uuid,
                tenant_slug=self.tenant_id,
                config=config,
            )
        return header

    def submit(
        self,
        universe_ref: str,
        *,
        currency: str,
        current_policy_label: str,
        challenger_policy_label: str,
        comparison_rule: str,
        match_tolerance: Decimal | str | int = Decimal("0"),
    ) -> ReplayRunSubmission:
        config = _normalize_config(
            universe_ref,
            currency=currency,
            current_policy_label=current_policy_label,
            challenger_policy_label=challenger_policy_label,
            comparison_rule=comparison_rule,
            match_tolerance=match_tolerance,
        )
        with self._conn() as conn:
            header = _load_trusted_header(
                conn,
                tenant_uuid=self._uuid,
                tenant_slug=self.tenant_id,
                config=config,
            )
            input_sha256, fingerprint = _replay_run_identity(header)
            header_payload = header.model_dump(mode="json")
            row = conn.execute(
                f"""
                insert into replay_runs (
                  tenant_id, replay_fingerprint, input_sha256,
                  contract_version, universe_ref, universe_id,
                  universe_sha256, comparison_rule, expected_decision_count,
                  request, submitted_by
                ) values (
                  %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                )
                on conflict (tenant_id, replay_fingerprint) do nothing
                returning {_RUN_COLUMNS}
                """,
                (
                    self._uuid,
                    fingerprint,
                    input_sha256,
                    header.contract_version,
                    config.universe_ref,
                    header.universe_id,
                    header.universe_sha256,
                    header.comparison_rule,
                    header.expected_decision_count,
                    json.dumps(header_payload),
                    self._principal,
                ),
            ).fetchone()
            created = row is not None
            if row is None:
                row = conn.execute(
                    f"""
                    select {_RUN_COLUMNS}
                    from replay_runs
                    where tenant_id = %s::uuid and replay_fingerprint = %s
                    """,
                    (self._uuid, fingerprint),
                ).fetchone()
                if row is None:  # pragma: no cover - conflict row cannot disappear
                    raise RuntimeError("idempotent replay run disappeared")
            run = _record(row)
            if (
                run.universe_ref != config.universe_ref
                or run.universe_sha256 != header.universe_sha256
            ):
                raise ValueError("idempotent replay universe does not reconcile")
            if created:
                conn.execute(
                    """
                    insert into jobs (tenant_id, kind, status, payload)
                    values (%s::uuid, 'replay', 'queued', %s::jsonb)
                    """,
                    (
                        self._uuid,
                        json.dumps({"replay_id": run.replay_id}),
                    ),
                )
        return ReplayRunSubmission(run=run, created=created)

    def get(self, replay_id: str) -> ReplayRunRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                f"""
                select {_RUN_COLUMNS}
                from replay_runs
                where tenant_id = %s::uuid and replay_id = %s::uuid
                """,
                (self._uuid, replay_id),
            ).fetchone()
        return _record(row) if row is not None else None

    def list_recent(self, *, limit: int = 20) -> tuple[ReplayRunRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("replay run limit must be between 1 and 100")
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                select {_RUN_COLUMNS}
                from replay_runs
                where tenant_id = %s::uuid
                order by created_at desc, replay_id desc
                limit %s
                """,
                (self._uuid, limit),
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def list_universes(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[ReplayUniverseRecord, ...], int]:
        """Return bounded trusted-universe metadata; never historical rows."""

        if not 1 <= limit <= 100:
            raise ValueError("replay universe limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("replay universe offset must be non-negative")
        with self._conn() as conn:
            total = conn.execute(
                """
                select count(*)
                from replay_universes
                where tenant_id = %s::uuid
                """,
                (self._uuid,),
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                select {_UNIVERSE_COLUMNS}
                from replay_universes
                where tenant_id = %s::uuid
                order by created_at desc, universe_ref
                limit %s offset %s
                """,
                (self._uuid, limit, offset),
            ).fetchall()
        return tuple(_universe_record(row) for row in rows), int(total)

    def lineage_page(
        self,
        replay_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        observation_id: str | None = None,
    ) -> tuple[tuple[ReplayLineageRecord, ...], int]:
        return self._child_page(
            table="replay_run_lineage",
            columns=(
                "observation_id, decision_key, as_of, horizon_end, "
                "cohort_id, lineage"
            ),
            model=ReplayLineageRecord,
            replay_id=replay_id,
            limit=limit,
            offset=offset,
            filter_field="observation_id",
            filter_value=observation_id,
        )

    def exclusion_page(
        self,
        replay_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        observation_id: str | None = None,
        reason_code: str | None = None,
    ) -> tuple[tuple[ReplayExclusionRecord, ...], int]:
        if reason_code is not None and not reason_code:
            raise ValueError("replay exclusion reason_code must be non-empty")
        clauses = ["tenant_id = %s::uuid", "replay_id = %s::uuid"]
        params: list[Any] = [self._uuid, replay_id]
        if observation_id is not None:
            if not observation_id:
                raise ValueError("replay observation_id must be non-empty")
            clauses.append("observation_id = %s")
            params.append(observation_id)
        if reason_code is not None:
            clauses.append("reason_code = %s")
            params.append(reason_code)
        return self._page_query(
            table="replay_run_exclusions",
            columns=(
                "observation_id, decision_key, as_of, horizon_end, "
                "reason_code, exclusion"
            ),
            model=ReplayExclusionRecord,
            where=" and ".join(clauses),
            params=params,
            limit=limit,
            offset=offset,
        )

    def cohort_page(
        self,
        replay_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[ReplayCohortRecord, ...], int]:
        return self._page_query(
            table="replay_run_cohorts",
            columns="cohort_id, observation_count, cohort",
            model=ReplayCohortRecord,
            where="tenant_id = %s::uuid and replay_id = %s::uuid",
            params=[self._uuid, replay_id],
            limit=limit,
            offset=offset,
            order_by="cohort_id",
        )

    def _child_page(
        self,
        *,
        table: str,
        columns: str,
        model,
        replay_id: str,
        limit: int,
        offset: int,
        filter_field: str,
        filter_value: str | None,
    ):
        clauses = ["tenant_id = %s::uuid", "replay_id = %s::uuid"]
        params: list[Any] = [self._uuid, replay_id]
        if filter_value is not None:
            if not filter_value:
                raise ValueError("replay observation_id must be non-empty")
            clauses.append(f"{filter_field} = %s")  # noqa: S608 - fixed caller literal
            params.append(filter_value)
        return self._page_query(
            table=table,
            columns=columns,
            model=model,
            where=" and ".join(clauses),
            params=params,
            limit=limit,
            offset=offset,
        )

    def _page_query(
        self,
        *,
        table: str,
        columns: str,
        model,
        where: str,
        params: list[Any],
        limit: int,
        offset: int,
        order_by: str = "observation_id",
    ):
        if not 1 <= limit <= 100:
            raise ValueError("replay page limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("replay page offset must be non-negative")
        with self._conn() as conn:
            total = conn.execute(
                f"select count(*) from {table} where {where}",  # noqa: S608
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                select {columns}
                from {table}
                where {where}
                order by {order_by}
                limit %s offset %s
                """,  # noqa: S608 - table/columns/where are fixed internal literals
                [*params, limit, offset],
            ).fetchall()
        return (
            tuple(
                model.model_validate(
                    dict(zip(model.model_fields, row, strict=True))
                )
                for row in rows
            ),
            int(total),
        )


__all__ = [
    "PgReplayRunStore",
    "ReplayCohortRecord",
    "ReplayExclusionRecord",
    "ReplayLineageRecord",
    "ReplayRunConfig",
    "ReplayRunRecord",
    "ReplayRunSubmission",
    "ReplayRunWork",
    "ReplayUniverseRecord",
    "load_replay_run_work",
    "mark_replay_run_claimed",
    "mark_replay_run_failed",
    "mark_replay_run_retry",
    "persist_replay_scorecard",
    "replay_fingerprint",
    "seed_replay_universe",
]
