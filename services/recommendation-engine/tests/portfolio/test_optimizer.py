from __future__ import annotations

import itertools
import json
import random
from decimal import Decimal
from types import SimpleNamespace

import pytest
from scipy.sparse import issparse

import trax_io_reco.portfolio.optimizer as optimizer_module
from trax_io_reco.candidate.identity import frontier_fingerprint
from trax_io_reco.candidate.planner import CandidatePlanner
from trax_io_reco.candidate.reconcile import (
    build_no_change_candidate,
    reconcile_candidate,
)
from trax_io_reco.contracts.candidate import (
    CandidateActionLine,
    CandidateEvidence,
    CandidateFingerprintInputs,
    CandidateTargetLevels,
    LifecycleEconomics,
    ModelIdentity,
    PolicyCandidate,
)
from trax_io_reco.contracts.planning import (
    MandatoryFloor,
    PortfolioKeyMenu,
    PortfolioSolveRequest,
    TenantObjectiveWeights,
)
from trax_io_reco.contracts.planning_run import PlanningWarning
from trax_io_reco.portfolio import (
    PortfolioOptimizer,
    build_planning_run_outcome,
    planning_assumption_diff,
)
from trax_io_reco.portfolio.identity import planning_fingerprint

TENANT = "tenant-a"
MODEL = ModelIdentity(
    forecast_model="compound-poisson",
    forecast_version="forecast-v1",
    policy_model="deterministic-s-S",
    policy_version="policy-v1",
    repair_model="repair-return",
    repair_version="repair-return.v1",
)
LEVELS = CandidateTargetLevels(rop=0, eoq=1, safety_stock=0, max_stock=20)
ECONOMICS = LifecycleEconomics(
    currency="USD",
    inventory_unit_value=Decimal("1"),
    annual_holding_rate=Decimal("0"),
    ordering_cost_per_purchase=Decimal("0"),
    shortage_cost_per_unit=Decimal("0"),
    horizon_days=30,
)
EVIDENCE = (
    CandidateEvidence(
        kind="planning_trace",
        source="snapshot",
        detail="Deterministic optimizer test evidence",
    ),
)
NO_COST_WEIGHTS = TenantObjectiveWeights(
    shortage_reduction_weight=Decimal("1"),
    aog_risk_reduction_weight=Decimal("1"),
    holding_cost_penalty_weight=Decimal("0"),
    ordering_cost_penalty_weight=Decimal("0"),
)


def _frontier(
    decision_key: str,
    *,
    choices: tuple[tuple[str, int, int, str], ...],
    demand: int = 10,
):
    pn, location = decision_key.split("@", maxsplit=1)
    inputs = CandidateFingerprintInputs(
        tenant_id=TENANT,
        decision_key=decision_key,
        member_keys=(decision_key,),
        source_snapshot_hash="snapshot-1",
        context_digest=f"context-{decision_key}",
        tenant_policy_version="policy-config-v1",
        as_of="2026-07-28",
        horizon_days=30,
        currency="USD",
        model_identity=MODEL,
        constraint_set_version="constraints-v1",
        arbitration_version="arbitration-v1",
        economics_version="economics-v1",
        objective_definition_version="objective-v1",
    )
    fingerprint = frontier_fingerprint(inputs)
    baseline = build_no_change_candidate(
        frontier_id=fingerprint,
        tenant_id=TENANT,
        pn=pn,
        location=location,
        decision_key=decision_key,
        member_keys=(decision_key,),
        model_identity=MODEL,
        current_levels=LEVELS,
        available_before=0,
        expected_receipts_before=0,
        projected_demand=demand,
        economics=ECONOMICS,
        expected_aog_risk=Decimal("0.9"),
        confidence=Decimal("0.8"),
        constraints=(),
        evidence=EVIDENCE,
    )
    candidates = [baseline]
    for label, quantity, unit_cash, aog_risk in choices:
        candidates.append(
            reconcile_candidate(
                frontier_id=fingerprint,
                tenant_id=TENANT,
                pn=pn,
                location=location,
                decision_key=decision_key,
                member_keys=(decision_key,),
                candidate_kind="purchase",
                label=label,
                is_no_change=False,
                model_identity=MODEL,
                current_levels=LEVELS,
                target_levels=LEVELS,
                actions=(
                    CandidateActionLine(
                        line_id=f"purchase-{label}",
                        kind="purchase",
                        quantity=quantity,
                        currency="USD",
                        unit_acquisition_cash=unit_cash,
                        destination_location=location,
                    ),
                ),
                available_before=0,
                expected_receipts_before=0,
                projected_demand=demand,
                economics=ECONOMICS,
                expected_aog_risk=Decimal(aog_risk),
                confidence=Decimal("0.8"),
                constraints=(),
                evidence=EVIDENCE,
            )
        )
    frontier = CandidatePlanner().build_frontier(
        inputs=inputs,
        candidates=tuple(candidates),
    )
    return frontier


