from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

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
    ShadowScorecard,
    replay_outcome_manifest_sha256,
    replay_universe_sha256,
)
from trax_io_reco.replay import build_shadow_scorecard
from trax_io_reco.replay.package import (
    ReplayExcludedSourceRecord,
    ReplayMatchedSourceRecord,
    ReplayPolicySourceRecord,
    TrustedReplaySourcePackage,
    build_trusted_replay_request,
)
from trax_io_reco.replay.package import (
    main as build_replay_package_cli,
)

AS_OF = datetime(2026, 1, 1, tzinfo=UTC)
HORIZON_END = AS_OF + timedelta(days=30)
SNAPSHOT = "snapshot-shared"
FACTUAL_KINDS = {
    "demand",
    "receipt",
    "repair_outcome",
    "price",
    "part_attributes",
}


def _hash(character: str) -> str:
    return hashlib.sha256(character.encode()).hexdigest()


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
    policy_suffix: str,
    snapshot: str = SNAPSHOT,
    demand_available_at: datetime | None = None,
) -> tuple[ReplayInputArtifact, ...]:
    rows = []
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
        shared = kind in FACTUAL_KINDS
        suffix = "f" if shared else policy_suffix
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
        rows.append(
            ReplayInputArtifact(
                artifact_id=(
                    "part-attributes-shared"
                    if kind == "part_attributes"
                    else f"{kind}-{suffix}"
                ),
                tenant_id="tenant-a",
                kind=kind,
                status=status,
                source_snapshot_hash=snapshot,
                content_sha256=(
                    _hash(chr(97 + index))
                    if status == "available"
                    else None
                ),
                occurred_at=(
                    AS_OF - timedelta(days=index + 2)
                    if status == "available"
                    else None
                ),
                available_at=(
                    demand_available_at
                    if kind == "demand" and demand_available_at is not None
                    else AS_OF - timedelta(days=1)
                ),
                versions=tuple(sorted(versions)),
                reason=(
                    "No applicable historical events."
                    if status != "available"
                    else None
                ),
            )
        )
    return tuple(rows)


def _lineage(
    *,
    suffix: str,
    snapshot: str = SNAPSHOT,
    demand_available_at: datetime | None = None,
) -> ReplayDecisionLineage:
    return ReplayDecisionLineage(
        tenant_id="tenant-a",
        as_of=AS_OF,
        source_snapshot_hash=snapshot,
        planning_fingerprint=f"planning_{suffix * 64}",
        planning_request_sha256=_hash(suffix),
        forecast_version=f"forecast-{suffix}",
        repair_model_version=f"repair-{suffix}",
        tenant_policy_version=f"policy-{suffix}",
        candidate_planner_version="candidate-planner-v1",
        objective_version=f"objective-{suffix}",
        artifacts=_input_artifacts(
            policy_suffix=suffix,
            snapshot=snapshot,
            demand_available_at=demand_available_at,
        ),
        solver=_solver(),
    )


def _outcome_lineage(
    *,
    suffix: str,
    window_end: datetime = HORIZON_END,
) -> ReplayOutcomeLineage:
    artifacts = []
    for index, kind in enumerate(("demand", "receipt", "repair_outcome")):
        status = "available" if kind == "demand" else "not_applicable"
        artifacts.append(
            ReplayOutcomeArtifact(
                artifact_id=f"outcome-{kind}-{suffix}",
                tenant_id="tenant-a",
                kind=kind,
                status=status,
                content_sha256=(
                    _hash(chr(107 + index))
                    if status == "available"
                    else None
                ),
                window_start=(
                    AS_OF
                    if status == "available"
                    else None
                ),
                window_end=window_end if status == "available" else None,
                available_at=HORIZON_END + timedelta(days=1),
                reason=(
                    "No applicable realized events."
                    if status != "available"
                    else None
                ),
            )
        )
    artifact_manifest = tuple(artifacts)
    return ReplayOutcomeLineage(
        tenant_id="tenant-a",
        as_of=AS_OF,
        horizon_end=HORIZON_END,
        manifest_sha256=replay_outcome_manifest_sha256(
            tenant_id="tenant-a",
            as_of=AS_OF,
            horizon_end=HORIZON_END,
            artifacts=artifact_manifest,
        ),
        artifacts=artifact_manifest,
    )


