from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trax_io_reco.contracts.planning import SolverEvidence
from trax_io_reco.contracts.replay import (
    MatchedReplayObservation,
    ReplayCohort,
    ReplayDecisionLineage,
    ReplayEvaluationRequest,
    ReplayExclusion,
    ReplayInputArtifact,
    ReplayMetrics,
    ReplayOutcomeArtifact,
    ReplayOutcomeLineage,
    ReplayUniverseDecision,
    replay_outcome_manifest_sha256,
    replay_universe_sha256,
)
from trax_io_reco.replay.package import (
    ReplayMatchedSourceRecord,
    ReplayPolicySourceRecord,
    TrustedReplaySourcePackage,
)

_FACTUAL_KINDS = {
    "demand",
    "receipt",
    "repair_outcome",
    "price",
    "part_attributes",
}


def replay_request(
    tenant_id: str,
    *,
    universe_id: str = "historical-decisions-2026q1",
    decision_count: int = 1,
) -> ReplayEvaluationRequest:
    """Small complete replay universe for API/persistence integration tests."""

    if decision_count < 1:
        raise ValueError("decision_count must be positive")
    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    decisions = tuple(
        ReplayUniverseDecision(
            observation_id=f"{universe_id}-excluded-{index:05d}",
            tenant_id=tenant_id,
            decision_key=f"PN-{index:05d}@MIA",
            as_of=as_of,
            horizon_end=as_of + timedelta(days=30),
        )
        for index in range(decision_count)
    )
    return ReplayEvaluationRequest(
        tenant_id=tenant_id,
        currency="USD",
        universe_id=universe_id,
        universe_sha256=replay_universe_sha256(
            universe_id=universe_id,
            tenant_id=tenant_id,
            decisions=decisions,
        ),
        expected_decision_count=decision_count,
        universe_decisions=decisions,
        current_policy_label="current",
        challenger_policy_label="repair-aware",
        comparison_rule="matched_budget",
        observations=(),
        exclusions=tuple(
            ReplayExclusion(
                observation_id=decision.observation_id,
                tenant_id=tenant_id,
                decision_key=decision.decision_key,
                as_of=decision.as_of,
                horizon_end=decision.horizon_end,
                reason_code="incomplete_horizon",
                detail="The realized evaluation horizon is incomplete.",
            )
            for decision in decisions
        ),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _solver() -> SolverEvidence:
    return SolverEvidence(
        implementation="scipy.optimize.milp/highs",
        implementation_version="1.16.0",
        termination="optimal",
        optimality_proven=True,
        objective=Decimal("1"),
        objective_bound=Decimal("1"),
        relative_gap=Decimal("0"),
        duration_ms=Decimal("5"),
        node_count=1,
        message="Optimal",
    )


def _input_artifacts(
    *,
    tenant_id: str,
    as_of: datetime,
    policy_suffix: str,
) -> tuple[ReplayInputArtifact, ...]:
    factual_kinds = {
        "demand",
        "receipt",
        "repair_outcome",
        "price",
        "part_attributes",
    }
    artifacts = []
    for index, kind in enumerate(
        (
            "demand",
            "receipt",
            "repair_outcome",
            "price",
            "part_attributes",
            "model",
            "tenant_policy",
            "objective",
            "candidate_frontier",
        )
    ):
        suffix = "f" if kind in factual_kinds else policy_suffix
        status = (
            "not_applicable"
            if kind in {"receipt", "repair_outcome"}
            else "available"
        )
        versions = {
            "model": (
                f"forecast-{policy_suffix}",
                f"repair-{policy_suffix}",
            ),
            "tenant_policy": (f"policy-{policy_suffix}",),
            "objective": (f"objective-{policy_suffix}",),
            "candidate_frontier": ("candidate-planner-v1",),
        }.get(kind, ())
        artifacts.append(
            ReplayInputArtifact(
                artifact_id=(
                    "part-attributes-shared"
                    if kind == "part_attributes"
                    else f"{kind}-{suffix}"
                ),
                tenant_id=tenant_id,
                kind=kind,
                status=status,
                source_snapshot_hash="snapshot-shared",
                content_sha256=(
                    _sha256(chr(97 + index))
                    if status == "available"
                    else None
                ),
                occurred_at=(
                    as_of - timedelta(days=index + 2)
                    if status == "available"
                    else None
                ),
                available_at=as_of - timedelta(days=1),
                versions=tuple(sorted(versions)),
                reason=(
                    "No applicable historical events."
                    if status != "available"
                    else None
                ),
            )
        )
    return tuple(artifacts)


def _lineage(
    *,
    tenant_id: str,
    as_of: datetime,
    suffix: str,
    planning_run_id: str | None = None,
    planning_selection_decision_key: str | None = None,
) -> ReplayDecisionLineage:
    return ReplayDecisionLineage(
        tenant_id=tenant_id,
        as_of=as_of,
        source_snapshot_hash="snapshot-shared",
        planning_fingerprint=f"planning_{suffix * 64}",
        planning_request_sha256=_sha256(suffix),
        planning_run_id=planning_run_id,
        planning_selection_decision_key=planning_selection_decision_key,
        planning_selection_available_at=(
            as_of - timedelta(minutes=1)
            if planning_run_id is not None
            else None
        ),
        forecast_version=f"forecast-{suffix}",
        repair_model_version=f"repair-{suffix}",
        tenant_policy_version=f"policy-{suffix}",
        candidate_planner_version="candidate-planner-v1",
        objective_version=f"objective-{suffix}",
        artifacts=_input_artifacts(
            tenant_id=tenant_id,
            as_of=as_of,
            policy_suffix=suffix,
        ),
        solver=_solver(),
    )


def _outcome_lineage(
    *,
    tenant_id: str,
    as_of: datetime,
    horizon_end: datetime,
) -> ReplayOutcomeLineage:
    artifacts = tuple(
        ReplayOutcomeArtifact(
            artifact_id=f"outcome-{kind}",
            tenant_id=tenant_id,
            kind=kind,
            status="available" if kind == "demand" else "not_applicable",
            content_sha256=(
                _sha256(f"outcome-{kind}")
                if kind == "demand"
                else None
            ),
            window_start=as_of if kind == "demand" else None,
            window_end=horizon_end if kind == "demand" else None,
            available_at=horizon_end + timedelta(days=1),
            reason=(
                None
                if kind == "demand"
                else "No applicable realized events."
            ),
        )
        for kind in ("demand", "receipt", "repair_outcome")
    )
    return ReplayOutcomeLineage(
        tenant_id=tenant_id,
        as_of=as_of,
        horizon_end=horizon_end,
        manifest_sha256=replay_outcome_manifest_sha256(
            tenant_id=tenant_id,
            as_of=as_of,
            horizon_end=horizon_end,
            artifacts=artifacts,
        ),
        artifacts=artifacts,
    )


def _metrics(
    *,
    outcome_hash: str,
    filled_units: int,
) -> ReplayMetrics:
    demand = Decimal("10")
    filled = Decimal(filled_units)
    return ReplayMetrics(
        currency="USD",
        outcome_manifest_sha256=outcome_hash,
        demanded_units=demand,
        filled_units=filled,
        backordered_units=demand - filled,
        shortage_unit_days=(demand - filled) * Decimal("3"),
        ending_inventory_units=Decimal("2"),
        inventory_investment=Decimal("200"),
        holding_cost=Decimal("4"),
        ordering_cost=Decimal("1"),
        acquisition_cash=Decimal("5"),
        aog_risk_proxy_events=demand - filled,
        decision_count=1,
        fill_rate=filled / demand,
    )


def matched_replay_request(
    tenant_id: str,
    *,
    universe_id: str = "matched-historical-decision",
    exclusion_count: int = 0,
    include_planning_links: bool = False,
) -> ReplayEvaluationRequest:
    """One fully matched observation plus optional excluded universe rows."""

    if exclusion_count < 0:
        raise ValueError("exclusion_count must be non-negative")

    as_of = datetime(2026, 1, 1, tzinfo=UTC)
    horizon_end = as_of + timedelta(days=30)
    observation_id = f"{universe_id}-observation"
    decision_key = "PN-MATCHED@MIA"
    outcome_lineage = _outcome_lineage(
        tenant_id=tenant_id,
        as_of=as_of,
        horizon_end=horizon_end,
    )
    observation = MatchedReplayObservation(
        observation_id=observation_id,
        tenant_id=tenant_id,
        decision_key=decision_key,
        as_of=as_of,
        horizon_end=horizon_end,
        cohort=ReplayCohort(
            criticality_tier=1,
            demand_regime="intermittent",
            repairability="rotable",
            location_code="MIA",
            repair_data_confidence="observed",
            evidence_artifact_id="part-attributes-shared",
        ),
        current=_metrics(
            outcome_hash=outcome_lineage.manifest_sha256,
            filled_units=8,
        ),
        challenger=_metrics(
            outcome_hash=outcome_lineage.manifest_sha256,
            filled_units=9,
        ),
        current_lineage=_lineage(
            tenant_id=tenant_id,
            as_of=as_of,
            suffix="a",
            planning_run_id=(
                "11111111-1111-1111-1111-111111111111"
                if include_planning_links
                else None
            ),
            planning_selection_decision_key=(
                decision_key if include_planning_links else None
            ),
        ),
        challenger_lineage=_lineage(
            tenant_id=tenant_id,
            as_of=as_of,
            suffix="b",
            planning_run_id=(
                "22222222-2222-2222-2222-222222222222"
                if include_planning_links
                else None
            ),
            planning_selection_decision_key=(
                decision_key if include_planning_links else None
            ),
        ),
        outcome_lineage=outcome_lineage,
    )
    decision = ReplayUniverseDecision(
        observation_id=observation_id,
        tenant_id=tenant_id,
        decision_key=decision_key,
        as_of=as_of,
        horizon_end=horizon_end,
    )
    excluded_decisions = tuple(
        ReplayUniverseDecision(
            observation_id=f"{universe_id}-excluded-{index:05d}",
            tenant_id=tenant_id,
            decision_key=f"PN-EXCLUDED-{index:05d}@MIA",
            as_of=as_of,
            horizon_end=horizon_end,
        )
        for index in range(exclusion_count)
    )
    decisions = (decision, *excluded_decisions)
    exclusions = tuple(
        ReplayExclusion(
            observation_id=excluded.observation_id,
            tenant_id=excluded.tenant_id,
            decision_key=excluded.decision_key,
            as_of=excluded.as_of,
            horizon_end=excluded.horizon_end,
            reason_code="incomplete_horizon",
            detail="The realized evaluation horizon is incomplete.",
        )
        for excluded in excluded_decisions
    )
    return ReplayEvaluationRequest(
        tenant_id=tenant_id,
        currency="USD",
        universe_id=universe_id,
        universe_sha256=replay_universe_sha256(
            universe_id=universe_id,
            tenant_id=tenant_id,
            decisions=decisions,
        ),
        expected_decision_count=len(decisions),
        universe_decisions=decisions,
        current_policy_label="current",
        challenger_policy_label="repair-aware",
        comparison_rule="matched_budget",
        observations=(observation,),
        exclusions=exclusions,
    )


def matched_replay_source_package(
    tenant_id: str,
    *,
    universe_id: str = "source-backed-matched-decision",
) -> TrustedReplaySourcePackage:
    """Controlled source-domain package for builder/import integration tests."""

    request = matched_replay_request(
        tenant_id,
        universe_id=universe_id,
        include_planning_links=True,
    )
    observation = request.observations[0]

    def policy_source(
        lineage: ReplayDecisionLineage,
        metrics: ReplayMetrics,
    ) -> ReplayPolicySourceRecord:
        assert lineage.planning_run_id is not None
        assert lineage.planning_selection_decision_key is not None
        assert lineage.planning_selection_available_at is not None
        return ReplayPolicySourceRecord(
            metrics=metrics,
            source_snapshot_hash=lineage.source_snapshot_hash,
            planning_fingerprint=lineage.planning_fingerprint,
            planning_request_sha256=lineage.planning_request_sha256,
            planning_run_id=lineage.planning_run_id,
            planning_selection_decision_key=(
                lineage.planning_selection_decision_key
            ),
            planning_selection_available_at=(
                lineage.planning_selection_available_at
            ),
            forecast_version=lineage.forecast_version,
            repair_model_version=lineage.repair_model_version,
            tenant_policy_version=lineage.tenant_policy_version,
            candidate_planner_version=lineage.candidate_planner_version,
            objective_version=lineage.objective_version,
            artifacts=tuple(
                artifact
                for artifact in lineage.artifacts
                if artifact.kind not in _FACTUAL_KINDS
            ),
            solver=lineage.solver,
        )

    return TrustedReplaySourcePackage(
        tenant_id=tenant_id,
        currency=request.currency,
        universe_id=universe_id,
        current_policy_label=request.current_policy_label,
        challenger_policy_label=request.challenger_policy_label,
        comparison_rule=request.comparison_rule,
        match_tolerance=request.match_tolerance,
        records=(
            ReplayMatchedSourceRecord(
                observation_id=observation.observation_id,
                tenant_id=tenant_id,
                decision_key=observation.decision_key,
                as_of=observation.as_of,
                horizon_end=observation.horizon_end,
                cohort=observation.cohort,
                factual_artifacts=tuple(
                    artifact
                    for artifact in observation.current_lineage.artifacts
                    if artifact.kind in _FACTUAL_KINDS
                ),
                current=policy_source(
                    observation.current_lineage,
                    observation.current,
                ),
                challenger=policy_source(
                    observation.challenger_lineage,
                    observation.challenger,
                ),
                outcome_lineage=observation.outcome_lineage,
            ),
        ),
    )
