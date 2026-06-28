import math

from trax_io_forecasting.backtest import backtest_key
from trax_io_forecasting.gradient_boosted import gb_next_rate


def test_gb_next_rate_scores_through_the_holdout_backtest():
    vals = [float(x % 6) for x in range(30)]  # varied -> finite naive scale
    mase = backtest_key(vals, gb_next_rate, holdout=6)
    assert mase >= 0.0
    assert math.isfinite(mase)


def test_gb_champion_vs_historical_mean_challenger_runs():
    vals = [float(x % 5 + 1) for x in range(30)]
    champion = backtest_key(vals, gb_next_rate, holdout=6)
    challenger = backtest_key(vals, lambda v: sum(v) / len(v), holdout=6)
    assert math.isfinite(champion) and math.isfinite(challenger)
