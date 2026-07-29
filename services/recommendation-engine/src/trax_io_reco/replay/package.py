"""Build trusted replay requests from explicit historical source records."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import click
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from trax_io_reco.contracts.candidate import (
    CurrencyCode,
    NonEmptyStr,
    NonNegativeDecimal,
)
from trax_io_reco.contracts.planning import SolverEvidence
from trax_io_reco.contracts.replay import (
    ComparisonRule,
    MatchedReplayObservation,
    ReplayCohort,
    ReplayDecisionLineage,
    ReplayEvaluationRequest,
    ReplayExclusion,
    ReplayExclusionReason,
    ReplayInputArtifact,
    ReplayMetrics,
    ReplayOutcomeLineage,
    ReplayUniverseDecision,
    Sha256,
    replay_universe_sha256,
)

REPLAY_SOURCE_PACKAGE_VERSION = "replay-source.v1"
FACTUAL_SOURCE_KINDS = frozenset(
    {"demand", "receipt", "repair_outcome", "price", "part_attributes"}
)
POLICY_SOURCE_KINDS = frozenset(
    {"model", "tenant_policy", "objective", "candidate_frontier"}
)


class _ReplaySourceBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    source_contract_version: Literal["replay-source.v1"] = (
        REPLAY_SOURCE_PACKAGE_VERSION
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("replay source timestamps must include a timezone")
    return value.astimezone(UTC)


class ReplayPolicySourceRecord(_ReplaySourceBase):
    """One policy result and its exact historically available inputs."""

    metrics: ReplayMetrics
    source_snapshot_hash: NonEmptyStr
    planning_fingerprint: str = Field(
        pattern=r"^planning_[0-9a-f]{64}$"
    )
    planning_request_sha256: Sha256
    planning_run_id: str = Field(
        pattern=(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$"
        )
    )
    planning_selection_decision_key: NonEmptyStr
    planning_selection_available_at: datetime
    forecast_version: NonEmptyStr
    repair_model_version: NonEmptyStr
    tenant_policy_version: NonEmptyStr
    candidate_planner_version: NonEmptyStr
    objective_version: NonEmptyStr
    artifacts: tuple[ReplayInputArtifact, ...]
    solver: SolverEvidence

    @field_validator("planning_selection_available_at")
    @classmethod
    def _selection_timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _complete_policy_domains(self) -> ReplayPolicySourceRecord:
        kinds = [artifact.kind for artifact in self.artifacts]
        if (
            set(kinds) != POLICY_SOURCE_KINDS
            or len(kinds) != len(POLICY_SOURCE_KINDS)
        ):
            raise ValueError(
                "policy source artifacts require exactly model, "
                "tenant_policy, objective, and candidate_frontier"
            )
        return self


class ReplayMatchedSourceRecord(_ReplaySourceBase):
    """Explicit source-domain facts for one candidate matched observation."""

    row_kind: Literal["matched"] = "matched"
    observation_id: NonEmptyStr
    tenant_id: NonEmptyStr
    decision_key: NonEmptyStr
    as_of: datetime
    horizon_end: datetime
    cohort: ReplayCohort
    factual_artifacts: tuple[ReplayInputArtifact, ...]
    current: ReplayPolicySourceRecord
    challenger: ReplayPolicySourceRecord
    outcome_lineage: ReplayOutcomeLineage

    @field_validator("as_of", "horizon_end")
    @classmethod
    def _record_timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _complete_factual_domains(self) -> ReplayMatchedSourceRecord:
        kinds = [artifact.kind for artifact in self.factual_artifacts]
        if (
            set(kinds) != FACTUAL_SOURCE_KINDS
            or len(kinds) != len(FACTUAL_SOURCE_KINDS)
        ):
            raise ValueError(
                "factual source artifacts require exactly demand, receipt, "
                "repair_outcome, price, and part_attributes"
            )
        return self


class ReplayExcludedSourceRecord(_ReplaySourceBase):
    """One historical decision excluded by a stable reviewed reason."""

    row_kind: Literal["excluded"] = "excluded"
    observation_id: NonEmptyStr
    tenant_id: NonEmptyStr
    decision_key: NonEmptyStr
    as_of: datetime
    horizon_end: datetime
    reason_code: ReplayExclusionReason
    detail: NonEmptyStr

    @field_validator("as_of", "horizon_end")
    @classmethod
    def _record_timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc(value)


ReplaySourceRecord = Annotated[
    ReplayMatchedSourceRecord | ReplayExcludedSourceRecord,
    Field(discriminator="row_kind"),
]


class TrustedReplaySourcePackage(_ReplaySourceBase):
    """Controlled repository/data-pipeline input, never a browser contract."""

    tenant_id: NonEmptyStr
    currency: CurrencyCode
    universe_id: NonEmptyStr
    current_policy_label: NonEmptyStr = "current"
    challenger_policy_label: NonEmptyStr = "repair-aware"
    comparison_rule: ComparisonRule = "matched_budget"
    match_tolerance: NonNegativeDecimal = Decimal("0")
    records: tuple[ReplaySourceRecord, ...] = Field(min_length=1)


def _lineage(
    record: ReplayMatchedSourceRecord,
    policy: ReplayPolicySourceRecord,
) -> ReplayDecisionLineage:
    return ReplayDecisionLineage(
        tenant_id=record.tenant_id,
        as_of=record.as_of,
        source_snapshot_hash=policy.source_snapshot_hash,
        planning_fingerprint=policy.planning_fingerprint,
        planning_request_sha256=policy.planning_request_sha256,
        planning_run_id=policy.planning_run_id,
        planning_selection_decision_key=(
            policy.planning_selection_decision_key
        ),
        planning_selection_available_at=(
            policy.planning_selection_available_at
        ),
        forecast_version=policy.forecast_version,
        repair_model_version=policy.repair_model_version,
        tenant_policy_version=policy.tenant_policy_version,
        candidate_planner_version=policy.candidate_planner_version,
        objective_version=policy.objective_version,
        artifacts=(*record.factual_artifacts, *policy.artifacts),
        solver=policy.solver,
    )


def _cutoff_violations(
    record: ReplayMatchedSourceRecord,
) -> tuple[str, ...]:
    violations = []
    for artifact in record.factual_artifacts:
        if artifact.available_at > record.as_of or (
            artifact.occurred_at is not None
            and artifact.occurred_at > record.as_of
        ):
            violations.append(
                f"factual.{artifact.kind}:{artifact.artifact_id}"
            )
    for side, policy in (
        ("current", record.current),
        ("challenger", record.challenger),
    ):
        if policy.planning_selection_available_at > record.as_of:
            violations.append(
                f"{side}.planning_selection:"
                f"{policy.planning_run_id}/"
                f"{policy.planning_selection_decision_key}"
            )
        for artifact in policy.artifacts:
            if artifact.available_at > record.as_of or (
                artifact.occurred_at is not None
                and artifact.occurred_at > record.as_of
            ):
                violations.append(
                    f"{side}.{artifact.kind}:{artifact.artifact_id}"
                )
    return tuple(sorted(violations))


def build_trusted_replay_request(
    source: TrustedReplaySourcePackage,
) -> ReplayEvaluationRequest:
    """Build a contract-validated request from controlled historical records."""

    observations = []
    exclusions = []
    for record in source.records:
        if isinstance(record, ReplayExcludedSourceRecord):
            exclusions.append(
                ReplayExclusion(
                    observation_id=record.observation_id,
                    tenant_id=record.tenant_id,
                    decision_key=record.decision_key,
                    as_of=record.as_of,
                    horizon_end=record.horizon_end,
                    reason_code=record.reason_code,
                    detail=record.detail,
                )
            )
            continue
        violations = _cutoff_violations(record)
        if violations:
            exclusions.append(
                ReplayExclusion(
                    observation_id=record.observation_id,
                    tenant_id=record.tenant_id,
                    decision_key=record.decision_key,
                    as_of=record.as_of,
                    horizon_end=record.horizon_end,
                    reason_code="invalid_lineage",
                    detail=(
                        "no_lookahead_cutoff_violation:"
                        + ",".join(violations)
                    ),
                )
            )
            continue
        observations.append(
            MatchedReplayObservation(
                observation_id=record.observation_id,
                tenant_id=record.tenant_id,
                decision_key=record.decision_key,
                as_of=record.as_of,
                horizon_end=record.horizon_end,
                cohort=record.cohort,
                current=record.current.metrics,
                challenger=record.challenger.metrics,
                current_lineage=_lineage(record, record.current),
                challenger_lineage=_lineage(record, record.challenger),
                outcome_lineage=record.outcome_lineage,
            )
        )
    decisions = tuple(
        ReplayUniverseDecision(
            observation_id=record.observation_id,
            tenant_id=record.tenant_id,
            decision_key=record.decision_key,
            as_of=record.as_of,
            horizon_end=record.horizon_end,
        )
        for record in source.records
    )
    return ReplayEvaluationRequest(
        tenant_id=source.tenant_id,
        currency=source.currency,
        universe_id=source.universe_id,
        universe_sha256=replay_universe_sha256(
            universe_id=source.universe_id,
            tenant_id=source.tenant_id,
            decisions=decisions,
        ),
        expected_decision_count=len(decisions),
        universe_decisions=decisions,
        current_policy_label=source.current_policy_label,
        challenger_policy_label=source.challenger_policy_label,
        comparison_rule=source.comparison_rule,
        match_tolerance=source.match_tolerance,
        observations=tuple(observations),
        exclusions=tuple(exclusions),
    )


def build_trusted_replay_package_file(
    *,
    input_path: str | Path,
    output_path: str | Path,
) -> ReplayEvaluationRequest:
    """Validate explicit source JSON and write an import-ready replay request."""

    source = TrustedReplaySourcePackage.model_validate_json(
        Path(input_path).read_text(encoding="utf-8")
    )
    request = build_trusted_replay_request(source)
    Path(output_path).write_text(
        request.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return request


@click.command(name="trax-io-replay-build")
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(
        exists=True,
        file_okay=True,
        dir_okay=False,
        path_type=Path,
    ),
    help="Controlled replay-source.v1 JSON file.",
)
@click.option(
    "--output",
    "output_path",
    required=True,
    type=click.Path(file_okay=True, dir_okay=False, path_type=Path),
    help="Destination ReplayEvaluationRequest JSON file.",
)
def main(input_path: Path, output_path: Path) -> None:
    """Build an import-ready replay request outside the browser boundary."""

    try:
        request = build_trusted_replay_package_file(
            input_path=input_path,
            output_path=output_path,
        )
    except Exception as exc:
        raise click.ClickException(
            "trusted replay package build failed; "
            "review controlled operator diagnostics"
        ) from exc
    click.echo(
        "trusted replay package built: "
        f"{request.expected_decision_count} decisions, "
        f"{len(request.observations)} matched, "
        f"{len(request.exclusions)} excluded"
    )


__all__ = [
    "REPLAY_SOURCE_PACKAGE_VERSION",
    "ReplayExcludedSourceRecord",
    "ReplayMatchedSourceRecord",
    "ReplayPolicySourceRecord",
    "TrustedReplaySourcePackage",
    "build_trusted_replay_package_file",
    "build_trusted_replay_request",
    "main",
]


if __name__ == "__main__":
    main()
