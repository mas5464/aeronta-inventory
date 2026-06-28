import math

from trax_io_forecasting.gradient_boosted import gb_forecast, gb_next_rate


def test_gb_forecast_returns_finite_mean_and_std_for_sufficient_history():
    vals = [float(x) for x in ([4, 5, 6, 5] * 6)]  # 24 periods
    fit = gb_forecast(vals)
    assert fit is not None
    mean, std = fit
    assert mean >= 0.0 and std >= 0.0
    assert math.isfinite(mean) and math.isfinite(std)


def test_gb_forecast_none_for_short_history():
    assert gb_forecast([1.0, 2.0, 3.0]) is None  # 3 - 6 < 8


def test_gb_forecast_is_deterministic():
    vals = [float(x % 7) for x in range(30)]
    assert gb_forecast(vals) == gb_forecast(vals)


def test_gb_next_rate_tracks_recent_level_on_ramp():
    ramp = [float(x) for x in range(1, 25)]  # 1..24 ascending
    assert gb_next_rate(ramp) > sum(ramp) / len(ramp)  # above the long-run average


def test_gb_next_rate_falls_back_to_mean_for_short_history():
    assert gb_next_rate([2.0, 4.0]) == 3.0