def _metrics(
    *,
    outcome_hash: str,
    filled: int,
    acquisition_cash: int = 5,
    inventory: int = 2,
) -> ReplayMetrics:
    demand = Decimal("10")
    filled_units = Decimal(filled)
    return ReplayMetrics(
        currency="USD",
        outcome_manifest_sha256=outcome_hash,
        demanded_units=demand,
        filled_units=filled_units,
        backordered_units=demand - filled_units,
        shortage_unit_days=(demand - filled_units) * Decimal("3"),
        ending_inventory_units=inventory,
        inventory_investment=Decimal(inventory) * Decimal("100"),
        holding_cost=Decimal(inventory) * Decimal("2"),
        ordering_cost=Decimal("1"),
        acquisition_cash=acquisition_cash,
        aog_risk_proxy_events=demand - filled_units,
        decision_count=1,
        fill_rate=filled_units / demand,
    )


def _observation(
    *,
    observation_id: str,
    location: str,
    current_filled: int = 8,
    challenger_filled: int = 9,
    current_cash: int = 5,
    challenger_cash: int = 5,
    challenger_snapshot: str = SNAPSHOT,
) -> MatchedReplayObservation:
    suffix = "x" if observation_id.endswith("1") else "y"
    outcomes = _outcome_lineage(suffix=suffix)
    return MatchedReplayObservation(
        observation_id=observation_id,
        tenant_id="tenant-a",
        decision_key=f"PN-{observation_id}@{location}",
        as_of=AS_OF,
        horizon_end=HORIZON_END,
        cohort=ReplayCohort(
            criticality_tier=1 if suffix == "x" else 3,
            demand_regime="intermittent" if suffix == "x" else "smooth",
            repairability="rotable" if suffix == "x" else "expendable",
            location_code=location,
            repair_data_confidence="observed" if suffix == "x" else "unavailable",
            evidence_artifact_id="part-attributes-shared",
        ),
        current=_metrics(
            outcome_hash=outcomes.manifest_sha256,
            filled=current_filled,
            acquisition_cash=current_cash,
        ),
        challenger=_metrics(
            outcome_hash=outcomes.manifest_sha256,
            filled=challenger_filled,
            acquisition_cash=challenger_cash,
            inventory=3,
        ),
        current_lineage=_lineage(suffix="a"),
        challenger_lineage=_lineage(
            suffix="b",
            snapshot=challenger_snapshot,
        ),
        outcome_lineage=outcomes,
    )


def _request(
    *observations: MatchedReplayObservation,
    exclusions: tuple[ReplayExclusion, ...] = (),
    expected_count: int | None = None,
    comparison_rule: str = "matched_budget",
) -> ReplayEvaluationRequest:
    rows = (*observations, *exclusions)
    universe = tuple(
        ReplayUniverseDecision(
            observation_id=row.observation_id,
            tenant_id=row.tenant_id,
            decision_key=row.decision_key,
            as_of=row.as_of,
            horizon_end=row.horizon_end,
        )
        for row in rows
    )
    universe_id = "historical-decisions-2026q1"
    return ReplayEvaluationRequest(
        tenant_id="tenant-a",
        currency="USD",
        universe_id=universe_id,
        universe_sha256=replay_universe_sha256(
            universe_id=universe_id,
            tenant_id="tenant-a",
            decisions=universe,
        ),
        expected_decision_count=(
            expected_count
            if expected_count is not None
            else len(observations) + len(exclusions)
        ),
        universe_decisions=universe,
        current_policy_label="current",
        challenger_policy_label="repair-aware",
        comparison_rule=comparison_rule,
        observations=observations,
        exclusions=exclusions,
    )


