"""No-lookahead replay and advisory shadow-scorecard contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from trax_io_reco.contracts.candidate import (
    CurrencyCode,
    ExactDecimal,
    NonEmptyStr,
    NonNegativeDecimal,
    UnitIntervalDecimal,
)
from trax_io_reco.contracts.planning import SolverEvidence

REPLAY_CONTRACT_VERSION = "replay.v1"
ComparisonRule = Literal["matched_budget", "matched_service"]
ReplayExclusionReason = Literal[
    "missing_snapshot",
    "incomplete_horizon",
    "missing_price",
    "missing_metrics",
    "invalid_lineage",
    "unmatched_budget",
    "unmatched_service",
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
DecisionArtifactKind = Literal[
    "demand",
    "receipt",
    "repair_outcome",
    "price",
    "part_attributes",
    "model",
    "tenant_policy",
    "objective",
    "candidate_frontier",
]
OutcomeArtifactKind = Literal["demand", "receipt", "repair_outcome"]
ArtifactStatus = Literal["available", "not_applicable", "unavailable"]

REQUIRED_DECISION_ARTIFACT_KINDS = frozenset(
    {
        "demand",
        "receipt",
        "repair_outcome",
        "price",
        "part_attributes",
        "model",
        "tenant_policy",
        "objective",
        "candidate_frontier",
    }
)
REQUIRED_OUTCOME_ARTIFACT_KINDS = frozenset(
    {"demand", "receipt", "repair_outcome"}
)
REQUIRED_REPLAY_METRICS = frozenset(
    {
        "demanded_units",
        "filled_units",
        "backordered_units",
        "shortage_unit_days",
        "ending_inventory_units",
        "inventory_investment",
        "holding_cost",
        "ordering_cost",
        "acquisition_cash",
        "aog_risk_proxy_events",
        "decision_count",
        "fill_rate",
    }
)
COMPARISON_RULE_DEFINITIONS = {
    "matched_budget": (
        "Compare policies at equal aggregate acquisition cash within the "
        "configured tolerance."
    ),
    "matched_service": (
        "Compare policies at equal aggregate fill rate within the "
        "configured tolerance."
    ),
}


class _ReplayBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    contract_version: Literal["replay.v1"] = REPLAY_CONTRACT_VERSION


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("replay timestamps must include a timezone")
    return value.astimezone(UTC)


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class ReplayInputArtifact(_ReplayBase):
    """One complete decision-input manifest entry, including explicit absence."""

    artifact_id: NonEmptyStr
    tenant_id: NonEmptyStr
    kind: DecisionArtifactKind
    status: ArtifactStatus
    source_snapshot_hash: NonEmptyStr
    content_sha256: Sha256 | None = None
    occurred_at: datetime | None = None
    available_at: datetime
    versions: tuple[NonEmptyStr, ...] = ()
    reason: NonEmptyStr | None = None

    @field_validator("occurred_at", "available_at")
    @classmethod
    def _timestamp_is_utc(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def _artifact_state(self) -> Self:
        if self.versions != tuple(sorted(set(self.versions))):
            raise ValueError("artifact versions must be unique and sorted")
        if self.status == "available":
            if self.content_sha256 is None or self.occurred_at is None:
                raise ValueError(
                    "available decision artifact requires content hash and occurrence"
                )
            if self.available_at < self.occurred_at:
                raise ValueError("artifact cannot be available before it occurred")
        elif (
            self.content_sha256 is not None
            or self.occurred_at is not None
            or self.reason is None
        ):
            raise ValueError(
                "absent decision artifact requires only an explicit reason"
            )
        return self

    @property
    def factual_signature(self) -> tuple[object, ...]:
        return (
            self.kind,
            self.status,
            self.source_snapshot_hash,
            self.content_sha256,
            self.occurred_at,
            self.available_at,
        )


class ReplayDecisionLineage(_ReplayBase):
    """Complete immutable decision lineage validated at the historical cutoff."""

    tenant_id: NonEmptyStr
    as_of: datetime
    source_snapshot_hash: NonEmptyStr
    planning_fingerprint: str = Field(pattern=r"^planning_[0-9a-f]{64}$")
    planning_request_sha256: Sha256
    planning_run_id: NonEmptyStr | None = None
    planning_selection_decision_key: NonEmptyStr | None = None
    planning_selection_available_at: datetime | None = None
    forecast_version: NonEmptyStr
    repair_model_version: NonEmptyStr
    tenant_policy_version: NonEmptyStr
    candidate_planner_version: NonEmptyStr
    objective_version: NonEmptyStr
    artifacts: tuple[ReplayInputArtifact, ...]
    solver: SolverEvidence

    @field_validator("as_of", "planning_selection_available_at")
    @classmethod
    def _lineage_timestamp_is_utc(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def _no_lookahead(self) -> Self:
        link = (
            self.planning_run_id,
            self.planning_selection_decision_key,
            self.planning_selection_available_at,
        )
        if any(value is None for value in link) != all(
            value is None for value in link
        ):
            raise ValueError(
                "planning run id, selection key, and availability "
                "must be supplied together"
            )
        if (
            self.planning_selection_available_at is not None
            and self.planning_selection_available_at > self.as_of
        ):
            raise ValueError(
                "no-lookahead violation for planning selection link"
            )
        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        kinds = [artifact.kind for artifact in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("replay artifact ids must be unique")
        if set(kinds) != REQUIRED_DECISION_ARTIFACT_KINDS or len(kinds) != len(
            REQUIRED_DECISION_ARTIFACT_KINDS
        ):
            raise ValueError(
                "decision lineage requires exactly one manifest entry for every kind"
            )
        if any(
            artifact.tenant_id != self.tenant_id
            or artifact.source_snapshot_hash != self.source_snapshot_hash
            for artifact in self.artifacts
        ):
            raise ValueError("decision artifact tenant or snapshot mismatch")
        later = [
            artifact.artifact_id
            for artifact in self.artifacts
            if artifact.available_at > self.as_of
            or (
                artifact.occurred_at is not None
                and artifact.occurred_at > self.as_of
            )
        ]
        if later:
            raise ValueError(
                "no-lookahead violation for artifact(s): "
                + ", ".join(sorted(later))
            )
        by_kind = {artifact.kind: artifact for artifact in self.artifacts}
        for kind in (
            "demand",
            "price",
            "part_attributes",
            "model",
            "tenant_policy",
            "objective",
            "candidate_frontier",
        ):
            if by_kind[kind].status != "available":
                raise ValueError(f"evaluated decision requires available {kind}")
        expected_versions = {
            "model": {self.forecast_version, self.repair_model_version},
            "tenant_policy": {self.tenant_policy_version},
            "objective": {self.objective_version},
            "candidate_frontier": {self.candidate_planner_version},
        }
        for kind, expected in expected_versions.items():
            if not expected <= set(by_kind[kind].versions):
                raise ValueError(f"{kind} artifact does not bind declared version(s)")
        return self

    def factual_signature(self) -> tuple[tuple[object, ...], ...]:
        factual_kinds = {
            "demand",
            "receipt",
            "repair_outcome",
            "price",
            "part_attributes",
        }
        return tuple(
            artifact.factual_signature
            for artifact in sorted(self.artifacts, key=lambda item: item.kind)
            if artifact.kind in factual_kinds
        )


class ReplayOutcomeArtifact(_ReplayBase):
    """One realized factual dataset bounded to the evaluation horizon."""

    artifact_id: NonEmptyStr
    tenant_id: NonEmptyStr
    kind: OutcomeArtifactKind
    status: ArtifactStatus
    content_sha256: Sha256 | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    available_at: datetime
    reason: NonEmptyStr | None = None

    @field_validator("window_start", "window_end", "available_at")
    @classmethod
    def _outcome_timestamp_is_utc(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def _artifact_state(self) -> Self:
        if self.status == "available":
            if (
                self.content_sha256 is None
                or self.window_start is None
                or self.window_end is None
            ):
                raise ValueError(
                    "available outcome artifact requires hash and complete window"
                )
            if self.window_end < self.window_start:
                raise ValueError("outcome artifact window is reversed")
            if self.available_at < self.window_end:
                raise ValueError("outcome artifact cannot be available before window end")
        elif (
            self.content_sha256 is not None
            or self.window_start is not None
            or self.window_end is not None
            or self.reason is None
        ):
            raise ValueError(
                "absent outcome artifact requires only an explicit reason"
            )
        return self


def replay_outcome_manifest_sha256(
    *,
    tenant_id: str,
    as_of: datetime,
    horizon_end: datetime,
    artifacts: Iterable[ReplayOutcomeArtifact],
) -> str:
    """Hash the complete realized-outcome manifest, not a caller assertion."""

    canonical_artifacts = sorted(
        (
            artifact.model_dump(mode="json")
            for artifact in artifacts
        ),
        key=lambda artifact: (
            artifact["kind"],
            artifact["artifact_id"],
        ),
    )
    return _canonical_sha256(
        {
            "namespace": "trax-io-replay-outcome-manifest-v1",
            "tenant_id": tenant_id,
            "as_of": _utc(as_of).isoformat(),
            "horizon_end": _utc(horizon_end).isoformat(),
            "artifacts": canonical_artifacts,
        }
    )


class ReplayOutcomeLineage(_ReplayBase):
    """Shared factual outcomes used to score both matched policies."""

    tenant_id: NonEmptyStr
    as_of: datetime
    horizon_end: datetime
    manifest_sha256: Sha256
    artifacts: tuple[ReplayOutcomeArtifact, ...]

    @field_validator("as_of", "horizon_end")
    @classmethod
    def _lineage_timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _bounded_outcomes(self) -> Self:
        if self.horizon_end <= self.as_of:
            raise ValueError("outcome horizon must end after as_of")
        kinds = [artifact.kind for artifact in self.artifacts]
        if set(kinds) != REQUIRED_OUTCOME_ARTIFACT_KINDS or len(kinds) != len(
            REQUIRED_OUTCOME_ARTIFACT_KINDS
        ):
            raise ValueError(
                "outcome lineage requires exactly one manifest entry for every kind"
            )
        ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(ids) != len(set(ids)):
            raise ValueError("outcome artifact ids must be unique")
        if any(artifact.tenant_id != self.tenant_id for artifact in self.artifacts):
            raise ValueError("outcome artifact tenant mismatch")
        for artifact in self.artifacts:
            if artifact.status != "available":
                continue
            assert artifact.window_start is not None
            assert artifact.window_end is not None
            if (
                artifact.window_start != self.as_of
                or artifact.window_end != self.horizon_end
            ):
                raise ValueError(
                    f"outcome artifact {artifact.artifact_id} must cover the "
                    "complete replay window"
                )
        demand = next(
            artifact for artifact in self.artifacts if artifact.kind == "demand"
        )
        if demand.status != "available":
            raise ValueError("evaluated outcome requires realized demand")
        expected_manifest = replay_outcome_manifest_sha256(
            tenant_id=self.tenant_id,
            as_of=self.as_of,
            horizon_end=self.horizon_end,
            artifacts=self.artifacts,
        )
        if self.manifest_sha256 != expected_manifest:
            raise ValueError("outcome manifest hash does not reconcile to artifacts")
        return self


class ReplayMetrics(_ReplayBase):
    """One policy's realized outcome over one or more completed horizons."""

    currency: CurrencyCode
    outcome_manifest_sha256: Sha256
    demanded_units: NonNegativeDecimal
    filled_units: NonNegativeDecimal
    backordered_units: NonNegativeDecimal
    shortage_unit_days: NonNegativeDecimal
    ending_inventory_units: NonNegativeDecimal
    inventory_investment: NonNegativeDecimal
    holding_cost: NonNegativeDecimal
    ordering_cost: NonNegativeDecimal
    acquisition_cash: NonNegativeDecimal
    aog_risk_proxy_events: NonNegativeDecimal
    decision_count: int = Field(ge=0)
    fill_rate: UnitIntervalDecimal

    @model_validator(mode="after")
    def _metrics_reconcile(self) -> Self:
        if self.filled_units > self.demanded_units:
            raise ValueError("filled units cannot exceed demanded units")
        if self.backordered_units != self.demanded_units - self.filled_units:
            raise ValueError("backordered units must equal demand less filled units")
        expected_fill = (
            Decimal("0")
            if self.decision_count == 0
            else (
                Decimal("1")
                if self.demanded_units == 0
                else self.filled_units / self.demanded_units
            )
        )
        if self.fill_rate != expected_fill:
            raise ValueError("fill rate does not reconcile to filled and demanded units")
        return self


