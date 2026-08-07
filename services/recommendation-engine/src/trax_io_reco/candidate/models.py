"""Truthful model identity adapters for candidate-frontier construction."""

from __future__ import annotations

from collections.abc import Mapping

from trax_io_reco.contracts.candidate import ModelIdentity, ServedForecastIdentity
from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.contracts.policy import PolicyRecommendation


def _forecast_identity(
    *,
    decision_key: str,
    projection: DemandProjection,
) -> ServedForecastIdentity:
    if projection.forecast_model is None or projection.forecast_version is None:
        raise ValueError(
            f"projection for {decision_key} does not disclose its served forecast identity"
        )
    return ServedForecastIdentity(
        decision_key=decision_key,
        forecast_model=projection.forecast_model,
        forecast_version=projection.forecast_version,
    )


def model_identity_from_served(
    *,
    decision_key: str,
    projection: DemandProjection,
    policy: PolicyRecommendation,
    member_projections: Mapping[str, DemandProjection] | None = None,
    repair_model: str | None = None,
    repair_version: str | None = None,
) -> ModelIdentity:
    """Build candidate identity from artifacts that actually produced the result.

    ``member_projections`` is required by the candidate contracts for pooled
    decisions and must contain every served member.  It remains optional for a
    single-key preview.  A legacy policy carrying the placeholder ``"stub"``
    identifier is deliberately rejected instead of being presented as a real model.
    """

    primary = _forecast_identity(decision_key=decision_key, projection=projection)
    if policy.model_id.strip().lower() == "stub":
        raise ValueError("candidate planning requires a non-placeholder served policy model")

    member_forecasts = tuple(
        _forecast_identity(decision_key=member_key, projection=member_projection)
        for member_key, member_projection in sorted((member_projections or {}).items())
    )
    if member_projections is not None:
        matches = [
            identity
            for identity in member_forecasts
            if identity.decision_key == decision_key
        ]
        if len(matches) != 1:
            raise ValueError("member_projections must contain the decision key exactly once")
        if (
            matches[0].forecast_model != primary.forecast_model
            or matches[0].forecast_version != primary.forecast_version
        ):
            raise ValueError("decision projection does not match its pooled member projection")

    return ModelIdentity(
        forecast_model=primary.forecast_model,
        forecast_version=primary.forecast_version,
        policy_model=policy.policy_kind.value,
        policy_version=policy.model_id,
        repair_model=repair_model,
        repair_version=repair_version,
        member_forecasts=member_forecasts,
    )


__all__ = ["model_identity_from_served"]
