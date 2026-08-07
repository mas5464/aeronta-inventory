"""Age-conditioned, probabilistic repair-return projection.

The model accepts completed cycle durations when available and treats each
eligible open unit's current age as a right-censored observation. It falls back
to the independently sourced REP supply-cycle distribution without ever using
NEW procurement lead time. Aggregate or excluded WIP receives no return credit.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from trax_io_feature_store.schemas import LeadTimeDistribution

from trax_io_reco.contracts.repair import (
    RepairItemReturnProbability,
    RepairPipeline,
    RepairReturnEvidence,
    RepairReturnHorizon,
    RepairReturnProfile,
)

_MODEL_VERSION = "repair-return.v1"
_P10_Z = 1.2815515655446004
_P90_NORMAL_Z = 1.2815515655446004
_MIN_SURVIVAL = 1e-12


@dataclass(frozen=True)
class _SurvivalModel:
    method: str
    survival: Callable[[float], float]
    completed_observations: int
    source: str
    confidence: str
    data_cutoff: date | None
    model_version: str
    proxy_definition: str | None


def _clean_durations(values: Iterable[float]) -> tuple[float, ...]:
    cleaned: list[float] = []
    for value in values:
        parsed = float(value)
        if math.isfinite(parsed) and parsed >= 0:
            cleaned.append(parsed)
    return tuple(sorted(cleaned))


def _kaplan_meier(
    *,
    completed: Sequence[float],
    censored: Sequence[float],
) -> _SurvivalModel:
    event_times = tuple(completed)
    censor_times = tuple(max(0.0, value) for value in censored)
    timeline = tuple(sorted(set(event_times)))
    steps: list[tuple[float, float]] = []
    survival = 1.0
    for event_time in timeline:
        at_risk = sum(value >= event_time for value in event_times) + sum(
            value >= event_time for value in censor_times
        )
        events = sum(value == event_time for value in event_times)
        if at_risk:
            survival *= 1.0 - events / at_risk
        steps.append((event_time, max(0.0, min(1.0, survival))))

    def survival_at(day: float) -> float:
        result = 1.0
        for event_time, value in steps:
            if event_time > day:
                break
            result = value
        return result

    n = len(event_times)
    confidence = "high" if n >= 30 else ("medium" if n >= 10 else "low")
    return _SurvivalModel(
        method="kaplan_meier",
        survival=survival_at,
        completed_observations=n,
        source="completed_repair_cycles+open_work_right_censoring",
        confidence=confidence,
        data_cutoff=None,
        model_version=_MODEL_VERSION,
        proxy_definition="repair_induction_to_serviceable_completion",
    )


def _lognormal_from_quantiles(
    distribution: LeadTimeDistribution,
) -> _SurvivalModel | None:
    median = float(distribution.realized_p50_days or 0.0)
    p90 = float(distribution.realized_p90_days or 0.0)
    if median <= 0 or p90 <= median:
        return None
    mu = math.log(median)
    sigma = max((math.log(p90) - mu) / _P90_NORMAL_Z, 1e-6)

    def survival_at(day: float) -> float:
        if day <= 0:
            return 1.0
        z = (math.log(day) - mu) / (sigma * math.sqrt(2.0))
        cdf = 0.5 * (1.0 + math.erf(z))
        return max(0.0, min(1.0, 1.0 - cdf))

    return _SurvivalModel(
        method="lognormal_quantile",
        survival=survival_at,
        completed_observations=int(distribution.n_observations),
        source=distribution.source,
        confidence=distribution.confidence,
        data_cutoff=distribution.data_cutoff,
        model_version=f"{_MODEL_VERSION}+{distribution.model_version}",
        proxy_definition=distribution.proxy_definition,
    )


def _deterministic_promise(
    distribution: LeadTimeDistribution,
) -> _SurvivalModel | None:
    duration = float(distribution.promised_lead_days or 0.0)
    if duration <= 0:
        return None

    def survival_at(day: float) -> float:
        return 1.0 if day < duration else 0.0

    return _SurvivalModel(
        method="deterministic_promise",
        survival=survival_at,
        completed_observations=0,
        source=distribution.source,
        confidence="low",
        data_cutoff=distribution.data_cutoff,
        model_version=f"{_MODEL_VERSION}+{distribution.model_version}",
        proxy_definition=distribution.proxy_definition,
    )


def _unavailable_model() -> _SurvivalModel:
    return _SurvivalModel(
        method="unavailable",
        survival=lambda _day: 1.0,
        completed_observations=0,
        source="repair_cycle_evidence_unavailable",
        confidence="unavailable",
        data_cutoff=None,
        model_version=_MODEL_VERSION,
        proxy_definition=None,
    )


def _select_model(
    *,
    completed_cycle_days: Sequence[float],
    right_censored_ages: Sequence[float],
    repair_cycle_time: LeadTimeDistribution | None,
) -> _SurvivalModel:
    if completed_cycle_days:
        return _kaplan_meier(
            completed=completed_cycle_days,
            censored=right_censored_ages,
        )
    if (
        repair_cycle_time is not None
        and repair_cycle_time.condition == "REP"
        and repair_cycle_time.evidence_status != "legacy_unknown"
    ):
        return (
            _lognormal_from_quantiles(repair_cycle_time)
            or _deterministic_promise(repair_cycle_time)
            or _unavailable_model()
        )
    return _unavailable_model()


def _conditional_return_probability(
    survival: Callable[[float], float],
    *,
    age_days: float,
    horizon_days: int,
    tat_multiplier: float,
) -> float:
    if horizon_days <= 0:
        return 0.0
    at_age = survival(age_days)
    if at_age <= _MIN_SURVIVAL:
        return 0.0
    # Scenario changes stretch or compress *remaining* cycle time while the
    # already-observed age remains fixed. This preserves age conditioning and
    # guarantees that a slower TAT assumption cannot increase fixed-horizon
    # return probability for any survival-curve shape.
    effective_horizon = horizon_days / tat_multiplier
    after_horizon = survival(age_days + effective_horizon)
    probability = 1.0 - after_horizon / at_age
    return max(0.0, min(1.0, probability))


def project_repair_returns(
    *,
    pipeline: RepairPipeline,
    horizons: Sequence[int],
    completed_cycle_days: Sequence[float] = (),
    repair_cycle_time: LeadTimeDistribution | None = None,
    serviceable_yield: float = 1.0,
    tat_multiplier: float = 1.0,
) -> RepairReturnProfile:
    """Project age-conditioned serviceable repair receipts.

    ``tat_multiplier`` affects only the REP distribution. It never changes or
    reads procurement lead-time evidence. Every returned quantity is bounded by
    the Phase-5 eligible pipeline quantity and preserves all pipeline exclusions.
    """

    if not 0.0 <= serviceable_yield <= 1.0:
        raise ValueError("serviceable_yield must be between zero and one")
    if not math.isfinite(tat_multiplier) or tat_multiplier <= 0:
        raise ValueError("tat_multiplier must be finite and positive")
    normalized_horizons = tuple(sorted(set(int(value) for value in horizons)))
    if any(value < 0 for value in normalized_horizons):
        raise ValueError("repair return horizons must be non-negative")

    completed = _clean_durations(completed_cycle_days)
    censored = tuple(
        float(position.age_days)
        for position in pipeline.included
        for _ in range(position.eligible_quantity)
    )
    model = _select_model(
        completed_cycle_days=completed,
        right_censored_ages=censored,
        repair_cycle_time=repair_cycle_time,
    )

    horizon_results: list[RepairReturnHorizon] = []
    for horizon_days in normalized_horizons:
        items: list[RepairItemReturnProbability] = []
        variance = 0.0
        for position in pipeline.included:
            return_probability = _conditional_return_probability(
                model.survival,
                age_days=float(position.age_days),
                horizon_days=horizon_days,
                tat_multiplier=tat_multiplier,
            )
            serviceable_probability = return_probability * serviceable_yield
            expected = position.eligible_quantity * serviceable_probability
            variance += (
                position.eligible_quantity
                * serviceable_probability
                * (1.0 - serviceable_probability)
            )
            items.append(
                RepairItemReturnProbability(
                    repair_order_id=position.work_item.repair_order_id,
                    repair_line_id=position.work_item.repair_line_id,
                    serial_number=position.work_item.serial_number,
                    quantity=position.eligible_quantity,
                    age_days=position.age_days,
                    return_probability=return_probability,
                    serviceable_probability=serviceable_probability,
                    expected_serviceable_units=expected,
                )
            )
        expected_units = sum(item.expected_serviceable_units for item in items)
        spread = _P10_Z * math.sqrt(variance)
        eligible = pipeline.eligible_quantity
        horizon_results.append(
            RepairReturnHorizon(
                horizon_days=horizon_days,
                eligible_quantity=eligible,
                expected_units=expected_units,
                variance_units=variance,
                p10_units=max(0.0, expected_units - spread),
                p90_units=min(float(eligible), expected_units + spread),
                mean_serviceable_probability=(
                    expected_units / eligible if eligible else 0.0
                ),
                item_probabilities=tuple(items),
            )
        )

    evidence = RepairReturnEvidence(
        method=model.method,
        completed_observations=model.completed_observations,
        right_censored_observations=len(censored),
        serviceable_yield=serviceable_yield,
        tat_multiplier=tat_multiplier,
        source=model.source,
        confidence=model.confidence,
        data_cutoff=model.data_cutoff,
        model_version=model.model_version,
        proxy_definition=model.proxy_definition,
    )
    warnings = set(pipeline.warning_codes)
    if model.method == "unavailable":
        warnings.add("repair_return_evidence_unavailable")
    elif model.method == "deterministic_promise":
        warnings.add("repair_return_configured_promise")
    status = (
        "unavailable"
        if model.method == "unavailable"
        else ("partial" if warnings else "available")
    )
    return RepairReturnProfile(
        tenant_id=pipeline.tenant_id,
        part_number=pipeline.part_number,
        location_code=pipeline.location_code,
        as_of=pipeline.as_of,
        status=status,
        eligible_quantity=pipeline.eligible_quantity,
        excluded_quantity=(
            pipeline.excluded_identifiable_quantity
            + pipeline.unidentified_source_quantity
        ),
        aggregate_residual_quantity=pipeline.aggregate_residual_quantity,
        horizons=tuple(horizon_results),
        exclusions=pipeline.exclusions,
        evidence=evidence,
        warning_codes=tuple(sorted(warnings)),
    )