class ReplayCohort(_ReplayBase):
    criticality_tier: int = Field(ge=1, le=5)
    demand_regime: Literal[
        "smooth",
        "intermittent",
        "lumpy",
        "erratic",
        "unknown",
    ]
    repairability: Literal[
        "repairable",
        "rotable",
        "expendable",
        "unknown",
    ]
    location_code: NonEmptyStr
    repair_data_confidence: Literal[
        "observed",
        "pooled",
        "proxy",
        "unavailable",
    ]
    evidence_artifact_id: NonEmptyStr

    @property
    def cohort_id(self) -> str:
        return "|".join(
            (
                f"criticality:{self.criticality_tier}",
                f"demand:{self.demand_regime}",
                f"repairability:{self.repairability}",
                f"location:{self.location_code}",
                f"repair-confidence:{self.repair_data_confidence}",
            )
        )


class MatchedReplayObservation(_ReplayBase):
    """One historical decision scored on shared facts for two advisory policies."""

    observation_id: NonEmptyStr
    tenant_id: NonEmptyStr
    decision_key: NonEmptyStr
    as_of: datetime
    horizon_end: datetime
    cohort: ReplayCohort
    current: ReplayMetrics
    challenger: ReplayMetrics
    current_lineage: ReplayDecisionLineage
    challenger_lineage: ReplayDecisionLineage
    outcome_lineage: ReplayOutcomeLineage

    @field_validator("as_of", "horizon_end")
    @classmethod
    def _observation_timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc(value)

    @property
    def natural_key(self) -> tuple[str, str, datetime, datetime]:
        return (self.tenant_id, self.decision_key, self.as_of, self.horizon_end)

    @model_validator(mode="after")
    def _observation_reconciles(self) -> Self:
        if self.horizon_end <= self.as_of:
            raise ValueError("replay horizon must end after as_of")
        if self.current.currency != self.challenger.currency:
            raise ValueError("matched replay policies must use one currency")
        if self.current.demanded_units != self.challenger.demanded_units:
            raise ValueError("matched replay policies must evaluate identical demand")
        if (
            self.current.decision_count != 1
            or self.challenger.decision_count != 1
        ):
            raise ValueError("one matched observation must represent one decision")
        for lineage in (self.current_lineage, self.challenger_lineage):
            if lineage.tenant_id != self.tenant_id:
                raise ValueError("replay lineage tenant does not match observation")
            if lineage.as_of != self.as_of:
                raise ValueError("replay lineage cutoff does not match observation")
            if (
                lineage.planning_selection_decision_key is not None
                and lineage.planning_selection_decision_key != self.decision_key
            ):
                raise ValueError(
                    "planning selection link does not match replay decision"
                )
        if (
            self.current_lineage.source_snapshot_hash
            != self.challenger_lineage.source_snapshot_hash
            or self.current_lineage.factual_signature()
            != self.challenger_lineage.factual_signature()
        ):
            raise ValueError(
                "matched policies must use the same historical factual evidence"
            )
        if (
            self.outcome_lineage.tenant_id != self.tenant_id
            or self.outcome_lineage.as_of != self.as_of
            or self.outcome_lineage.horizon_end != self.horizon_end
        ):
            raise ValueError("outcome lineage does not match observation window")
        if (
            self.current.outcome_manifest_sha256
            != self.outcome_lineage.manifest_sha256
            or self.challenger.outcome_manifest_sha256
            != self.outcome_lineage.manifest_sha256
        ):
            raise ValueError("replay metrics are not bound to outcome lineage")
        for lineage in (self.current_lineage, self.challenger_lineage):
            artifact = next(
                (
                    item
                    for item in lineage.artifacts
                    if item.artifact_id == self.cohort.evidence_artifact_id
                ),
                None,
            )
            if artifact is None or artifact.kind != "part_attributes":
                raise ValueError("replay cohort lacks as-of part-attribute evidence")
        return self