def _request(
    menus: tuple[PortfolioKeyMenu, ...],
    *,
    budget: int | str,
    weights: TenantObjectiveWeights = NO_COST_WEIGHTS,
) -> PortfolioSolveRequest:
    return PortfolioSolveRequest(
        tenant_id=TENANT,
        source_snapshot_hash="snapshot-1",
        horizon_days=30,
        currency="USD",
        budget=str(budget),
        menus=menus,
        objective_weights=weights,
        tenant_policy_version="policy-config-v1",
        forecast_version="forecast-v1",
        repair_model_version="repair-return.v1",
        candidate_planner_version="candidate-planner-v1",
        time_limit_seconds=5,
    )


def _menus() -> tuple[PortfolioKeyMenu, ...]:
    return (
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-A@MIA",
                choices=(("full", 10, 1, "0.1"),),
            ),
            criticality_tier=1,
        ),
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-B@MIA",
                choices=(("full", 10, 1, "0.1"),),
            ),
            criticality_tier=5,
        ),
    )


def _candidate_value(
    request: PortfolioSolveRequest,
    menu: PortfolioKeyMenu,
    candidate: PolicyCandidate,
) -> Decimal:
    baseline = next(
        item for item in menu.frontier.candidates if item.is_no_change
    )
    weights = request.objective_weights
    criticality = weights.criticality_weights[menu.criticality_tier]
    return criticality * (
        weights.shortage_reduction_weight
        * (
            baseline.outcome.expected_shortage
            - candidate.outcome.expected_shortage
        )
        + weights.aog_risk_reduction_weight
        * (
            baseline.outcome.expected_aog_risk
            - candidate.outcome.expected_aog_risk
        )
    ) - (
        weights.holding_cost_penalty_weight
        * (
            candidate.lifecycle_costs.holding_cost
            - baseline.lifecycle_costs.holding_cost
        )
        + weights.ordering_cost_penalty_weight
        * (
            candidate.lifecycle_costs.ordering_cost
            - baseline.lifecycle_costs.ordering_cost
        )
    )


def test_hard_budget_selects_exactly_one_candidate_per_key() -> None:
    result = PortfolioOptimizer().solve(_request(_menus(), budget=10))

    assert result.status == "completed"
    assert result.solver.termination == "optimal"
    assert result.solver.optimality_proven
    assert result.summary is not None
    assert result.summary.selected_key_count == 2
    assert result.summary.selected_acquisition_cash == 10
    assert result.summary.budget_slack == 0
    assert sum(selection.selected_is_no_change for selection in result.selections) == 1
    selected_purchase = next(
        selection
        for selection in result.selections
        if not selection.selected_is_no_change
    )
    assert selected_purchase.decision_key == "PN-A@MIA"


def test_zero_and_ample_budgets_choose_expected_extremes() -> None:
    zero = PortfolioOptimizer().solve(_request(_menus(), budget=0))
    ample = PortfolioOptimizer().solve(_request(_menus(), budget=100))

    assert zero.summary is not None
    assert zero.summary.selected_acquisition_cash == 0
    assert zero.summary.no_change_key_count == 2
    assert ample.summary is not None
    assert ample.summary.selected_acquisition_cash == 20
    assert ample.summary.no_change_key_count == 0
    assert ample.summary.selected_objective >= zero.summary.selected_objective


