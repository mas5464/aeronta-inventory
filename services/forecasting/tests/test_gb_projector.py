from datetime import date

from trax_io_feature_store.schemas import DemandObservation
from trax_io_reco.contracts.context import ScheduledDemandItem
from trax_io_reco.contracts.enums import EvidenceKind, Regime
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
    assert proj.forecast_model == "hist-gradient-boosting"
    assert proj.forecast_version.startswith(
        "hist-gradient-boosting-v1+scikit-learn-"
    )


def test_tracks_recent_level_above_average_on_ramp(sample_context):
    ctx = with_demand(sample_context, _monthly(list(range(1, 25))))  # 1..24 ascending
    gb = GradientBoostedProjector().project(context=ctx, regime=Regime.MODERATE)
    det = HistoricalScheduledProjector().project(context=ctx, regime=Regime.MODERATE)
    assert gb.historical_component > det.historical_component


def test_gb_keeps_scheduled_demand_out_of_daily_rate(sample_context):
    ctx = with_demand(sample_context, _monthly([4, 5, 6, 5] * 6))
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
                    due_date=date(2026, 1, 1),
                    qty=8,
                    source_ref="GB-SCHEDULE",
                    source_kind=EvidenceKind.TASK_CARD,
                ),
            ),
        }
    )

    projection = GradientBoostedProjector().project(
        context=ctx,
        regime=Regime.MODERATE,
    )

    assert projection.basis_window_days == 731
    assert projection.mean_per_day == projection.historical_component
    assert projection.scheduled_component == 0.0
    assert projection.scheduled_demand_total == 8.0
