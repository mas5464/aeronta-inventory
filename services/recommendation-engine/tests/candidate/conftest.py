from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from trax_io_reco.contracts.candidate import (
    CandidateEvidence,
    CandidateFingerprintInputs,
    CandidateTargetLevels,
    FingerprintComponent,
    LifecycleEconomics,
    ModelIdentity,
)


@pytest.fixture
def model_identity() -> ModelIdentity:
    return ModelIdentity(
        forecast_model="compound-poisson",
        forecast_version="forecast-artifact-17",
        policy_model="deterministic-s-S",
        policy_version="policy-code-9",
    )


@pytest.fixture
def fingerprint_inputs(model_identity: ModelIdentity) -> CandidateFingerprintInputs:
    return CandidateFingerprintInputs(
        tenant_id="tenant-a",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        source_snapshot_hash="snapshot-123",
        context_digest="context-456",
        tenant_policy_version="tenant-policy-7",
        observation_start=date(2023, 1, 1),
        observation_end=date(2025, 12, 31),
        as_of=date(2026, 1, 31),
        horizon_days=30,
        currency="USD",
        model_identity=model_identity,
        constraint_set_version="constraints-3",
        arbitration_version="transfer-first-2",
        economics_version="economics-4",
        objective_definition_version="objective-1",
        objective_inputs=(
            FingerprintComponent(name="shortage_weight", value="4"),
            FingerprintComponent(name="holding_weight", value="1"),
        ),
    )


@pytest.fixture
def current_levels() -> CandidateTargetLevels:
    return CandidateTargetLevels(
        rop=2,
        eoq=1,
        safety_stock=1,
        max_stock=3,
    )


@pytest.fixture
def economics() -> LifecycleEconomics:
    return LifecycleEconomics(
        currency="USD",
        inventory_unit_value=Decimal("12"),
        annual_holding_rate=Decimal("0.25"),
        ordering_cost_per_purchase=Decimal("10"),
        shortage_cost_per_unit=Decimal("50"),
        other_cost=Decimal("0"),
        horizon_days=30,
    )


@pytest.fixture
def source_evidence() -> tuple[CandidateEvidence, ...]:
    return (
        CandidateEvidence(
            kind="planning_trace",
            source="input_snapshot",
            reference_id="snapshot-123",
            detail="Reconciled demand, stock, and receipt trace",
        ),
    )
