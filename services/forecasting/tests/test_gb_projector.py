from datetime import date

from trax_io_feature_store.schemas import DemandObservation
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.projection import HistoricalScheduledProjector

from trax_io_forecasting.gb_projector import GradientBoostedProjector

from .conftest import with_demand


def _monthly(values, start_year=2024):
    obs = []
    for i, v in enumerate(values):
        obs.append(
            DemandObservation(
                bucket="month", period_start=date(start_year + i // 12, i % 12 + 1, 1),
                removals=int(v), issues=0,
            )
        )
    return obs


def test_delegates_non_target_regimes(sample_context):
    proj, fb = GradientBoostedProjector(), HistoricalScheduledProjector()
    for regime in (Regime.ULTRA_RARE, Regime.INTERMITTENT):
        assert proj.project(context=sample_context, regime=regime) == fb.project(
            context=sample_context, regime=regime
        )


def test_cold_start_moderate_delegates_to_fallback(sample_context):
    ctx = with_demand(sample_context, _monthly([5, 6, 7]))  # too short to train
    proj, fb = GradientBoostedProjector(), HistoricalScheduledProjector()
    assert proj.project(context=ctx, regime=Regime.MODERATE) == fb.project(
        context=ctx, regime=Regime.MODERATE
    )


def test_moderate_returns_a_normal_projection(sample_context):
    ctx = with_demand(sample_context, _monthly([4, 5, 6, 5] * 6))  # 24 periods
    proj = GradientBoostedProjector().project(context=ctx, regime=Regime.MODERATE)
    assert proj.dist_kind == "NORMAL"
    assert proj.mean_per_day > 0.0
    assert proj.std_per_day > 0.0
    assert set(proj.dist_params) == {"mean", "var"}
    assert proj.historical_component > 0.0


def test_tracks_recent_level_above_average_on_ramp(sample_context):
    ctx = with_demand(sample_context, _monthly(list(range(1, 25))))  # 1..24 ascending
    gb = GradientBoostedProjector().project(context=ctx, regime=Regime.MODERATE)
    det = HistoricalScheduledProjector().project(context=ctx, regime=Regime.MODERATE)
    assert gb.historical_component > det.historical_component
