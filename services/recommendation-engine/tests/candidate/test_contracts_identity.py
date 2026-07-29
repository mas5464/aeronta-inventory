from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from trax_io_reco.candidate.identity import (
    candidate_identifier,
    canonical_json,
    canonical_sha256,
    content_digest,
    frontier_fingerprint,
    output_digest,
)
from trax_io_reco.candidate.planner import CandidatePlanner
from trax_io_reco.candidate.reconcile import build_no_change_candidate
from trax_io_reco.contracts.candidate import (
    CandidateFingerprintInputs,
    FingerprintComponent,
    LifecycleEconomics,
    ModelIdentity,
)


def test_streaming_canonical_digest_matches_canonical_json_bytes(
    fingerprint_inputs: CandidateFingerprintInputs,
) -> None:
    payload = {
        "namespace": "streaming-equivalence",
        "inputs": fingerprint_inputs,
        "decimal": Decimal("120.5000"),
        "date": date(2026, 7, 28),
        "generated_at": datetime(2026, 7, 28, tzinfo=UTC),
        "nested": {"updated_at": "excluded", "flags": (True, None)},
    }
    canonical = canonical_json(payload, exclude_timestamps=True).encode()

    assert canonical_sha256(
        payload,
        exclude_timestamps=True,
    ) == hashlib.sha256(canonical).hexdigest()


def test_contracts_are_frozen_extra_forbid_and_require_actual_model_labels(
    model_identity: ModelIdentity,
) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        model_identity.forecast_model = "configured-default"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs"):
        ModelIdentity(
            forecast_model="served",
            forecast_version="1",
            policy_model="served-policy",
            policy_version="2",
            configured_model="not-the-served-model",
        )
    with pytest.raises(ValidationError):
        ModelIdentity(
            forecast_model=" ",
            forecast_version="1",
            policy_model="served-policy",
            policy_version="2",
        )
    with pytest.raises(ValidationError, match="supplied together"):
        ModelIdentity(
            forecast_model="served",
            forecast_version="1",
            policy_model="served-policy",
            policy_version="2",
            repair_model="weibull",
        )


def test_decimal_money_boundary_rejects_binary_float_and_bad_currency() -> None:
    with pytest.raises(ValidationError, match="binary floats"):
        LifecycleEconomics(
            currency="USD",
            inventory_unit_value=12.50,
            annual_holding_rate=Decimal("0.25"),
            ordering_cost_per_purchase=Decimal("10"),
            shortage_cost_per_unit=Decimal("50"),
            horizon_days=30,
        )
    with pytest.raises(ValidationError):
        LifecycleEconomics(
            currency="",
            inventory_unit_value=Decimal("12.50"),
            annual_holding_rate=Decimal("0.25"),
            ordering_cost_per_purchase=Decimal("10"),
            shortage_cost_per_unit=Decimal("50"),
            horizon_days=30,
        )
    with pytest.raises(ValidationError):
        LifecycleEconomics(
            currency="USD",
            inventory_unit_value=Decimal("NaN"),
            annual_holding_rate=Decimal("0.25"),
            ordering_cost_per_purchase=Decimal("10"),
            shortage_cost_per_unit=Decimal("50"),
            horizon_days=30,
        )


def test_fingerprint_is_deterministic_canonical_and_excludes_timestamps(
    fingerprint_inputs: CandidateFingerprintInputs,
) -> None:
    same_components_reordered = CandidateFingerprintInputs(
        **{
            **fingerprint_inputs.model_dump(mode="python"),
            "member_keys": tuple(reversed(fingerprint_inputs.member_keys)),
            "objective_inputs": tuple(reversed(fingerprint_inputs.objective_inputs)),
        }
    )
    assert frontier_fingerprint(fingerprint_inputs) == frontier_fingerprint(
        same_components_reordered
    )

    first = frontier_fingerprint(
        {
            "snapshot": "same",
            "generated_at": datetime(2026, 1, 1, tzinfo=UTC),
            "nested": {"updated_at": "first", "result": 4},
        }
    )
    second = frontier_fingerprint(
        {
            "nested": {"result": 4, "updated_at": "second"},
            "generated_at": datetime(2030, 1, 1, tzinfo=UTC),
            "snapshot": "same",
        }
    )
    assert first == second
    assert content_digest({"value": 1, "generated_at": "first"}) == content_digest(
        {"generated_at": "second", "value": 1}
    )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("tenant_id", "tenant-other"),
        ("source_snapshot_hash", "snapshot-other"),
        ("context_digest", "context-other"),
        ("tenant_policy_version", "tenant-policy-other"),
        ("observation_start", date(2023, 2, 1)),
        ("observation_end", date(2025, 11, 30)),
        ("as_of", date(2026, 2, 1)),
        ("horizon_days", 60),
        ("currency", "EUR"),
        ("constraint_set_version", "constraints-other"),
        ("arbitration_version", "arbitration-other"),
        ("economics_version", "economics-other"),
        ("objective_definition_version", "objective-other"),
        (
            "objective_inputs",
            (FingerprintComponent(name="shortage_weight", value="99"),),
        ),
        (
            "additional_result_inputs",
            (FingerprintComponent(name="feature_flags", value="candidate-v2"),),
        ),
    ],
)
def test_every_fingerprint_component_is_identity_sensitive(
    fingerprint_inputs: CandidateFingerprintInputs,
    field_name: str,
    replacement: object,
) -> None:
    changed_payload = fingerprint_inputs.model_dump(mode="python")
    changed_payload[field_name] = replacement
    changed = CandidateFingerprintInputs.model_validate(changed_payload)
    assert frontier_fingerprint(changed) != frontier_fingerprint(fingerprint_inputs)