def test_mandatory_floor_and_budget_shortfall_are_explicit() -> None:
    floor = MandatoryFloor(
        floor_id="critical-service-floor",
        source="tenant_policy",
        min_service_level=Decimal("1"),
    )
    menus = tuple(
        menu.model_copy(update={"mandatory_floors": (floor,)})
        for menu in _menus()
    )

    result = PortfolioOptimizer().solve(_request(menus, budget=15))

    assert result.status == "infeasible"
    assert result.selections == ()
    assert result.summary is None
    assert result.minimum_budget_required == 20
    assert result.budget_shortfall == 5
    assert result.solver.termination == "infeasible"


def test_impossible_floor_identifies_key_and_floor() -> None:
    impossible = MandatoryFloor(
        floor_id="zero-shortage",
        source="regulatory",
        max_expected_shortage=Decimal("0"),
    )
    menu = PortfolioKeyMenu(
        frontier=_frontier(
            "PN-A@MIA",
            choices=(("partial", 5, 1, "0.2"),),
        ),
        criticality_tier=1,
        mandatory_floors=(impossible,),
    )

    result = PortfolioOptimizer().solve(_request((menu,), budget=100))

    assert result.status == "infeasible"
    assert result.infeasible_keys == ("PN-A@MIA",)
    assert result.infeasible_floor_ids == ("zero-shortage",)
    assert result.selections == ()


def test_small_instance_matches_brute_force_oracle() -> None:
    menus = (
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-A@MIA",
                choices=(
                    ("partial", 4, 1, "0.5"),
                    ("full", 10, 1, "0.2"),
                ),
            ),
            criticality_tier=2,
        ),
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-B@MIA",
                choices=(
                    ("partial", 5, 1, "0.4"),
                    ("full", 10, 1, "0.1"),
                ),
            ),
            criticality_tier=3,
        ),
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-C@MIA",
                choices=(("full", 10, 1, "0.1"),),
            ),
            criticality_tier=5,
        ),
    )
    request = _request(menus, budget=14)
    result = PortfolioOptimizer().solve(request)

    feasible = []
    for selection in itertools.product(
        *(menu.frontier.candidates for menu in menus)
    ):
        spend = sum(
            (
                candidate.lifecycle_costs.acquisition_cash
                for candidate in selection
            ),
            Decimal("0"),
        )
        if spend <= request.budget:
            feasible.append(
                sum(
                    (
                        _candidate_value(request, menu, candidate)
                        for menu, candidate in zip(menus, selection, strict=True)
                    ),
                    Decimal("0"),
                )
            )

    assert result.summary is not None
    assert result.summary.selected_objective == max(feasible)


def test_equal_objective_tie_breaks_to_lower_spend() -> None:
    weights = TenantObjectiveWeights(
        shortage_reduction_weight=Decimal("1"),
        aog_risk_reduction_weight=Decimal("10"),
        holding_cost_penalty_weight=Decimal("0"),
        ordering_cost_penalty_weight=Decimal("0"),
    )
    menu = PortfolioKeyMenu(
        frontier=_frontier(
            "PN-A@MIA",
            choices=(
                # Objective: shortage reduction 5 + AOG reduction .5 * 10 = 10.
                ("cheap-risk", 5, 1, "0.4"),
                # Objective: shortage reduction 10 + no AOG reduction = 10.
                ("expensive-shortage", 10, 1, "0.9"),
            ),
        ),
        criticality_tier=5,
    )

    result = PortfolioOptimizer().solve(
        _request((menu,), budget=20, weights=weights)
    )

    assert result.summary is not None
    assert result.summary.selected_acquisition_cash == 5


def test_input_permutation_is_deterministic_and_fingerprint_sensitive() -> None:
    menus = _menus()
    first_request = _request(menus, budget=10)
    reversed_request = _request(tuple(reversed(menus)), budget=10)
    first = PortfolioOptimizer().solve(first_request)
    second = PortfolioOptimizer().solve(reversed_request)

    assert first_request == reversed_request
    assert first.planning_fingerprint == second.planning_fingerprint
    assert first.selections == second.selections
    assert first.summary == second.summary
    assert planning_fingerprint(
        first_request.model_copy(update={"budget": Decimal("11")})
    ) != planning_fingerprint(first_request)
    assert planning_fingerprint(
        first_request.model_copy(update={"horizon_days": 60})
    ) != planning_fingerprint(first_request)


