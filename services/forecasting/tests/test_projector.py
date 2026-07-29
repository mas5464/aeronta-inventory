from datetime import date

import pytest
from trax_io_feature_store.schemas import DemandHistory, DemandObservation
from trax_io_reco.contracts.context import ScheduledDemandItem
from trax_io_reco.contracts.enums import EvidenceKind, Regime
from trax_io_reco.demand.projection import HistoricalScheduledProjector

from tests.conftest import with_demand
from trax_io_forecasting.classical import forecast_rate, select_model
from trax_io_forecasting.projector import StatisticalProjector
from trax_io_forecasting.series import to_period_series


def _intermittent_obs() -> list[DemandObservation]:
    # 24 months, intermittent + recency-trending (so the fit differs from the flat average).
    counts = [0] * 18 + [1, 2, 1, 3, 2, 4]
    return [
        DemandObservation(bucket="month", period_start=date(2024, 1 + (i % 12), 1)
                          if i < 12 else date(2025, 1 + (i - 12), 1), removals=c)
        for i, c in enumerate(counts)
    ]


def test_intermittent_uses_fitted_lambda(sample_context) -> None:
    ctx = with_demand(sample_context, _intermittent_obs())
    proj = StatisticalProjector().project(context=ctx, regime=Regime.INTERMITTENT)

    series = to_period_series(ctx.demand_history)
    expected_rate = forecast_rate(series.values, select_model(series.values))
    expected_units_per_day = expected_rate / series.days_per_period
    # Legacy event fallback: six non-zero buckets represent six events and
    # thirteen demanded units.
    expected_clump_p = 6 / 13
    expected_lambda = expected_units_per_day * expected_clump_p

    assert proj.dist_kind == "COMPOUND_POISSON"
    assert proj.dist_params["lambda"] == pytest.approx(expected_lambda)
    assert proj.dist_params["clump_p"] == pytest.approx(expected_clump_p)
    assert proj.mean_per_day == pytest.approx(expected_units_per_day)
    assert proj.forecast_model == f"statsforecast-{select_model(series.values).value}"
    assert proj.forecast_version.startswith(
        "classical-intermittent-v1+statsforecast-"
    )


def test_fitted_lambda_differs_from_deterministic_average(sample_context) -> None:
    ctx = with_demand(sample_context, _intermittent_obs())
    fitted = StatisticalProjector().project(context=ctx, regime=Regime.INTERMITTENT)
    deterministic = HistoricalScheduledProjector().project(context=ctx, regime=Regime.INTERMITTENT)
    assert fitted.dist_params["lambda"] != deterministic.dist_params["lambda"]


def test_non_intermittent_delegates_to_fallback(sample_context) -> None:
    ctx = with_demand(sample_context, _intermittent_obs())
    fitted = StatisticalProjector().project(context=ctx, regime=Regime.MODERATE)
    deterministic = HistoricalScheduledProjector().project(context=ctx, regime=Regime.MODERATE)
    assert fitted == deterministic


def test_empty_intermittent_history_discloses_the_actual_fallback_model(
    sample_context,
) -> None:
    context = with_demand(sample_context, [])
    projection = StatisticalProjector().project(
        context=context,
        regime=Regime.INTERMITTENT,
    )
    assert projection.forecast_model == "historical-compound-poisson"
    assert projection.forecast_version == "historical-scheduled-v1"


def test_statistical_projection_uses_actual_basis_and_keeps_schedule_discrete(
    sample_context,
) -> None:
    ctx = with_demand(sample_context, _intermittent_obs())
    history = ctx.demand_history.model_copy(
        update={
            "observation_start": date(2024, 1, 1),
            "observation_end": date(2025, 12, 31),
            "bucket": "month",
        }
    )
    ctx = ctx.model_copy(
        update={
            "demand_history": history,
            "scheduled_demand": (
                ScheduledDemandItem(
                    due_date=date(2026, 1, 31),
                    qty=5,
                    source_ref="TC-1",
                    source_kind=EvidenceKind.TASK_CARD,
                ),
            ),
        }
    )

    projection = StatisticalProjector().project(
        context=ctx,
        regime=Regime.INTERMITTENT,
    )

    assert projection.basis_window_days == 731
    assert projection.mean_per_day == projection.historical_component
    assert projection.scheduled_component == 0.0
    assert projection.scheduled_demand_total == 5.0


def test_statistical_projection_preserves_event_count_and_unit_clumps(
    sample_context,
) -> None:
    observations = tuple(
        observation.model_copy(
            update={
                "removal_events": 1 if observation.removals > 0 else 0,
                "issue_events": 0,
            }
        )
        for observation in _intermittent_obs()
    )
    history = DemandHistory(
        tenant_id=sample_context.tenant_id,
        pn=sample_context.pn,
        location=sample_context.location,
        observation_start=date(2024, 1, 1),
        observation_end=date(2025, 12, 31),
        bucket="month",
        event_count_source="observed",
        observations=observations,
        extract_date=sample_context.demand_history.extract_date,
    )
    context = sample_context.model_copy(update={"demand_history": history})
    series = to_period_series(history)
    fitted_units_per_day = (
        forecast_rate(series.values, select_model(series.values))
        / series.days_per_period
    )
    event_to_unit_ratio = 6 / 13

    projection = StatisticalProjector().project(
        context=context,
        regime=Regime.INTERMITTENT,
    )

    assert projection.mean_per_day == pytest.approx(fitted_units_per_day)
    assert projection.dist_params["clump_p"] == pytest.approx(event_to_unit_ratio)
    assert projection.dist_params["lambda"] == pytest.approx(
        fitted_units_per_day * event_to_unit_ratio
    )
