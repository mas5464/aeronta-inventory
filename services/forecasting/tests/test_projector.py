from datetime import date

import pytest
from trax_io_feature_store.schemas import DemandObservation
from trax_io_reco.contracts.enums import Regime
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
    expected_lambda = expected_rate / series.days_per_period

    assert proj.dist_kind == "COMPOUND_POISSON"
    assert proj.dist_params["lambda"] == pytest.approx(expected_lambda)
    assert proj.dist_params["clump_p"] == 1.0


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