def test_time_limited_feasible_incumbent_is_never_presented_as_exact(
    monkeypatch,
) -> None:
    menus = _menus()
    request = _request(menus, budget=10)
    # Canonical variable order is baseline/purchase per key. Select the first
    # key's purchase and the second key's no-change as a feasible incumbent.
    fake = SimpleNamespace(
        x=[0.0, 1.0, 1.0, 0.0],
        status=1,
        message="Time limit reached",
        mip_dual_bound=-100.0,
        mip_gap=Decimal("0.25"),
        mip_node_count=2,
    )
    monkeypatch.setattr(optimizer_module, "milp", lambda **_kwargs: fake)

    result = PortfolioOptimizer().solve(request)

    assert result.status == "completed"
    assert result.solver.termination == "not_proven"
    assert not result.solver.optimality_proven
    assert result.solver.relative_gap == Decimal("0.25")


def test_exactly_one_constraint_uses_full_network_safe_sparse_matrix(
    monkeypatch,
) -> None:
    captured = {}
    fake = SimpleNamespace(
        x=[0.0, 1.0, 1.0, 0.0],
        status=1,
        message="Time limit reached",
        mip_dual_bound=-100.0,
        mip_gap=Decimal("0.25"),
        mip_node_count=2,
    )

    def fake_milp(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(optimizer_module, "milp", fake_milp)

    result = PortfolioOptimizer().solve(_request(_menus(), budget=10))

    assert result.status == "completed"
    assert issparse(captured["constraints"][0].A)
    assert captured["constraints"][0].A.shape == (2, 4)
    assert captured["constraints"][0].A.nnz == 4


def test_invalid_solver_incumbent_fails_without_actionable_selections(
    monkeypatch,
) -> None:
    fake = SimpleNamespace(
        x=[1.0, 1.0, 1.0, 1.0],
        status=1,
        message="Invalid incumbent",
        mip_dual_bound=None,
        mip_gap=None,
        mip_node_count=0,
    )
    monkeypatch.setattr(optimizer_module, "milp", lambda **_kwargs: fake)

    result = PortfolioOptimizer().solve(_request(_menus(), budget=10))

    assert result.status == "failed"
    assert result.selections == ()
    assert result.summary is None
    assert result.solver.termination == "failed"


def test_randomized_small_menus_match_exact_oracle() -> None:
    generator = random.Random(20260728)

    for case in range(12):
        menus = tuple(
            PortfolioKeyMenu(
                frontier=_frontier(
                    f"PN-{case:02d}-{key_index}@MIA",
                    choices=tuple(
                        (
                            f"choice-{choice_index}",
                            generator.randint(1, 9),
                            generator.randint(1, 3),
                            str(
                                Decimal(generator.randint(0, 8))
                                / Decimal("10")
                            ),
                        )
                        for choice_index in range(3)
                    ),
                    demand=10,
                ),
                criticality_tier=generator.randint(1, 5),
            )
            for key_index in range(3)
        )
        request = _request(
            menus,
            budget=generator.randint(0, 35),
        )
        result = PortfolioOptimizer().solve(request)
        feasible: list[tuple[Decimal, Decimal, tuple[str, ...]]] = []
        for candidates in itertools.product(
            *(menu.frontier.candidates for menu in menus)
        ):
            spend = sum(
                (
                    candidate.lifecycle_costs.acquisition_cash
                    for candidate in candidates
                ),
                Decimal("0"),
            )
            if spend > request.budget:
                continue
            feasible.append(
                (
                    sum(
                        (
                            _candidate_value(request, menu, candidate)
                            for menu, candidate in zip(
                                menus,
                                candidates,
                                strict=True,
                            )
                        ),
                        Decimal("0"),
                    ),
                    spend,
                    tuple(candidate.candidate_id for candidate in candidates),
                )
            )

        assert result.status == "completed"
        assert result.summary is not None
        expected_objective = max(item[0] for item in feasible)
        expected_spend = min(
            item[1] for item in feasible if item[0] == expected_objective
        )
        assert result.summary.selected_objective == expected_objective
        assert result.summary.selected_acquisition_cash == expected_spend


def test_increasing_budget_never_worsens_objective() -> None:
    menus = (
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-A@MIA",
                choices=(
                    ("small", 2, 1, "0.7"),
                    ("medium", 5, 1, "0.4"),
                    ("large", 10, 1, "0.1"),
                ),
            ),
            criticality_tier=1,
        ),
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-B@MIA",
                choices=(
                    ("small", 3, 1, "0.6"),
                    ("large", 10, 1, "0.1"),
                ),
            ),
            criticality_tier=2,
        ),
    )

    objectives = []
    for budget in range(0, 22):
        result = PortfolioOptimizer().solve(_request(menus, budget=budget))
        assert result.summary is not None
        objectives.append(result.summary.selected_objective)

    assert objectives == sorted(objectives)


