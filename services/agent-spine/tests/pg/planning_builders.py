from __future__ import annotations

from decimal import Decimal

from trax_io_reco.candidate.identity import frontier_fingerprint
from trax_io_reco.candidate.planner import CandidatePlanner
from trax_io_reco.candidate.reconcile import build_no_change_candidate
from trax_io_reco.contracts.candidate import (
    CandidateEvidence,
    CandidateFingerprintInputs,
    CandidateTargetLevels,
    LifecycleEconomics,
    ModelIdentity,
)
from trax_io_reco.contracts.planning import PortfolioKeyMenu, PortfolioSolveRequest


def planning_request(
    tenant_id: str,
    *,
    decision_keys: tuple[str, ...] = ("PN-A@MIA",),
    source_snapshot_hash: str = "snapshot-1",
    budget: str = "0",
) -> PortfolioSolveRequest:
    model = ModelIdentity(
        forecast_model="compound-poisson",
        forecast_version="forecast-v1",
        policy_model="deterministic-s-S",
        policy_version="policy-v1",
        repair_model="repair-return",
        repair_version="repair-return.v1",
    )
    levels = CandidateTargetLevels(rop=0, eoq=1, safety_stock=0, max_stock=20)
    economics = LifecycleEconomics(
        currency="USD",
        inventory_unit_value=Decimal("1"),
        annual_holding_rate=Decimal("0"),
        ordering_cost_per_purchase=Decimal("0"),
        shortage_cost_per_unit=Decimal("0"),
        horizon_days=30,
    )
    evidence = (
        CandidateEvidence(
            kind="planning_trace",
            source="snapshot",
            detail="Planning persistence integration fixture",
        ),
    )
    menus = []
    for decision_key in decision_keys:
        pn, location = decision_key.split("@", maxsplit=1)
        inputs = CandidateFingerprintInputs(
            tenant_id=tenant_id,
            decision_key=decision_key,
            member_keys=(decision_key,),
            source_snapshot_hash=source_snapshot_hash,
            context_digest=f"context-{decision_key}",
            tenant_policy_version="policy-config-v1",
            as_of="2026-07-28",
            horizon_days=30,
            currency="USD",
            model_identity=model,
            constraint_set_version="constraints-v1",
            arbitration_version="arbitration-v1",
            economics_version="economics-v1",
            objective_definition_version="objective-v1",
        )
        fingerprint = frontier_fingerprint(inputs)
        baseline = build_no_change_candidate(
            frontier_id=fingerprint,
            tenant_id=tenant_id,
            pn=pn,
            location=location,
            decision_key=decision_key,
            member_keys=(decision_key,),
            model_identity=model,
            current_levels=levels,
            available_before=0,
            expected_receipts_before=0,
            projected_demand=10,
            economics=economics,
            expected_aog_risk=Decimal("0.9"),
            confidence=Decimal("0.8"),
            constraints=(),
            evidence=evidence,
        )
        menus.append(
            PortfolioKeyMenu(
                frontier=CandidatePlanner().build_frontier(
                    inputs=inputs,
                    candidates=(baseline,),
                ),
                criticality_tier=3,
            )
        )
    return PortfolioSolveRequest(
        tenant_id=tenant_id,
        source_snapshot_hash=source_snapshot_hash,
        horizon_days=30,
        currency="USD",
        budget=budget,
        menus=tuple(menus),
        tenant_policy_version="policy-config-v1",
        forecast_version="forecast-v1",
        repair_model_version="repair-return.v1",
        candidate_planner_version="candidate-planner-v1",
        time_limit_seconds=5,
    )


def planning_request_input_coverage(
    request: PortfolioSolveRequest,
    *,
    total_key_count: int | None = None,
) -> dict[str, int]:
    """Authoritative snapshot-shape metadata for PG persistence tests."""

    eligible_key_count = len(request.menus)
    total = eligible_key_count if total_key_count is None else total_key_count
    return {
        "total_key_count": total,
        "returned_key_count": eligible_key_count,
        "eligible_key_count": eligible_key_count,
        "missing_frontier_key_count": total - eligible_key_count,
        "candidate_count": sum(
            len(menu.frontier.candidates) for menu in request.menus
        ),
        "feasible_candidate_count": sum(
            candidate.feasible
            for menu in request.menus
            for candidate in menu.frontier.candidates
        ),
        "criticality_known_key_count": eligible_key_count,
        "criticality_unknown_key_count": total - eligible_key_count,
    }