def _policy_source(
    observation: MatchedReplayObservation,
    lineage: ReplayDecisionLineage,
    metrics: ReplayMetrics,
    *,
    run_id: str,
) -> ReplayPolicySourceRecord:
    return ReplayPolicySourceRecord(
        metrics=metrics,
        source_snapshot_hash=lineage.source_snapshot_hash,
        planning_fingerprint=lineage.planning_fingerprint,
        planning_request_sha256=lineage.planning_request_sha256,
        planning_run_id=run_id,
        planning_selection_decision_key=observation.decision_key,
        planning_selection_available_at=AS_OF - timedelta(minutes=1),
        forecast_version=lineage.forecast_version,
        repair_model_version=lineage.repair_model_version,
        tenant_policy_version=lineage.tenant_policy_version,
        candidate_planner_version=lineage.candidate_planner_version,
        objective_version=lineage.objective_version,
        artifacts=tuple(
            artifact
            for artifact in lineage.artifacts
            if artifact.kind not in FACTUAL_KINDS
        ),
        solver=lineage.solver,
    )


def _source_package(
    observation: MatchedReplayObservation | None = None,
) -> TrustedReplaySourcePackage:
    observation = observation or _observation(
        observation_id="obs-1",
        location="MIA",
    )
    return TrustedReplaySourcePackage(
        tenant_id="tenant-a",
        currency="USD",
        universe_id="source-backed-2026q1",
        current_policy_label="current",
        challenger_policy_label="repair-aware",
        comparison_rule="matched_budget",
        records=(
            ReplayMatchedSourceRecord(
                observation_id=observation.observation_id,
                tenant_id=observation.tenant_id,
                decision_key=observation.decision_key,
                as_of=observation.as_of,
                horizon_end=observation.horizon_end,
                cohort=observation.cohort,
                factual_artifacts=tuple(
                    artifact
                    for artifact in observation.current_lineage.artifacts
                    if artifact.kind in FACTUAL_KINDS
                ),
                current=_policy_source(
                    observation,
                    observation.current_lineage,
                    observation.current,
                    run_id="11111111-1111-1111-1111-111111111111",
                ),
                challenger=_policy_source(
                    observation,
                    observation.challenger_lineage,
                    observation.challenger,
                    run_id="22222222-2222-2222-2222-222222222222",
                ),
                outcome_lineage=observation.outcome_lineage,
            ),
        ),
    )


def test_scorecard_reconciles_cohorts_coverage_metrics_and_lineage() -> None:
    exclusion = ReplayExclusion(
        observation_id="obs-excluded",
        tenant_id="tenant-a",
        decision_key="PN-X@MIA",
        as_of=AS_OF,
        horizon_end=HORIZON_END,
        reason_code="missing_price",
        detail="No historically effective unit price was available.",
    )
    request = _request(
        _observation(observation_id="obs-2", location="DFW"),
        _observation(observation_id="obs-1", location="MIA"),
        exclusions=(exclusion,),
    )

    scorecard = build_shadow_scorecard(request)

    assert scorecard.advisory_only
    assert scorecard.observation_count == 2
    assert scorecard.total_observation_count == 3
    assert scorecard.excluded_observation_count == 1
    assert scorecard.coverage_rate == Decimal(2) / Decimal(3)
    assert scorecard.current.demanded_units == 20
    assert scorecard.current.filled_units == 16
    assert scorecard.challenger.filled_units == 18
    assert scorecard.delta.fill_rate == Decimal("0.1")
    assert scorecard.delta.backordered_units == -2
    assert len(scorecard.cohorts) == 2
    assert sum(
        cohort.current.demanded_units for cohort in scorecard.cohorts
    ) == scorecard.current.demanded_units
    assert scorecard.source_snapshot_hashes == (SNAPSHOT,)
    assert len(scorecard.planning_fingerprints) == 2
    assert tuple(
        lineage.observation_id for lineage in scorecard.observation_lineage
    ) == ("obs-1", "obs-2")
    assert scorecard.exclusions_by_reason[0].reason_code == "missing_price"
    assert {definition.metric for definition in scorecard.metric_definitions} == {
        "demanded_units",
        "filled_units",
        "fill_rate",
        "backordered_units",
        "shortage_unit_days",
        "ending_inventory_units",
        "inventory_investment",
        "holding_cost",
        "ordering_cost",
        "acquisition_cash",
        "aog_risk_proxy_events",
        "decision_count",
    }