def test_model_identity_changes_fingerprint(
    fingerprint_inputs: CandidateFingerprintInputs,
) -> None:
    changed_payload = fingerprint_inputs.model_dump(mode="python")
    changed_payload["model_identity"] = {
        **fingerprint_inputs.model_identity.model_dump(mode="python"),
        "forecast_version": "forecast-artifact-18",
    }
    changed = CandidateFingerprintInputs.model_validate(changed_payload)
    assert frontier_fingerprint(changed) != frontier_fingerprint(fingerprint_inputs)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    [
        ("forecast_model", "other-forecast"),
        ("forecast_version", "other-forecast-version"),
        ("policy_model", "other-policy"),
        ("policy_version", "other-policy-version"),
        ("repair_model", "weibull"),
        ("repair_version", "repair-v2"),
    ],
)
def test_each_model_artifact_field_changes_fingerprint(
    fingerprint_inputs: CandidateFingerprintInputs,
    field_name: str,
    replacement: str,
) -> None:
    identity_payload = fingerprint_inputs.model_identity.model_dump(mode="python")
    if field_name == "repair_model":
        identity_payload.update(repair_model=replacement, repair_version="repair-v1")
    elif field_name == "repair_version":
        identity_payload.update(repair_model="gamma", repair_version=replacement)
    else:
        identity_payload[field_name] = replacement
    changed = CandidateFingerprintInputs.model_validate(
        {
            **fingerprint_inputs.model_dump(mode="python"),
            "model_identity": identity_payload,
        }
    )
    assert frontier_fingerprint(changed) != frontier_fingerprint(fingerprint_inputs)


def test_key_membership_changes_fingerprint_without_false_deduplication(
    fingerprint_inputs: CandidateFingerprintInputs,
) -> None:
    changed = CandidateFingerprintInputs.model_validate(
        {
            **fingerprint_inputs.model_dump(mode="python"),
            "decision_key": "PN-2@MIA",
            "member_keys": ("PN-2@MIA",),
        }
    )
    assert frontier_fingerprint(changed) != frontier_fingerprint(fingerprint_inputs)


def test_candidate_id_and_output_digest_have_separate_stable_domains(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    planner = CandidatePlanner()
    fingerprint = planner.fingerprint(fingerprint_inputs)
    candidate = build_no_change_candidate(
        frontier_id=fingerprint,
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
        expected_aog_risk=Decimal("0.4"),
        confidence=Decimal("0.8"),
        constraints=(),
        evidence=source_evidence,
    )
    assert candidate.candidate_id == candidate_identifier(fingerprint, candidate)
    assert candidate_identifier("frontier_" + ("f" * 64), candidate) != candidate.candidate_id

    digest = output_digest(
        frontier_id=fingerprint,
        tenant_id="tenant-a",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        currency="USD",
        candidates=(candidate,),
        dominated_options_removed=0,
    )
    assert digest.startswith("output_")
    assert digest.removeprefix("output_") != fingerprint.removeprefix("frontier_")


def test_candidate_planner_version_is_locked() -> None:
    with pytest.raises(ValueError, match="unsupported CandidatePlanner version"):
        CandidatePlanner(version="candidate-planner-v2")


def test_planner_rejects_candidate_model_identity_outside_fingerprint(
    fingerprint_inputs,
    current_levels,
    economics,
    model_identity,
    source_evidence,
) -> None:
    planner = CandidatePlanner()
    fingerprint = planner.fingerprint(fingerprint_inputs)
    mislabeled = build_no_change_candidate(
        frontier_id=fingerprint,
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        decision_key="PN-1@MIA",
        member_keys=("PN-1@MIA",),
        model_identity=model_identity.model_copy(
            update={"forecast_model": "configured-but-not-served"}
        ),
        current_levels=current_levels,
        available_before=8,
        expected_receipts_before=0,
        projected_demand=10,
        economics=economics,
        expected_aog_risk=Decimal("0.4"),
        confidence=Decimal("0.8"),
        constraints=(),
        evidence=source_evidence,
    )

    with pytest.raises(ValueError, match="model identity"):
        planner.build_frontier(inputs=fingerprint_inputs, candidates=(mislabeled,))