def test_tenant_weight_change_can_change_selected_key() -> None:
    menus = (
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-SHORTAGE@MIA",
                choices=(("shortage", 10, 1, "0.9"),),
            ),
            criticality_tier=5,
        ),
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-AOG@MIA",
                choices=(("aog", 1, 10, "0.0"),),
            ),
            criticality_tier=5,
        ),
    )
    shortage_weights = TenantObjectiveWeights(
        shortage_reduction_weight=Decimal("1"),
        aog_risk_reduction_weight=Decimal("0"),
        holding_cost_penalty_weight=Decimal("0"),
        ordering_cost_penalty_weight=Decimal("0"),
    )
    aog_weights = TenantObjectiveWeights(
        shortage_reduction_weight=Decimal("1"),
        aog_risk_reduction_weight=Decimal("20"),
        holding_cost_penalty_weight=Decimal("0"),
        ordering_cost_penalty_weight=Decimal("0"),
    )

    shortage_result = PortfolioOptimizer().solve(
        _request(menus, budget=10, weights=shortage_weights)
    )
    aog_result = PortfolioOptimizer().solve(
        _request(menus, budget=10, weights=aog_weights)
    )

    assert next(
        selection.decision_key
        for selection in shortage_result.selections
        if not selection.selected_is_no_change
    ) == "PN-SHORTAGE@MIA"
    assert next(
        selection.decision_key
        for selection in aog_result.selections
        if not selection.selected_is_no_change
    ) == "PN-AOG@MIA"


def test_equal_objective_and_spend_uses_stable_candidate_id_order() -> None:
    weights = TenantObjectiveWeights(
        shortage_reduction_weight=Decimal("1"),
        aog_risk_reduction_weight=Decimal("10"),
        holding_cost_penalty_weight=Decimal("0"),
        ordering_cost_penalty_weight=Decimal("0"),
    )
    menu = PortfolioKeyMenu(
        frontier=_frontier(
            "PN-TIE@MIA",
            choices=(
                # Both candidates spend 5 and contribute objective 10.
                ("shortage", 5, 1, "0.4"),
                ("risk", 1, 5, "0.0"),
            ),
        ),
        criticality_tier=5,
    )
    request = _request((menu,), budget=5, weights=weights)

    first = PortfolioOptimizer().solve(request)
    second = PortfolioOptimizer().solve(request)
    tied_ids = sorted(
        candidate.candidate_id
        for candidate in menu.frontier.candidates
        if not candidate.is_no_change
        and _candidate_value(request, menu, candidate) == Decimal("10")
        and candidate.lifecycle_costs.acquisition_cash == Decimal("5")
    )

    assert len(tied_ids) == 2
    assert first.selections[0].selected_candidate_id == tied_ids[0]
    assert second.selections == first.selections
    assert second.summary == first.summary


def test_multi_key_tie_uses_total_lexicographic_candidate_order() -> None:
    menus = tuple(
        PortfolioKeyMenu(
            frontier=_frontier(
                decision_key,
                choices=(("one-unit-improvement", 1, 1, "0.8"),),
            ),
            criticality_tier=1,
        )
        for decision_key in ("PN-LEX-A@MIA", "PN-LEX-B@MIA")
    )
    request = _request(menus, budget=1)

    feasible: list[
        tuple[Decimal, Decimal, tuple[str, ...]]
    ] = []
    for candidates in itertools.product(
        *(menu.frontier.candidates for menu in menus)
    ):
        spend = sum(
            (
                candidate.lifecycle_costs.acquisition_cash
                for candidate in candidates
            ),
            Decimal("0"),
        )
        if spend > request.budget:
            continue
        feasible.append(
            (
                sum(
                    (
                        _candidate_value(request, menu, candidate)
                        for menu, candidate in zip(
                            menus,
                            candidates,
                            strict=True,
                        )
                    ),
                    Decimal("0"),
                ),
                spend,
                tuple(candidate.candidate_id for candidate in candidates),
            )
        )
    best_objective = max(item[0] for item in feasible)
    best_spend = min(
        item[1] for item in feasible if item[0] == best_objective
    )
    expected_ids = min(
        item[2]
        for item in feasible
        if item[0] == best_objective and item[1] == best_spend
    )

    result = PortfolioOptimizer().solve(request)

    assert result.solver.termination == "optimal"
    assert tuple(
        selection.selected_candidate_id for selection in result.selections
    ) == expected_ids