def test_decision_lineage_rejects_any_fact_available_after_cutoff() -> None:
    with pytest.raises(
        ValidationError,
        match="no-lookahead violation.*demand-f",
    ):
        _lineage(
            suffix="a",
            demand_available_at=AS_OF + timedelta(seconds=1),
        )


def test_outcome_lineage_rejects_facts_after_evaluation_horizon() -> None:
    with pytest.raises(
        ValidationError,
        match="must cover the complete replay window",
    ):
        _outcome_lineage(
            suffix="x",
            window_end=HORIZON_END + timedelta(seconds=1),
        )


def test_matched_policies_must_share_snapshot_and_factual_evidence() -> None:
    with pytest.raises(
        ValidationError,
        match="same historical factual evidence",
    ):
        _observation(
            observation_id="obs-1",
            location="MIA",
            challenger_snapshot="snapshot-other",
        )


def test_declared_universe_prevents_silent_coverage_inflation() -> None:
    with pytest.raises(
        ValidationError,
        match="declared historical universe count does not reconcile",
    ):
        _request(
            _observation(observation_id="obs-1", location="MIA"),
            expected_count=2,
        )


def test_natural_decision_key_cannot_be_double_counted_under_new_id() -> None:
    first = _observation(observation_id="obs-1", location="MIA")
    payload = first.model_dump()
    payload["observation_id"] = "different-id"
    duplicate = MatchedReplayObservation.model_validate(payload)

    with pytest.raises(
        ValidationError,
        match="natural decision keys must be unique",
    ):
        _request(first, duplicate)


def test_all_excluded_universe_returns_safe_zero_coverage_scorecard() -> None:
    exclusion = ReplayExclusion(
        observation_id="obs-excluded",
        tenant_id="tenant-a",
        decision_key="PN-X@MIA",
        as_of=AS_OF,
        horizon_end=HORIZON_END,
        reason_code="incomplete_horizon",
        detail="The realized evaluation horizon has not completed.",
    )

    scorecard = build_shadow_scorecard(_request(exclusions=(exclusion,)))

    assert scorecard.observation_count == 0
    assert scorecard.coverage_rate == 0
    assert scorecard.current.decision_count == 0
    assert scorecard.current.fill_rate == 0
    assert scorecard.cohorts == ()
    assert scorecard.observation_lineage == ()


def test_matched_budget_and_service_rules_are_enforced() -> None:
    with pytest.raises(
        ValidationError,
        match="matched_budget comparison exceeds configured tolerance",
    ):
        _request(
            _observation(
                observation_id="obs-1",
                location="MIA",
                current_cash=5,
                challenger_cash=6,
            )
        )

    service_request = _request(
        _observation(
            observation_id="obs-1",
            location="MIA",
            current_filled=9,
            challenger_filled=9,
            current_cash=5,
            challenger_cash=9,
        ),
        comparison_rule="matched_service",
    )
    scorecard = build_shadow_scorecard(service_request)
    assert scorecard.delta.fill_rate == 0
    assert scorecard.delta.acquisition_cash == 4


def test_scorecard_contract_rejects_tampered_delta_and_currency() -> None:
    scorecard = build_shadow_scorecard(
        _request(_observation(observation_id="obs-1", location="MIA"))
    )
    delta_payload = scorecard.model_dump()
    delta_payload["delta"]["fill_rate"] = Decimal("999")
    with pytest.raises(ValidationError, match="delta does not reconcile"):
        ShadowScorecard.model_validate(delta_payload)

    currency_payload = scorecard.model_dump()
    currency_payload["currency"] = "EUR"
    with pytest.raises(ValidationError, match="currencies do not reconcile"):
        ShadowScorecard.model_validate(currency_payload)


