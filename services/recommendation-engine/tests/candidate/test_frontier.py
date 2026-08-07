from __future__ import annotations

import itertools
import random
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trax_io_reco.candidate.frontier import dominates, prune_dominated
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
    CandidateFrontier,
    CandidateTargetLevels,
    LifecycleEconomics,
    PolicyCandidate,
)


def _purchase_candidate(
    *,
    fingerprint: str,
    decision_key: str,
    quantity: int,
    unit_cash: int,
    label: str,
    current_levels: CandidateTargetLevels,
    economics: LifecycleEconomics,
    model_identity,
    source_evidence: tuple[CandidateEvidence, ...],
    demand: int = 6,
    aog_risk: Decimal = Decimal("0.2"),
    target_levels: CandidateTargetLevels | None = None,
) -> PolicyCandidate:
    pn, location = decision_key.split("@", maxsplit=1)
    return reconcile_candidate(
        frontier_id=fingerprint,
        tenant_id="tenant-a",
        pn=pn,
        location=location,
        decision_key=decision_key,
        member_keys=(decision_key,),
        candidate_kind="purchase",
        label=label,
        is_no_change=False,
        model_identity=model_identity,
        current_levels=current_levels,
        target_levels=target_levels or current_levels,
        actions=(
            CandidateActionLine(
                line_id=f"buy-{label}",
                kind="purchase",
                quantity=quantity,
                currency=economics.currency,
                unit_acquisition_cash=unit_cash,
                destination_location=location,
            ),
        ),
        available_before=0,
        expected_receipts_before=0,
        projected_demand=demand,
        economics=economics,
        expected_aog_risk=aog_risk,
        confidence=Decimal("0.8"),
        constraints=(),
        evidence=source_evidence,
    )


