import numpy as np

from trax_io_forecasting.features import build_supervised, next_feature_row


def test_build_supervised_shapes_and_lag_order():
    X, y = build_supervised([float(x) for x in range(10)], n_lags=3)  # noqa: N806
    assert X.shape == (7, 6)  # 10-3 rows; 3 lags + mean/std/max
    assert y.shape == (7,)
    assert list(X[0][:3]) == [2.0, 1.0, 0.0]  # most-recent-first lags before t=3
    assert y[0] == 3.0
    # rolling block on window [0,1,2]: mean=1, max=2
    assert X[0][3] == 1.0
    assert X[0][5] == 2.0


def test_build_supervised_too_short_is_empty_with_right_width():
    X, y = build_supervised([1.0, 2.0], n_lags=6)  # noqa: N806
    assert X.shape == (0, 9)
    assert y.shape == (0,)


def test_next_feature_row():
    row = next_feature_row([float(x) for x in range(8)], n_lags=3)  # [0..7]
    assert row.shape == (1, 6)
    assert list(row[0][:3]) == [7.0, 6.0, 5.0]  # most-recent-first
    assert np.isfinite(row).all()