def test_ample_budget_matches_independent_per_key_optima() -> None:
    menus = (
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-A@MIA",
                choices=(
                    ("partial", 4, 1, "0.5"),
                    ("full", 10, 1, "0.2"),
                ),
            ),
            criticality_tier=1,
        ),
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-B@MIA",
                choices=(
                    ("risk", 2, 1, "0.0"),
                    ("full", 10, 1, "0.1"),
                ),
            ),
            criticality_tier=3,
        ),
        PortfolioKeyMenu(
            frontier=_frontier(
                "PN-C@MIA",
                choices=(("full", 10, 2, "0.1"),),
            ),
            criticality_tier=5,
        ),
    )
    request = _request(menus, budget=1_000)
    result = PortfolioOptimizer().solve(request)

    assert result.summary is not None
    selected = {
        selection.decision_key: selection.selected_candidate_id
        for selection in result.selections
    }
    for menu in menus:
        expected = min(
            menu.frontier.candidates,
            key=lambda candidate: (
                -_candidate_value(request, menu, candidate),
                candidate.lifecycle_costs.acquisition_cash,
                candidate.candidate_id,
            ),
        )
        assert selected[menu.frontier.decision_key] == expected.candidate_id


def test_run_outcome_reconciles_current_selected_and_budget_rejection() -> None:
    menu = PortfolioKeyMenu(
        frontier=_frontier(
            "PN-EXPLAIN@MIA",
            choices=(
                ("partial", 5, 1, "0.4"),
                ("full", 10, 1, "0.1"),
            ),
        ),
        criticality_tier=1,
    )
    request = _request((menu,), budget=5)
    result = PortfolioOptimizer().solve(request)

    outcome = build_planning_run_outcome(
        run_id="run-explain",
        request=request,
        result=result,
    )

    assert outcome.advisory_only
    assert outcome.result.summary is not None
    assert len(outcome.selection_details) == 1
    detail = outcome.selection_details[0]
    assert detail.current.candidate_kind == "no_change"
    assert detail.selected.candidate_id == result.selections[0].selected_candidate_id
    assert detail.selected.acquisition_cash == 5
    assert detail.rejected_alternatives[0].reason_code == "budget"
    assert "hard acquisition budget" in detail.rejected_alternatives[0].reason
    assert "objective contribution" in detail.selected_reason
    assert "confidence 0.8" in detail.selected_reason
    assert "planning_trace evidence from snapshot" in detail.selected_reason


def test_run_summary_reconciles_warning_and_selected_confidence_totals() -> None:
    request = _request(_menus(), budget=10)
    result = PortfolioOptimizer().solve(request)
    outcome = build_planning_run_outcome(
        run_id="run-summary-evidence",
        request=request,
        result=result,
        warnings=(
            PlanningWarning(
                code="repair_evidence_limited",
                count=2,
                detail="Two repair evidence warning instances were reported.",
            ),
        ),
    )

    summary = outcome.result.summary
    assert summary is not None
    assert summary.warning_count == 2
    assert summary.confidence_summary is not None
    assert summary.confidence_summary.selected_confidence_total == Decimal("1.6")
    assert summary.confidence_summary.minimum_selected_confidence == Decimal("0.8")
    assert summary.confidence_summary.low_confidence_threshold == Decimal("0.5")
    assert summary.confidence_summary.low_confidence_key_count == 0

    payload = outcome.model_dump()
    payload["result"]["summary"]["warning_count"] = 1
    with pytest.raises(ValueError, match="warning count does not reconcile"):
        type(outcome).model_validate(payload)

    payload = outcome.model_dump()
    payload["result"]["summary"]["confidence_summary"][
        "selected_confidence_total"
    ] = Decimal("1.5")
    with pytest.raises(
        ValueError,
        match="selected confidence total does not reconcile",
    ):
        type(outcome).model_validate(payload)