def test_pruning_requires_strict_pareto_improvement_and_retains_audit_choices(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    fingerprint = frontier_fingerprint(fingerprint_inputs)
    baseline = build_no_change_candidate(
        frontier_id=fingerprint,
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        model_identity=model_identity,
        current_levels=current_levels,
        available_before=0,
        expected_receipts_before=0,
        projected_demand=6,
        economics=economics,
        expected_aog_risk=Decimal("0.8"),
        confidence=Decimal("0.8"),
        constraints=(),
        evidence=source_evidence,
    )
    cheaper = _purchase_candidate(
        fingerprint=fingerprint,
        decision_key="PN-1@MIA",
        quantity=6,
        unit_cash=10,
        label="cheaper",
        current_levels=current_levels,
        economics=economics,
        model_identity=model_identity,
        source_evidence=source_evidence,
        aog_risk=Decimal("0.1"),
    )
    expensive = _purchase_candidate(
        fingerprint=fingerprint,
        decision_key="PN-1@MIA",
        quantity=6,
        unit_cash=20,
        label="expensive",
        current_levels=current_levels,
        economics=economics,
        model_identity=model_identity,
        source_evidence=source_evidence,
        aog_risk=Decimal("0.1"),
    )
    invalid_target = CandidateTargetLevels(
        rop=5,
        eoq=5,
        safety_stock=2,
        max_stock=8,
    )
    infeasible = _purchase_candidate(
        fingerprint=fingerprint,
        decision_key="PN-1@MIA",
        quantity=6,
        unit_cash=30,
        label="infeasible",
        current_levels=current_levels,
        economics=economics,
        model_identity=model_identity,
        source_evidence=source_evidence,
        target_levels=invalid_target,
    )

    assert dominates(cheaper, expensive)
    assert not dominates(expensive, cheaper)
    result = prune_dominated((expensive, infeasible, baseline, cheaper))
    assert expensive.candidate_id in result.removed_candidate_ids
    assert baseline in result.candidates
    assert infeasible in result.candidates
    assert cheaper in result.candidates


def test_dominance_fails_closed_across_decision_key_or_currency(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    first = _purchase_candidate(
        fingerprint=frontier_fingerprint(fingerprint_inputs),
        decision_key="PN-1@MIA",
        quantity=6,
        unit_cash=10,
        label="first",
        current_levels=current_levels,
        economics=economics,
        model_identity=model_identity,
        source_evidence=source_evidence,
    )
    other_inputs = CandidateFingerprintInputs.model_validate(
        {
            **fingerprint_inputs.model_dump(mode="python"),
            "decision_key": "PN-2@MIA",
            "member_keys": ("PN-2@MIA",),
        }
    )
    second = _purchase_candidate(
        fingerprint=frontier_fingerprint(other_inputs),
        decision_key="PN-2@MIA",
        quantity=6,
        unit_cash=10,
        label="second",
        current_levels=current_levels,
        economics=economics,
        model_identity=model_identity,
        source_evidence=source_evidence,
    )
    with pytest.raises(ValueError, match="different decision keys"):
        dominates(first, second)

    eur_economics = LifecycleEconomics.model_validate(
        {**economics.model_dump(mode="python"), "currency": "EUR"}
    )
    eur = _purchase_candidate(
        fingerprint=frontier_fingerprint(fingerprint_inputs),
        decision_key="PN-1@MIA",
        quantity=6,
        unit_cash=10,
        label="eur",
        current_levels=current_levels,
        economics=eur_economics,
        model_identity=model_identity,
        source_evidence=source_evidence,
    )
    with pytest.raises(ValueError, match="different currencies"):
        dominates(first, eur)


def test_purchase_and_transfer_resource_vectors_remain_incomparable(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    fingerprint = frontier_fingerprint(fingerprint_inputs)
    purchase = _purchase_candidate(
        fingerprint=fingerprint,
        decision_key="PN-1@MIA",
        quantity=6,
        unit_cash=10,
        label="purchase",
        current_levels=current_levels,
        economics=economics,
        model_identity=model_identity,
        source_evidence=source_evidence,
        aog_risk=Decimal("0.1"),
    )
    transfer = reconcile_candidate(
        frontier_id=fingerprint,
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        candidate_kind="transfer",
        label="transfer",
        is_no_change=False,
        model_identity=model_identity,
        current_levels=current_levels,
        target_levels=current_levels,
        actions=(
            CandidateActionLine(
                line_id="transfer",
                kind="transfer_in",
                quantity=6,
                currency="USD",
                unit_acquisition_cash=0,
                source_location="JFK",
                destination_location="MIA",
            ),
        ),
        available_before=0,
        expected_receipts_before=0,
        projected_demand=6,
        economics=economics,
        expected_aog_risk=Decimal("0.1"),
        confidence=Decimal("0.8"),
        constraints=(),
        evidence=source_evidence,
    )
    assert not dominates(purchase, transfer)
    assert not dominates(transfer, purchase)


def test_planner_is_deterministic_under_candidate_input_permutation(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    planner = CandidatePlanner()
    fingerprint = planner.fingerprint(fingerprint_inputs)
    baseline = build_no_change_candidate(
        frontier_id=fingerprint,
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        model_identity=model_identity,
        current_levels=current_levels,
        available_before=0,
        expected_receipts_before=0,
        projected_demand=6,
        economics=economics,
        expected_aog_risk=Decimal("0.8"),
        confidence=Decimal("0.8"),
        constraints=(),
        evidence=source_evidence,
    )
    choices = tuple(
        _purchase_candidate(
            fingerprint=fingerprint,
            decision_key="PN-1@MIA",
            quantity=quantity,
            unit_cash=unit_cash,
            label=f"choice-{quantity}-{unit_cash}",
            current_levels=current_levels,
            economics=economics,
            model_identity=model_identity,
            source_evidence=source_evidence,
        )
        for quantity, unit_cash in ((3, 10), (6, 20), (6, 10))
    )
    first = planner.build_frontier(
        inputs=fingerprint_inputs,
        candidates=(baseline, *choices),
    )
    second = planner.build_frontier(
        inputs=fingerprint_inputs,
        candidates=tuple(reversed((baseline, *choices))),
    )
    assert first == second
    assert first.dominated_options_removed == 1
    assert first.total_options_considered == 4
    with pytest.raises(ValidationError, match="canonical stable order"):
        CandidateFrontier.model_validate(
            {
                **first.model_dump(mode="python"),
                "candidates": tuple(reversed(first.candidates)),
            }
        )

    tampered = baseline.model_copy(update={"confidence": Decimal("0.1")})
    with pytest.raises(ValueError, match="was not built for this fingerprint"):
        planner.build_frontier(
            inputs=fingerprint_inputs,
            candidates=(tampered, *choices),
        )


def _objective(candidate: PolicyCandidate, weights: tuple[Decimal, ...]) -> Decimal:
    costs = candidate.lifecycle_costs
    outcome = candidate.outcome
    dimensions = (
        outcome.expected_shortage,
        outcome.expected_aog_risk,
        costs.holding_cost,
        costs.ordering_cost,
        costs.shortage_cost,
        costs.other_cost,
    )
    return -sum(
        (weight * value for weight, value in zip(weights, dimensions, strict=True)),
        Decimal("0"),
    )


def _best_portfolio_value(
    menus: tuple[tuple[PolicyCandidate, ...], ...],
    *,
    budget: Decimal,
    weights: tuple[Decimal, ...],
) -> Decimal:
    feasible_values = []
    for selection in itertools.product(*menus):
        if not all(candidate.feasible for candidate in selection):
            continue
        spend = sum(
            (candidate.lifecycle_costs.acquisition_cash for candidate in selection),
            Decimal("0"),
        )
        if spend <= budget:
            feasible_values.append(
                sum((_objective(candidate, weights) for candidate in selection), Decimal("0"))
            )
    return max(feasible_values)


def test_property_pruning_preserves_best_feasible_portfolio_value(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    rng = random.Random(20260728)
    menus: list[tuple[PolicyCandidate, ...]] = []
    for key_index in range(2):
        decision_key = f"PN-{key_index + 1}@MIA"
        inputs = CandidateFingerprintInputs.model_validate(
            {
                **fingerprint_inputs.model_dump(mode="python"),
                "decision_key": decision_key,
                "member_keys": (decision_key,),
                "context_digest": f"context-{key_index}",
            }
        )
        fingerprint = frontier_fingerprint(inputs)
        pn, location = decision_key.split("@", maxsplit=1)
        baseline = build_no_change_candidate(
            frontier_id=fingerprint,
            tenant_id="tenant-a",
            pn=pn,
            location=location,
            decision_key=decision_key,
            member_keys=(decision_key,),
            model_identity=model_identity,
            current_levels=current_levels,
            available_before=0,
            expected_receipts_before=0,
            projected_demand=8,
            economics=economics,
            expected_aog_risk=Decimal("0.9"),
            confidence=Decimal("0.8"),
            constraints=(),
            evidence=source_evidence,
        )
        generated = tuple(
            _purchase_candidate(
                fingerprint=fingerprint,
                decision_key=decision_key,
                quantity=rng.randint(1, 10),
                unit_cash=rng.randint(2, 15),
                label=f"random-{key_index}-{candidate_index}",
                current_levels=current_levels,
                economics=economics,
                model_identity=model_identity,
                source_evidence=source_evidence,
                demand=8,
                aog_risk=Decimal(rng.randint(0, 9)) / Decimal(10),
            )
            for candidate_index in range(8)
        )
        menus.append((baseline, *generated))

    original = tuple(menus)
    pruned = tuple(prune_dominated(menu).candidates for menu in original)
    for _ in range(40):
        weights = tuple(Decimal(rng.randint(0, 5)) for _ in range(6))
        budget = Decimal(rng.choice((0, 25, 50, 100, 200, 500)))
        assert _best_portfolio_value(
            original,
            budget=budget,
            weights=weights,
        ) == _best_portfolio_value(
            pruned,
            budget=budget,
            weights=weights,
        )
