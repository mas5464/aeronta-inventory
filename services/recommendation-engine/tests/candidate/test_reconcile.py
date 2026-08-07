from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from trax_io_reco.candidate.identity import frontier_fingerprint
from trax_io_reco.candidate.reconcile import (
    build_no_change_candidate,
    build_transfer_purchase_candidate,
    reconcile_candidate,
)
from trax_io_reco.contracts.candidate import (
    CandidateActionLine,
    CandidateFingerprintInputs,
    CandidateTargetLevels,
    ConstraintEvidence,
    PolicyCandidate,
)


def test_no_change_has_zero_acquisition_but_real_lifecycle_consequences(
    fingerprint_inputs: CandidateFingerprintInputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    candidate = build_no_change_candidate(
        frontier_id=frontier_fingerprint(fingerprint_inputs),
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        model_identity=model_identity,
        current_levels=current_levels,
        available_before=8,
        expected_receipts_before=0,
        projected_demand=10,
        economics=economics,
        expected_aog_risk=Decimal("0.5"),
        confidence=Decimal("0.8"),
        constraints=(),
        evidence=source_evidence,
    )
    assert candidate.is_no_change
    assert candidate.action_quantity == 0
    assert candidate.lifecycle_costs.acquisition_cash == 0
    assert candidate.outcome.expected_shortage == 2
    assert candidate.lifecycle_costs.shortage_cost == Decimal("100.00")
    assert candidate.lifecycle_costs.total_lifecycle_cost == Decimal("100.00")


def test_invalid_current_policy_remains_visible_as_infeasible_no_change(
    fingerprint_inputs,
    economics,
    model_identity,
    source_evidence,
) -> None:
    invalid_current = CandidateTargetLevels(
        rop=5,
        eoq=4,
        safety_stock=3,
        max_stock=7,
    )
    candidate = build_no_change_candidate(
        frontier_id=frontier_fingerprint(fingerprint_inputs),
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        model_identity=model_identity,
        current_levels=invalid_current,
        available_before=8,
        expected_receipts_before=0,
        projected_demand=10,
        economics=economics,
        expected_aog_risk=Decimal("0.5"),
        confidence=Decimal("0.8"),
        constraints=(),
        evidence=source_evidence,
    )
    assert not candidate.feasible
    floor = next(
        item for item in candidate.constraints if item.constraint_id == "maximum_stock_floor"
    )
    assert floor.hard
    assert not floor.satisfied
    assert floor.binding


def test_transfer_first_then_moq_residual_purchase_reconciles_every_component(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    target = CandidateTargetLevels(
        rop=5,
        eoq=2,
        safety_stock=2,
        max_stock=7,
    )
    candidate = build_transfer_purchase_candidate(
        frontier_id=frontier_fingerprint(fingerprint_inputs),
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        model_identity=model_identity,
        current_levels=current_levels,
        target_levels=target,
        available_before=0,
        expected_receipts_before=0,
        projected_demand=6,
        requested_transfer_quantity=3,
        donor_available_quantity=3,
        donor_location="JFK",
        minimum_order_quantity=5,
        purchase_unit_cost=Decimal("12"),
        economics=economics,
        expected_aog_risk=Decimal("0.1"),
        confidence=Decimal("0.9"),
        constraints=(
            ConstraintEvidence(
                constraint_id="minimum_order_quantity",
                source="legacy.unreconciled",
                value="99",
                hard=True,
                satisfied=False,
                binding=True,
            ),
        ),
        evidence=source_evidence,
    )
    assert candidate.candidate_kind == "transfer_purchase"
    assert candidate.reconciliation.transfer_in_quantity == 3
    assert candidate.reconciliation.purchase_quantity == 5
    assert candidate.action_quantity == 8
    assert candidate.lifecycle_costs.acquisition_cash == Decimal("60")
    assert candidate.lifecycle_costs.holding_cost == Decimal("0.49")
    assert candidate.lifecycle_costs.ordering_cost == Decimal("10.00")
    assert candidate.lifecycle_costs.total_lifecycle_cost == Decimal("70.49")
    assert candidate.outcome.ending_net_position == 2
    assert candidate.outcome.expected_shortage == 0
    moq = next(
        item for item in candidate.constraints if item.constraint_id == "minimum_order_quantity"
    )
    assert moq.binding
    assert moq.source == "vendor_economics.minimum_order_quantity"


def test_purchase_rounding_alone_does_not_claim_moq_is_binding(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    candidate = build_transfer_purchase_candidate(
        frontier_id=frontier_fingerprint(fingerprint_inputs),
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        model_identity=model_identity,
        current_levels=current_levels,
        target_levels=current_levels,
        available_before=0,
        expected_receipts_before=0,
        projected_demand="2.2",
        requested_transfer_quantity=0,
        donor_available_quantity=0,
        donor_location="JFK",
        minimum_order_quantity=1,
        purchase_unit_cost=Decimal("12"),
        economics=economics,
        expected_aog_risk=Decimal("0.1"),
        confidence=Decimal("0.9"),
        constraints=(),
        evidence=source_evidence,
    )
    assert candidate.reconciliation.purchase_quantity == 3
    moq = next(
        item for item in candidate.constraints if item.constraint_id == "minimum_order_quantity"
    )
    assert not moq.binding


def test_mixed_currency_fails_closed_before_candidate_construction(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    with pytest.raises(ValueError, match="mixed or blank"):
        reconcile_candidate(
            frontier_id=frontier_fingerprint(fingerprint_inputs),
            tenant_id="tenant-a",
            pn="PN-1",
            location="MIA",
            decision_key="PN-1@MIA",
            member_keys=("PN-1@MIA",),
            candidate_kind="purchase",
            label="Buy",
            is_no_change=False,
            model_identity=model_identity,
            current_levels=current_levels,
            target_levels=current_levels,
            actions=(
                CandidateActionLine(
                    line_id="buy",
                    kind="purchase",
                    quantity=1,
                    currency="EUR",
                    unit_acquisition_cash=Decimal("10"),
                    destination_location="MIA",
                ),
            ),
            available_before=0,
            expected_receipts_before=0,
            projected_demand=1,
            economics=economics,
            expected_aog_risk=Decimal("0"),
            confidence=Decimal("1"),
            constraints=(),
            evidence=source_evidence,
        )


def test_reconstructed_contract_rejects_tampered_reconciliation(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    candidate = build_no_change_candidate(
        frontier_id=frontier_fingerprint(fingerprint_inputs),
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        model_identity=model_identity,
        current_levels=current_levels,
        available_before=8,
        expected_receipts_before=0,
        projected_demand=10,
        economics=economics,
        expected_aog_risk=Decimal("0.5"),
        confidence=Decimal("0.8"),
        constraints=(),
        evidence=source_evidence,
    )
    payload = candidate.model_dump(mode="python")
    payload["reconciliation"]["ending_net_position"] = Decimal("999")
    with pytest.raises(ValidationError, match="does not reconcile"):
        PolicyCandidate.model_validate(payload)


def test_contract_rejects_cross_ledger_tampering_even_when_net_is_unchanged(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    candidate = build_no_change_candidate(
        frontier_id=frontier_fingerprint(fingerprint_inputs),
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        model_identity=model_identity,
        current_levels=current_levels,
        available_before=8,
        expected_receipts_before=0,
        projected_demand=10,
        economics=economics,
        expected_aog_risk=Decimal("0.5"),
        confidence=Decimal("0.8"),
        constraints=(),
        evidence=source_evidence,
    )
    payload = candidate.model_dump(mode="python")
    payload["outcome"]["available_before"] = Decimal("9")
    payload["outcome"]["projected_demand"] = Decimal("11")
    payload["outcome"]["expected_service_level"] = Decimal("1") - (Decimal("2") / Decimal("11"))
    with pytest.raises(ValidationError, match="available quantities do not match"):
        PolicyCandidate.model_validate(payload)