def test_run_explanation_does_not_blame_budget_for_mandatory_floor() -> None:
    floor = MandatoryFloor(
        floor_id="aog-floor",
        source="tenant_policy",
        max_aog_risk=Decimal("0.3"),
    )
    menu = PortfolioKeyMenu(
        frontier=_frontier(
            "PN-FLOOR@MIA",
            choices=(
                ("cheap", 5, 1, "0.4"),
                ("safe", 10, 1, "0.2"),
            ),
        ),
        criticality_tier=1,
        mandatory_floors=(floor,),
    )
    request = _request((menu,), budget=20)
    result = PortfolioOptimizer().solve(request)

    outcome = build_planning_run_outcome(
        run_id="run-floor",
        request=request,
        result=result,
    )

    rejected = outcome.selection_details[0].rejected_alternatives
    assert len(rejected) == 1
    assert rejected[0].candidate.label == "cheap"
    assert rejected[0].reason_code == "mandatory_floor"
    assert rejected[0].candidate.mandatory_floor_ids == ("aog-floor",)
    assert "budget" not in rejected[0].reason.lower()


def test_rerun_lineage_reports_only_material_changes() -> None:
    parent = _request(_menus(), budget=10)
    unchanged = parent.model_copy(deep=True)
    changed = parent.model_copy(update={"budget": Decimal("15")})

    assert planning_assumption_diff(parent, unchanged) == ()
    assert planning_fingerprint(parent) == planning_fingerprint(unchanged)
    changes = planning_assumption_diff(parent, changed)
    assert tuple(change.field for change in changes) == ("budget",)
    assert planning_fingerprint(parent) != planning_fingerprint(changed)

    result = PortfolioOptimizer().solve(changed)
    outcome = build_planning_run_outcome(
        run_id="run-child",
        parent_run_id="run-parent",
        parent_request=parent,
        request=changed,
        result=result,
    )
    assert outcome.parent_run_id == "run-parent"
    assert outcome.parent_planning_fingerprint == planning_fingerprint(parent)
    assert outcome.parent_source_snapshot_hash == parent.source_snapshot_hash
    assert outcome.assumption_diff == changes


def test_rerun_menu_diff_uses_bounded_fingerprint_evidence() -> None:
    parent = _request(_menus(), budget=10)
    rerun = _request(
        (
            PortfolioKeyMenu(
                frontier=_frontier(
                    "PN-CHANGED@MIA",
                    choices=(("full", 10, 1, "0.1"),),
                ),
                criticality_tier=1,
            ),
        ),
        budget=10,
    )

    changes = planning_assumption_diff(parent, rerun)

    assert tuple(change.field for change in changes) == ("menus",)
    before = json.loads(changes[0].before)
    after = json.loads(changes[0].after)
    assert set(before) == set(after) == {"menu_count", "menus_fingerprint"}
    assert before["menu_count"] == 2
    assert after["menu_count"] == 1
    assert before["menus_fingerprint"] != after["menus_fingerprint"]
    assert len(changes[0].before) < 200
    assert len(changes[0].after) < 200


def test_run_outcome_rejects_mismatched_result_identity() -> None:
    request = _request(_menus(), budget=10)
    other_request = request.model_copy(update={"budget": Decimal("11")})
    result = PortfolioOptimizer().solve(other_request)

    with pytest.raises(
        ValueError,
        match="does not belong to the planning request",
    ):
        build_planning_run_outcome(
            run_id="run-invalid",
            request=request,
            result=result,
        )


def test_infeasible_run_never_exposes_actionable_selection_details() -> None:
    floor = MandatoryFloor(
        floor_id="service-floor",
        source="tenant_policy",
        min_service_level=Decimal("1"),
    )
    menus = tuple(
        menu.model_copy(update={"mandatory_floors": (floor,)})
        for menu in _menus()
    )
    request = _request(menus, budget=5)
    result = PortfolioOptimizer().solve(request)

    outcome = build_planning_run_outcome(
        run_id="run-infeasible",
        request=request,
        result=result,
    )

    assert outcome.status == "infeasible"
    assert outcome.selection_details == ()
    assert outcome.result.selections == ()