def test_outcome_manifest_and_full_horizon_are_content_bound() -> None:
    lineage = _outcome_lineage(suffix="x")
    forged = lineage.model_dump()
    forged["artifacts"][0]["content_sha256"] = _hash("changed-content")
    with pytest.raises(
        ValidationError,
        match="outcome manifest hash does not reconcile",
    ):
        ReplayOutcomeLineage.model_validate(forged)

    partial_artifacts = tuple(
        artifact.model_copy(
            update={"window_start": AS_OF + timedelta(days=1)}
        )
        if artifact.status == "available"
        else artifact
        for artifact in lineage.artifacts
    )
    with pytest.raises(
        ValidationError,
        match="must cover the complete replay window",
    ):
        ReplayOutcomeLineage(
            tenant_id=lineage.tenant_id,
            as_of=lineage.as_of,
            horizon_end=lineage.horizon_end,
            manifest_sha256=replay_outcome_manifest_sha256(
                tenant_id=lineage.tenant_id,
                as_of=lineage.as_of,
                horizon_end=lineage.horizon_end,
                artifacts=partial_artifacts,
            ),
            artifacts=partial_artifacts,
        )


def test_universe_and_aggregate_manifest_hashes_cannot_be_forged() -> None:
    request = _request(_observation(observation_id="obs-1", location="MIA"))
    forged_request = request.model_dump()
    forged_request["universe_sha256"] = _hash("forged-universe")
    with pytest.raises(
        ValidationError,
        match="replay universe hash does not reconcile",
    ):
        ReplayEvaluationRequest.model_validate(forged_request)

    scorecard = build_shadow_scorecard(request)
    top_level = scorecard.model_dump()
    top_level["current"]["outcome_manifest_sha256"] = _hash("forged-top")
    with pytest.raises(
        ValidationError,
        match="aggregate metrics do not reconcile to outcome lineage",
    ):
        ShadowScorecard.model_validate(top_level)

    cohort = scorecard.model_dump()
    cohort["cohorts"][0]["challenger"][
        "outcome_manifest_sha256"
    ] = _hash("forged-cohort")
    with pytest.raises(
        ValidationError,
        match="cohort metrics do not reconcile to outcome lineage",
    ):
        ShadowScorecard.model_validate(cohort)

    membership = scorecard.model_dump()
    membership["observation_lineage"][0]["cohort_id"] = "forged-cohort"
    with pytest.raises(
        ValidationError,
        match="cohort membership does not reconcile to lineage",
    ):
        ShadowScorecard.model_validate(membership)


def test_shadow_scorecard_contract_has_no_mutation_surface() -> None:
    scorecard = build_shadow_scorecard(
        _request(_observation(observation_id="obs-1", location="MIA"))
    )
    payload = scorecard.model_dump(mode="json")

    assert payload["advisory_only"] is True
    assert not {
        "writeback",
        "purchase_order",
        "repair_routing",
        "apply",
        "approve",
    } & set(payload)


def test_source_package_builds_no_lookahead_request_with_planning_links() -> None:
    source = _source_package()
    observation = source.records[0]

    request = build_trusted_replay_request(source)

    assert request.expected_decision_count == 1
    assert request.exclusions == ()
    built = request.observations[0]
    assert built.current_lineage.planning_run_id == (
        "11111111-1111-1111-1111-111111111111"
    )
    assert built.current_lineage.planning_selection_decision_key == (
        observation.decision_key
    )
    scorecard = build_shadow_scorecard(request)
    assert scorecard.observation_lineage[0].current_planning_run_id == (
        "11111111-1111-1111-1111-111111111111"
    )


