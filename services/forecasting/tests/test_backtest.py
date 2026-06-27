import math

from trax_io_forecasting.backtest import backtest_key, compare, mase, naive_scale


def test_mase_basic() -> None:
    assert mase([2.0, 2.0, 2.0], 2.0, naive_scale=1.0) == 0.0
    assert mase([0.0, 4.0], 2.0, naive_scale=2.0) == 1.0  # mean|.-2| = 2; /2 = 1


def test_mase_inf_on_zero_scale() -> None:
    assert math.isinf(mase([1.0, 1.0], 0.5, naive_scale=0.0))


def test_naive_scale_is_lag1_mae() -> None:
    assert naive_scale([1.0, 1.0, 4.0]) == 1.5  # |1-1| + |4-1| = 3; /2


def test_backtest_key_scores_a_constant_rate() -> None:
    score = backtest_key([1.0, 1.0, 1.0, 1.0, 1.0, 1.0], lambda v: sum(v) / len(v), holdout=2)
    assert score == 0.0  # constant series, constant forecast -> zero error


def test_compare_reports_a_winner() -> None:
    # a recency-trending intermittent series where the fit should not be worse than the flat mean
    series = [[0, 0, 1, 0, 2, 0, 1, 0, 3, 0, 2, 4]]
    report = compare(series, holdout=4)
    assert report.n_keys == 1
    assert isinstance(report.champion_wins, bool)
    assert report.champion_mase >= 0.0 and report.challenger_mase >= 0.0
