from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from trax_io_reco.candidate.identity import frontier_fingerprint
from trax_io_reco.candidate.models import model_identity_from_served
from trax_io_reco.contracts.candidate import (
    CandidateFingerprintInputs,
    ModelIdentity,
    ServedForecastIdentity,
)
from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.contracts.enums import PolicyKind
from trax_io_reco.contracts.policy import PolicyRecommendation


def _projection(
    *,
    model: str | None = "statsforecast-sba",
    version: str | None = "classical-intermittent-v1",
) -> DemandProjection:
    return DemandProjection(
        mean_per_day=0.2,
        std_per_day=0.3,
        dist_kind="COMPOUND_POISSON",
        dist_params={"lambda": 0.1, "clump_p": 0.5},
        historical_component=0.2,
        scheduled_component=0.0,
        basis_window_days=731,
        forecast_model=model,
        forecast_version=version,
    )


def _policy(*, model_id: str = "deterministic-v1") -> PolicyRecommendation:
    return PolicyRecommendation(
        tenant_id="tenant-a",
        pn="PN-1",
        location="MIA",
        rop=2,
        eoq=1,
        safety_stock=1,
        max_stock=3,
        policy_kind=PolicyKind.S_S,
        provenance_id="policy-content-id",
        model_id=model_id,
    )


def test_model_identity_is_built_from_the_artifacts_that_actually_ran() -> None:
    identity = model_identity_from_served(
        decision_key="PN-1@MIA",
        projection=_projection(),
        policy=_policy(),
    )

    assert identity.forecast_model == "statsforecast-sba"
    assert identity.forecast_version == "classical-intermittent-v1"
    assert identity.policy_model == "s_S"
    assert identity.policy_version == "deterministic-v1"
    assert identity.member_forecasts == ()


def test_missing_forecast_identity_and_placeholder_policy_fail_closed() -> None:
    with pytest.raises(ValueError, match="does not disclose"):
        model_identity_from_served(
            decision_key="PN-1@MIA",
            projection=_projection(model=None, version=None),
            policy=_policy(),
        )

    with pytest.raises(ValueError, match="non-placeholder"):
        model_identity_from_served(
            decision_key="PN-1@MIA",
            projection=_projection(),
            policy=_policy(model_id="stub"),
        )


def test_projection_contract_requires_paired_identity_fields() -> None:
    with pytest.raises(ValidationError, match="supplied together"):
        _projection(version=None)


def test_pooled_identity_is_canonical_and_requires_every_member(
    fingerprint_inputs: CandidateFingerprintInputs,
) -> None:
    primary = _projection()
    other = _projection(
        model="gamma-poisson-empirical-bayes",
        version="gamma-poisson-eb-v1",
    )
    identity = model_identity_from_served(
        decision_key="PN-1@MIA",
        projection=primary,
        policy=_policy(),
        member_projections={
            "PN-2@MIA": other,
            "PN-1@MIA": primary,
        },
    )
    assert tuple(item.decision_key for item in identity.member_forecasts) == (
        "PN-1@MIA",
        "PN-2@MIA",
    )

    payload = fingerprint_inputs.model_dump(mode="python")
    payload.update(
        {
            "member_keys": ("PN-2@MIA", "PN-1@MIA"),
            "model_identity": identity,
        }
    )
    pooled = CandidateFingerprintInputs.model_validate(payload)
    assert pooled.member_keys == ("PN-1@MIA", "PN-2@MIA")

    incomplete_identity = identity.model_copy(
        update={"member_forecasts": identity.member_forecasts[:1]}
    )
    with pytest.raises(ValidationError, match="match member_keys exactly"):
        CandidateFingerprintInputs.model_validate(
            {
                **payload,
                "model_identity": incomplete_identity,
            }
        )

    mislabeled_primary = identity.model_copy(
        update={"forecast_model": "configured-but-not-served"}
    )
    with pytest.raises(ValidationError, match="primary forecast identity"):
        CandidateFingerprintInputs.model_validate(
            {
                **payload,
                "model_identity": mislabeled_primary,
            }
        )


def test_duplicate_members_are_rejected_instead_of_silently_deduplicated(
    fingerprint_inputs: CandidateFingerprintInputs,
) -> None:
    with pytest.raises(ValidationError, match="member_keys must be unique"):
        CandidateFingerprintInputs.model_validate(
            {
                **fingerprint_inputs.model_dump(mode="python"),
                "member_keys": ("PN-1@MIA", "PN-1@MIA"),
            }
        )


def test_member_forecast_contract_rejects_duplicate_keys() -> None:
    member = ServedForecastIdentity(
        decision_key="PN-1@MIA",
        forecast_model="statsforecast-sba",
        forecast_version="classical-intermittent-v1",
    )
    with pytest.raises(ValidationError, match="decision keys must be unique"):
        ModelIdentity.model_validate(
            {
                "forecast_model": "statsforecast-sba",
                "forecast_version": "classical-intermittent-v1",
                "policy_model": "s_S",
                "policy_version": "deterministic-v1",
                "member_forecasts": (member, member),
            }
        )


def test_fingerprint_date_is_not_mistaken_for_operational_timestamp(
    fingerprint_inputs: CandidateFingerprintInputs,
) -> None:
    changed = CandidateFingerprintInputs.model_validate(
        {
            **fingerprint_inputs.model_dump(mode="python"),
            "as_of": date(2026, 2, 1),
        }
    )
    assert frontier_fingerprint(changed) != frontier_fingerprint(fingerprint_inputs)
