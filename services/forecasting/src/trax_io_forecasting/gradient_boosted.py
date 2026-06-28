"""Gradient-boosted demand forecaster core (sklearn HistGradientBoostingRegressor)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from trax_io_forecasting.features import build_supervised, next_feature_row

_N_LAGS = 6
_MIN_TRAIN_ROWS = 8


def gb_forecast(
    values: Sequence[float],
    *,
    n_lags: int = _N_LAGS,
    min_train_rows: int = _MIN_TRAIN_ROWS,
    random_state: int = 0,
) -> tuple[float, float] | None:
    vals = [float(v) for v in values]
    if len(vals) - n_lags < min_train_rows:
        return None  # caller cold-starts to the deterministic projector

    from sklearn.ensemble import HistGradientBoostingRegressor  # lazy: keep import light

    x, y = build_supervised(vals, n_lags=n_lags)
    model = HistGradientBoostingRegressor(
        max_iter=200, max_depth=3, learning_rate=0.05,
        min_samples_leaf=1, random_state=random_state,
    )
    model.fit(x, y)
    pred = float(model.predict(next_feature_row(vals, n_lags=n_lags))[0])
    mean_per_period = max(0.0, pred)
    std_per_period = float(np.std(y - model.predict(x)))
    return mean_per_period, std_per_period


def gb_next_rate(
    values: Sequence[float],
    *,
    n_lags: int = _N_LAGS,
    min_train_rows: int = _MIN_TRAIN_ROWS,
    random_state: int = 0,
) -> float:
    fit = gb_forecast(
        values, n_lags=n_lags, min_train_rows=min_train_rows, random_state=random_state
    )
    if fit is not None:
        return fit[0]
    vals = [float(v) for v in values]
    return sum(vals) / len(vals) if vals else 0.0