def test_objective_weight_mapping_is_deeply_immutable() -> None:
    weights = TenantObjectiveWeights()
    request = _request(_menus(), budget=10, weights=weights)
    before = planning_fingerprint(request)

    with pytest.raises(TypeError, match="immutable"):
        weights.criticality_weights[1] = Decimal("999")
    with pytest.raises(TypeError, match="immutable"):
        weights.criticality_weights.__ior__({1: Decimal("999")})

    assert planning_fingerprint(request) == before
    assert request.objective_weights.criticality_weights[1] == 5


def test_run_outcome_rejects_tampered_detail_and_request_summary() -> None:
    request = _request(_menus(), budget=10)
    result = PortfolioOptimizer().solve(request)
    outcome = build_planning_run_outcome(
        run_id="run-contract-tamper",
        request=request,
        result=result,
    )

    detail_payload = outcome.model_dump()
    detail_payload["selection_details"][0]["selected"][
        "expected_shortage"
    ] += Decimal("1")
    with pytest.raises(
        ValueError,
        match="selection detail does not reconcile",
    ):
        type(outcome).model_validate(detail_payload)

    summary_payload = outcome.model_dump()
    summary_payload["result"]["summary"]["budget"] = Decimal("999")
    summary_payload["result"]["summary"]["budget_slack"] = (
        Decimal("999")
        - summary_payload["result"]["summary"]["selected_acquisition_cash"]
    )
    with pytest.raises(
        ValueError,
        match="summary currency and budget must match request",
    ):
        type(outcome).model_validate(summary_payload)

    currency_payload = outcome.model_dump()
    currency_payload["result"]["summary"]["currency"] = "EUR"
    with pytest.raises(
        ValueError,
        match="summary currency and budget must match request",
    ):
        type(outcome).model_validate(currency_payload)


def test_result_contract_rejects_tampered_summary_metrics() -> None:
    result = PortfolioOptimizer().solve(_request(_menus(), budget=10))
    assert result.summary is not None
    payload = result.model_dump()
    payload["summary"]["expected_shortage"] = Decimal("999")

    with pytest.raises(
        ValueError,
        match="expected shortage does not reconcile",
    ):
        type(result).model_validate(payload)


def test_run_explanation_rejects_tampered_selection_candidate_metrics() -> None:
    request = _request(_menus(), budget=10)
    result = PortfolioOptimizer().solve(request)
    payload = result.model_dump()
    payload["selections"][0]["expected_shortage"] += Decimal("1")
    payload["summary"]["expected_shortage"] += Decimal("1")
    tampered = type(result).model_validate(payload)

    with pytest.raises(
        ValueError,
        match="does not reconcile to candidate frontier",
    ):
        build_planning_run_outcome(
            run_id="run-tampered",
            request=request,
            result=tampered,
        )


def test_rejection_explanation_preserves_candidate_infeasibility_reason() -> None:
    frontier = _frontier(
        "PN-INFEASIBLE@MIA",
        choices=(("shop-choice", 10, 1, "0.1"),),
    )
    candidate = next(
        item for item in frontier.candidates if not item.is_no_change
    )
    infeasible = candidate.model_copy(
        update={
            "feasible": False,
            "infeasibility_reasons": ("repair shop unavailable",),
        }
    )
    frontier_payload = frontier.model_dump()
    frontier_payload["candidates"] = [
        item.model_dump()
        for item in sorted(
            (
                next(item for item in frontier.candidates if item.is_no_change),
                infeasible,
            ),
            key=lambda item: (not item.is_no_change, item.candidate_id),
        )
    ]
    menu = PortfolioKeyMenu(
        frontier=type(frontier).model_validate(frontier_payload),
        criticality_tier=1,
    )
    request = _request((menu,), budget=100)
    result = PortfolioOptimizer().solve(request)

    outcome = build_planning_run_outcome(
        run_id="run-infeasible-choice",
        request=request,
        result=result,
    )

    rejected = outcome.selection_details[0].rejected_alternatives[0]
    assert rejected.reason_code == "candidate_infeasible"
    assert rejected.candidate.infeasibility_reasons == (
        "repair shop unavailable",
    )
    assert "individually feasible" not in rejected.reason