@pytest.mark.parametrize(
    "late_source",
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
        "planning_selection",
    ),
)
def test_source_package_explicitly_excludes_every_late_input_family(
    late_source: str,
) -> None:
    source = _source_package()
    record = source.records[0]
    late_at = AS_OF + timedelta(microseconds=1)
    if late_source in FACTUAL_KINDS:
        record = record.model_copy(
            update={
                "factual_artifacts": tuple(
                    artifact.model_copy(update={"available_at": late_at})
                    if artifact.kind == late_source
                    else artifact
                    for artifact in record.factual_artifacts
                )
            }
        )
    elif late_source == "planning_selection":
        record = record.model_copy(
            update={
                "current": record.current.model_copy(
                    update={"planning_selection_available_at": late_at}
                )
            }
        )
    else:
        record = record.model_copy(
            update={
                "current": record.current.model_copy(
                    update={
                        "artifacts": tuple(
                            artifact.model_copy(
                                update={"available_at": late_at}
                            )
                            if artifact.kind == late_source
                            else artifact
                            for artifact in record.current.artifacts
                        )
                    }
                )
            }
        )

    request = build_trusted_replay_request(
        source.model_copy(update={"records": (record,)})
    )

    assert request.observations == ()
    assert request.expected_decision_count == 1
    assert len(request.exclusions) == 1
    exclusion = request.exclusions[0]
    assert exclusion.reason_code == "invalid_lineage"
    assert exclusion.detail.startswith("no_lookahead_cutoff_violation:")
    assert late_source in exclusion.detail


def test_source_package_preserves_explicit_stable_exclusions() -> None:
    source = _source_package()
    excluded = ReplayExcludedSourceRecord(
        observation_id="obs-missing-snapshot",
        tenant_id="tenant-a",
        decision_key="PN-MISSING@MIA",
        as_of=AS_OF,
        horizon_end=HORIZON_END,
        reason_code="missing_snapshot",
        detail="No complete source snapshot was available at the cutoff.",
    )

    request = build_trusted_replay_request(
        source.model_copy(update={"records": (excluded,)})
    )

    assert request.observations == ()
    assert request.expected_decision_count == 1
    assert request.exclusions == (
        ReplayExclusion(
            observation_id=excluded.observation_id,
            tenant_id=excluded.tenant_id,
            decision_key=excluded.decision_key,
            as_of=excluded.as_of,
            horizon_end=excluded.horizon_end,
            reason_code=excluded.reason_code,
            detail=excluded.detail,
        ),
    )


def test_source_package_requires_explicit_factual_and_policy_domains() -> None:
    source = _source_package()
    factual_payload = source.model_dump()
    factual_artifacts = list(
        factual_payload["records"][0]["factual_artifacts"]
    )
    factual_artifacts[0] = (
        factual_payload["records"][0]["current"]["artifacts"][0]
    )
    factual_payload["records"][0]["factual_artifacts"] = factual_artifacts
    with pytest.raises(
        ValidationError,
        match="factual source artifacts",
    ):
        TrustedReplaySourcePackage.model_validate(factual_payload)

    policy_payload = source.model_dump()
    policy_payload["records"][0]["current"]["artifacts"] = (
        policy_payload["records"][0]["current"]["artifacts"][:-1]
    )
    with pytest.raises(
        ValidationError,
        match="policy source artifacts",
    ):
        TrustedReplaySourcePackage.model_validate(policy_payload)


def test_source_package_cli_writes_import_ready_request(tmp_path) -> None:
    source_path = tmp_path / "historical-source.json"
    output_path = tmp_path / "replay-evaluation-request.json"
    source_path.write_text(_source_package().model_dump_json(), encoding="utf-8")

    result = CliRunner().invoke(
        build_replay_package_cli,
        (
            "--input",
            str(source_path),
            "--output",
            str(output_path),
        ),
    )

    assert result.exit_code == 0, result.output
    request = ReplayEvaluationRequest.model_validate_json(
        output_path.read_text(encoding="utf-8")
    )
    assert request.expected_decision_count == 1
    assert request.observations[0].current_lineage.planning_run_id == (
        "11111111-1111-1111-1111-111111111111"
    )
    assert "source_contract_version" not in request.model_dump(mode="json")


def test_scorecard_lineage_rejects_tampered_planning_selection_link() -> None:
    scorecard = build_shadow_scorecard(
        build_trusted_replay_request(_source_package())
    )
    payload = scorecard.model_dump()
    payload["observation_lineage"][0][
        "current_planning_selection_decision_key"
    ] = "PN-TAMPERED@MIA"

    with pytest.raises(
        ValidationError,
        match="lineage planning selection link",
    ):
        ShadowScorecard.model_validate(payload)