class ReplayExclusion(_ReplayBase):
    """One expected historical decision excluded with a stable reason."""

    observation_id: NonEmptyStr
    tenant_id: NonEmptyStr
    decision_key: NonEmptyStr
    as_of: datetime
    horizon_end: datetime
    reason_code: ReplayExclusionReason
    detail: NonEmptyStr

    @field_validator("as_of", "horizon_end")
    @classmethod
    def _exclusion_timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc(value)

    @property
    def natural_key(self) -> tuple[str, str, datetime, datetime]:
        return (self.tenant_id, self.decision_key, self.as_of, self.horizon_end)

    @model_validator(mode="after")
    def _valid_window(self) -> Self:
        if self.horizon_end <= self.as_of:
            raise ValueError("excluded replay horizon must end after as_of")
        return self


class ReplayUniverseDecision(_ReplayBase):
    """One exact historical decision declared by the trusted replay universe."""

    observation_id: NonEmptyStr
    tenant_id: NonEmptyStr
    decision_key: NonEmptyStr
    as_of: datetime
    horizon_end: datetime

    @field_validator("as_of", "horizon_end")
    @classmethod
    def _universe_timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc(value)

    @property
    def natural_key(self) -> tuple[str, str, datetime, datetime]:
        return (self.tenant_id, self.decision_key, self.as_of, self.horizon_end)

    @model_validator(mode="after")
    def _valid_window(self) -> Self:
        if self.horizon_end <= self.as_of:
            raise ValueError("universe replay horizon must end after as_of")
        return self


def replay_universe_sha256(
    *,
    universe_id: str,
    tenant_id: str,
    decisions: Iterable[ReplayUniverseDecision],
) -> str:
    """Hash the exact natural-key universe used for coverage accounting."""

    canonical = sorted(
        (
            {
                "observation_id": decision.observation_id,
                "tenant_id": decision.tenant_id,
                "decision_key": decision.decision_key,
                "as_of": decision.as_of.isoformat(),
                "horizon_end": decision.horizon_end.isoformat(),
            }
            for decision in decisions
        ),
        key=lambda decision: (
            decision["as_of"],
            decision["decision_key"],
            decision["observation_id"],
        ),
    )
    return _canonical_sha256(
        {
            "namespace": "trax-io-replay-universe-v1",
            "universe_id": universe_id,
            "tenant_id": tenant_id,
            "decisions": canonical,
        }
    )


class ReplayEvaluationRequest(_ReplayBase):
    tenant_id: NonEmptyStr
    currency: CurrencyCode
    universe_id: NonEmptyStr
    universe_sha256: Sha256
    expected_decision_count: int = Field(ge=1)
    universe_decisions: tuple[ReplayUniverseDecision, ...]
    current_policy_label: NonEmptyStr
    challenger_policy_label: NonEmptyStr
    comparison_rule: ComparisonRule
    match_tolerance: NonNegativeDecimal = Decimal("0")
    observations: tuple[MatchedReplayObservation, ...] = ()
    exclusions: tuple[ReplayExclusion, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _canonical_rows(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for field_name in (
            "universe_decisions",
            "observations",
            "exclusions",
        ):
            if field_name not in normalized:
                continue
            normalized[field_name] = tuple(
                sorted(
                    normalized[field_name],
                    key=lambda item: (
                        (
                            item.as_of
                            if isinstance(
                                item,
                                MatchedReplayObservation
                                | ReplayExclusion
                                | ReplayUniverseDecision,
                            )
                            else item["as_of"]
                        ),
                        (
                            item.decision_key
                            if isinstance(
                                item,
                                MatchedReplayObservation
                                | ReplayExclusion
                                | ReplayUniverseDecision,
                            )
                            else item["decision_key"]
                        ),
                        (
                            item.observation_id
                            if isinstance(
                                item,
                                MatchedReplayObservation
                                | ReplayExclusion
                                | ReplayUniverseDecision,
                            )
                            else item["observation_id"]
                        ),
                    ),
                )
            )
        return normalized

    @model_validator(mode="after")
    def _matched_request(self) -> Self:
        rows = (*self.observations, *self.exclusions)
        if len(self.universe_decisions) != self.expected_decision_count:
            raise ValueError(
                "declared historical universe count does not reconcile"
            )
        if any(
            decision.tenant_id != self.tenant_id
            for decision in self.universe_decisions
        ):
            raise ValueError("replay universe tenant does not match request")
        universe_ids = [
            decision.observation_id for decision in self.universe_decisions
        ]
        universe_keys = [
            decision.natural_key for decision in self.universe_decisions
        ]
        if len(universe_ids) != len(set(universe_ids)):
            raise ValueError("replay universe observation ids must be unique")
        if len(universe_keys) != len(set(universe_keys)):
            raise ValueError("replay universe natural decision keys must be unique")
        expected_universe_hash = replay_universe_sha256(
            universe_id=self.universe_id,
            tenant_id=self.tenant_id,
            decisions=self.universe_decisions,
        )
        if self.universe_sha256 != expected_universe_hash:
            raise ValueError("replay universe hash does not reconcile")
        if len(rows) != self.expected_decision_count:
            raise ValueError(
                "replay rows must cover the declared historical decision universe"
            )
        ids = [row.observation_id for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("replay observation ids must be unique")
        natural_keys = [row.natural_key for row in rows]
        if len(natural_keys) != len(set(natural_keys)):
            raise ValueError("replay natural decision keys must be unique")
        if any(row.tenant_id != self.tenant_id for row in rows):
            raise ValueError("replay row tenant does not match request")
        row_manifest = {
            (row.observation_id, row.natural_key) for row in rows
        }
        universe_manifest = {
            (decision.observation_id, decision.natural_key)
            for decision in self.universe_decisions
        }
        if row_manifest != universe_manifest:
            raise ValueError(
                "replay rows do not match the exact historical universe manifest"
            )
        if any(
            observation.current.currency != self.currency
            for observation in self.observations
        ):
            raise ValueError("replay observation currency does not match request")
        current_cash = sum(
            (
                observation.current.acquisition_cash
                for observation in self.observations
            ),
            Decimal("0"),
        )
        challenger_cash = sum(
            (
                observation.challenger.acquisition_cash
                for observation in self.observations
            ),
            Decimal("0"),
        )
        current_demand = sum(
            (observation.current.demanded_units for observation in self.observations),
            Decimal("0"),
        )
        challenger_demand = sum(
            (
                observation.challenger.demanded_units
                for observation in self.observations
            ),
            Decimal("0"),
        )
        current_filled = sum(
            (observation.current.filled_units for observation in self.observations),
            Decimal("0"),
        )
        challenger_filled = sum(
            (
                observation.challenger.filled_units
                for observation in self.observations
            ),
            Decimal("0"),
        )
        current_service = (
            Decimal("0")
            if not self.observations
            else (
                Decimal("1")
                if current_demand == 0
                else current_filled / current_demand
            )
        )
        challenger_service = (
            Decimal("0")
            if not self.observations
            else (
                Decimal("1")
                if challenger_demand == 0
                else challenger_filled / challenger_demand
            )
        )
        difference = (
            abs(current_cash - challenger_cash)
            if self.comparison_rule == "matched_budget"
            else abs(current_service - challenger_service)
        )
        if difference > self.match_tolerance:
            raise ValueError(
                f"{self.comparison_rule} comparison exceeds configured tolerance"
            )
        return self


class ReplayMetricDelta(_ReplayBase):
    fill_rate: ExactDecimal
    backordered_units: ExactDecimal
    shortage_unit_days: ExactDecimal
    inventory_investment: ExactDecimal
    holding_cost: ExactDecimal
    ordering_cost: ExactDecimal
    acquisition_cash: ExactDecimal
    aog_risk_proxy_events: ExactDecimal


def expected_metric_delta(
    current: ReplayMetrics,
    challenger: ReplayMetrics,
) -> ReplayMetricDelta:
    return ReplayMetricDelta(
        fill_rate=challenger.fill_rate - current.fill_rate,
        backordered_units=(
            challenger.backordered_units - current.backordered_units
        ),
        shortage_unit_days=(
            challenger.shortage_unit_days - current.shortage_unit_days
        ),
        inventory_investment=(
            challenger.inventory_investment - current.inventory_investment
        ),
        holding_cost=challenger.holding_cost - current.holding_cost,
        ordering_cost=challenger.ordering_cost - current.ordering_cost,
        acquisition_cash=challenger.acquisition_cash - current.acquisition_cash,
        aog_risk_proxy_events=(
            challenger.aog_risk_proxy_events - current.aog_risk_proxy_events
        ),
    )


class ReplayCohortResult(_ReplayBase):
    cohort_id: NonEmptyStr
    cohort: ReplayCohort
    observation_count: int = Field(ge=1)
    observation_ids: tuple[NonEmptyStr, ...]
    current: ReplayMetrics
    challenger: ReplayMetrics
    delta: ReplayMetricDelta

    @model_validator(mode="after")
    def _cohort_reconciles(self) -> Self:
        if self.cohort_id != self.cohort.cohort_id:
            raise ValueError("replay cohort id does not reconcile")
        if (
            len(self.observation_ids) != self.observation_count
            or self.observation_ids != tuple(sorted(set(self.observation_ids)))
        ):
            raise ValueError("replay cohort observation ids do not reconcile")
        if self.current.currency != self.challenger.currency:
            raise ValueError("replay cohort currencies do not match")
        if (
            self.current.decision_count != self.observation_count
            or self.challenger.decision_count != self.observation_count
        ):
            raise ValueError("replay cohort decision count does not reconcile")
        if self.delta != expected_metric_delta(self.current, self.challenger):
            raise ValueError("replay cohort delta does not reconcile")
        return self


class ReplayMetricDefinition(_ReplayBase):
    metric: NonEmptyStr
    unit: NonEmptyStr
    denominator: NonEmptyStr
    exclusions: NonEmptyStr


class ReplayExclusionCount(_ReplayBase):
    reason_code: NonEmptyStr
    count: int = Field(ge=1)


class ReplayObservationLineageRef(_ReplayBase):
    observation_id: NonEmptyStr
    decision_key: NonEmptyStr
    as_of: datetime
    horizon_end: datetime
    cohort_id: NonEmptyStr
    source_snapshot_hash: NonEmptyStr
    outcome_manifest_sha256: Sha256
    current_planning_fingerprint: str = Field(
        pattern=r"^planning_[0-9a-f]{64}$"
    )
    challenger_planning_fingerprint: str = Field(
        pattern=r"^planning_[0-9a-f]{64}$"
    )
    current_request_sha256: Sha256
    challenger_request_sha256: Sha256
    current_planning_run_id: NonEmptyStr | None = None
    current_planning_selection_decision_key: NonEmptyStr | None = None
    challenger_planning_run_id: NonEmptyStr | None = None
    challenger_planning_selection_decision_key: NonEmptyStr | None = None

    @field_validator("as_of", "horizon_end")
    @classmethod
    def _lineage_ref_timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _lineage_ref_window(self) -> Self:
        if self.horizon_end <= self.as_of:
            raise ValueError("lineage reference horizon must end after as_of")
        for side, run_id, selection_key in (
            (
                "current",
                self.current_planning_run_id,
                self.current_planning_selection_decision_key,
            ),
            (
                "challenger",
                self.challenger_planning_run_id,
                self.challenger_planning_selection_decision_key,
            ),
        ):
            if (run_id is None) != (selection_key is None):
                raise ValueError(
                    f"{side} lineage planning selection link is incomplete"
                )
            if (
                selection_key is not None
                and selection_key != self.decision_key
            ):
                raise ValueError(
                    f"{side} lineage planning selection link "
                    "does not match replay decision"
                )
        return self


def combined_outcome_manifest_sha256(
    lineage: Iterable[tuple[str, str]],
) -> str:
    """Bind aggregate metrics to exact observation ids and outcome manifests."""

    return _canonical_sha256(
        {
            "namespace": "trax-io-replay-aggregate-outcomes-v1",
            "observations": [
                {
                    "observation_id": observation_id,
                    "outcome_manifest_sha256": manifest_sha256,
                }
                for observation_id, manifest_sha256 in sorted(lineage)
            ],
        }
    )


class ShadowScorecard(_ReplayBase):
    tenant_id: NonEmptyStr
    currency: CurrencyCode
    universe_id: NonEmptyStr
    universe_sha256: Sha256
    universe_decisions: tuple[ReplayUniverseDecision, ...]
    current_policy_label: NonEmptyStr
    challenger_policy_label: NonEmptyStr
    comparison_rule: ComparisonRule
    comparison_rule_definition: NonEmptyStr
    match_tolerance: NonNegativeDecimal
    advisory_only: Literal[True] = True
    observation_count: int = Field(ge=0)
    total_observation_count: int = Field(ge=1)
    excluded_observation_count: int = Field(ge=0)
    coverage_rate: UnitIntervalDecimal
    exclusions_by_reason: tuple[ReplayExclusionCount, ...] = ()
    exclusions: tuple[ReplayExclusion, ...] = ()
    current: ReplayMetrics
    challenger: ReplayMetrics
    delta: ReplayMetricDelta
    cohorts: tuple[ReplayCohortResult, ...]
    metric_definitions: tuple[ReplayMetricDefinition, ...]
    observation_lineage: tuple[ReplayObservationLineageRef, ...]
    source_snapshot_hashes: tuple[NonEmptyStr, ...]
    planning_fingerprints: tuple[str, ...]

    @model_validator(mode="after")
    def _scorecard_reconciles(self) -> Self:
        if (
            self.comparison_rule_definition
            != COMPARISON_RULE_DEFINITIONS[self.comparison_rule]
        ):
            raise ValueError("comparison rule definition does not reconcile")
        if (
            self.observation_count + self.excluded_observation_count
            != self.total_observation_count
        ):
            raise ValueError("replay evaluated and excluded counts do not reconcile")
        if len(self.universe_decisions) != self.total_observation_count:
            raise ValueError("scorecard universe count does not reconcile")
        if any(
            decision.tenant_id != self.tenant_id
            for decision in self.universe_decisions
        ):
            raise ValueError("scorecard universe tenant does not reconcile")
        if self.universe_sha256 != replay_universe_sha256(
            universe_id=self.universe_id,
            tenant_id=self.tenant_id,
            decisions=self.universe_decisions,
        ):
            raise ValueError("scorecard universe hash does not reconcile")
        if len(self.exclusions) != self.excluded_observation_count:
            raise ValueError("scorecard exclusion ledger does not reconcile")
        expected_coverage = (
            Decimal(self.observation_count) / Decimal(self.total_observation_count)
        )
        if self.coverage_rate != expected_coverage:
            raise ValueError("replay coverage rate does not reconcile")
        if sum(item.count for item in self.exclusions_by_reason) != (
            self.excluded_observation_count
        ):
            raise ValueError("replay exclusion reason counts do not reconcile")
        actual_exclusion_counts: dict[str, int] = {}
        for exclusion in self.exclusions:
            actual_exclusion_counts[exclusion.reason_code] = (
                actual_exclusion_counts.get(exclusion.reason_code, 0) + 1
            )
        if {
            item.reason_code: item.count for item in self.exclusions_by_reason
        } != actual_exclusion_counts:
            raise ValueError("scorecard exclusion reasons do not match ledger")
        reason_codes = [item.reason_code for item in self.exclusions_by_reason]
        if (
            len(reason_codes) != len(set(reason_codes))
            or reason_codes != sorted(reason_codes)
        ):
            raise ValueError("replay exclusion reason codes must be unique and sorted")
        if (
            self.current.currency != self.currency
            or self.challenger.currency != self.currency
            or any(
                cohort.current.currency != self.currency
                or cohort.challenger.currency != self.currency
                for cohort in self.cohorts
            )
        ):
            raise ValueError("scorecard currencies do not reconcile")
        if (
            self.current.decision_count != self.observation_count
            or self.challenger.decision_count != self.observation_count
        ):
            raise ValueError("scorecard decision counts do not reconcile")
        if self.delta != expected_metric_delta(self.current, self.challenger):
            raise ValueError("scorecard delta does not reconcile")
        cohort_ids = [cohort.cohort_id for cohort in self.cohorts]
        if (
            len(cohort_ids) != len(set(cohort_ids))
            or cohort_ids != sorted(cohort_ids)
        ):
            raise ValueError("scorecard cohort ids must be unique and sorted")
        if sum(cohort.observation_count for cohort in self.cohorts) != (
            self.observation_count
        ):
            raise ValueError("cohort observation counts do not reconcile")
        for name in (
            "demanded_units",
            "filled_units",
            "backordered_units",
            "shortage_unit_days",
            "ending_inventory_units",
            "inventory_investment",
            "holding_cost",
            "ordering_cost",
            "acquisition_cash",
            "aog_risk_proxy_events",
        ):
            if sum(
                (getattr(cohort.current, name) for cohort in self.cohorts),
                Decimal("0"),
            ) != getattr(self.current, name):
                raise ValueError(f"current cohort {name} does not reconcile")
            if sum(
                (getattr(cohort.challenger, name) for cohort in self.cohorts),
                Decimal("0"),
            ) != getattr(self.challenger, name):
                raise ValueError(f"challenger cohort {name} does not reconcile")
        definition_names = [
            definition.metric for definition in self.metric_definitions
        ]
        if (
            set(definition_names) != REQUIRED_REPLAY_METRICS
            or len(definition_names) != len(REQUIRED_REPLAY_METRICS)
        ):
            raise ValueError("scorecard metric definitions are incomplete")
        lineage_ids = [lineage.observation_id for lineage in self.observation_lineage]
        if (
            len(lineage_ids) != self.observation_count
            or lineage_ids != sorted(set(lineage_ids))
        ):
            raise ValueError("scorecard observation lineage is incomplete")
        declared_rows = {
            (
                decision.observation_id,
                decision.natural_key,
            )
            for decision in self.universe_decisions
        }
        evaluated_rows = {
            (
                lineage.observation_id,
                (
                    self.tenant_id,
                    lineage.decision_key,
                    lineage.as_of,
                    lineage.horizon_end,
                ),
            )
            for lineage in self.observation_lineage
        }
        excluded_rows = {
            (exclusion.observation_id, exclusion.natural_key)
            for exclusion in self.exclusions
        }
        if len(excluded_rows) != len(self.exclusions):
            raise ValueError("scorecard exclusions contain duplicate decisions")
        represented_rows = evaluated_rows | excluded_rows
        if represented_rows != declared_rows:
            raise ValueError(
                "scorecard rows do not cover the exact historical universe"
            )
        expected_outcome_hash = combined_outcome_manifest_sha256(
            (
                lineage.observation_id,
                lineage.outcome_manifest_sha256,
            )
            for lineage in self.observation_lineage
        )
        if (
            self.current.outcome_manifest_sha256 != expected_outcome_hash
            or self.challenger.outcome_manifest_sha256 != expected_outcome_hash
        ):
            raise ValueError(
                "scorecard aggregate metrics do not reconcile to outcome lineage"
            )
        cohort_observation_ids = [
            observation_id
            for cohort in self.cohorts
            for observation_id in cohort.observation_ids
        ]
        if sorted(cohort_observation_ids) != lineage_ids:
            raise ValueError(
                "scorecard cohorts do not partition observation lineage"
            )
        lineage_by_id = {
            lineage.observation_id: lineage for lineage in self.observation_lineage
        }
        for cohort in self.cohorts:
            if any(
                lineage_by_id[observation_id].cohort_id != cohort.cohort_id
                for observation_id in cohort.observation_ids
            ):
                raise ValueError(
                    "scorecard cohort membership does not reconcile to lineage"
                )
            cohort_hash = combined_outcome_manifest_sha256(
                (
                    observation_id,
                    lineage_by_id[observation_id].outcome_manifest_sha256,
                )
                for observation_id in cohort.observation_ids
            )
            if (
                cohort.current.outcome_manifest_sha256 != cohort_hash
                or cohort.challenger.outcome_manifest_sha256 != cohort_hash
            ):
                raise ValueError(
                    "scorecard cohort metrics do not reconcile to outcome lineage"
                )
        expected_snapshots = tuple(
            sorted(
                {
                    lineage.source_snapshot_hash
                    for lineage in self.observation_lineage
                }
            )
        )
        if self.source_snapshot_hashes != expected_snapshots:
            raise ValueError("scorecard source snapshots do not reconcile")
        expected_fingerprints = tuple(
            sorted(
                {
                    fingerprint
                    for lineage in self.observation_lineage
                    for fingerprint in (
                        lineage.current_planning_fingerprint,
                        lineage.challenger_planning_fingerprint,
                    )
                }
            )
        )
        if self.planning_fingerprints != expected_fingerprints:
            raise ValueError("scorecard planning fingerprints do not reconcile")
        return self


__all__ = [
    "COMPARISON_RULE_DEFINITIONS",
    "combined_outcome_manifest_sha256",
    "MatchedReplayObservation",
    "REPLAY_CONTRACT_VERSION",
    "REQUIRED_REPLAY_METRICS",
    "ReplayCohort",
    "ReplayCohortResult",
    "ReplayDecisionLineage",
    "ReplayEvaluationRequest",
    "ReplayExclusion",
    "ReplayExclusionCount",
    "ReplayExclusionReason",
    "ReplayInputArtifact",
    "ReplayMetricDefinition",
    "ReplayMetricDelta",
    "ReplayMetrics",
    "ReplayObservationLineageRef",
    "ReplayOutcomeArtifact",
    "ReplayOutcomeLineage",
    "ReplayUniverseDecision",
    "ShadowScorecard",
    "expected_metric_delta",
    "replay_outcome_manifest_sha256",
    "replay_universe_sha256",
]
