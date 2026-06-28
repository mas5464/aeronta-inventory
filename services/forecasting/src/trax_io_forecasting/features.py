"""Per-key autoregressive feature engineering for the gradient-boosted forecaster."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

_ROLLING_COLS = 3  # mean, std, max appended after the lags


def _row(window: list[float]) -> list[float]:
    lags = list(reversed(window))  # [v[t-1], v[t-2], ..., v[t-n_lags]]
    return [*lags, float(np.mean(window)), float(np.std(window)), float(np.max(window))]


def build_supervised(
    values: Sequence[float], *, n_lags: int = 6
) -> tuple[np.ndarray, np.ndarray]:
    vals = [float(v) for v in values]
    rows_x: list[list[float]] = []
    rows_y: list[float] = []
    for t in range(n_lags, len(vals)):
        rows_x.append(_row(vals[t - n_lags : t]))
        rows_y.append(vals[t])
    if not rows_x:
        return np.empty((0, n_lags + _ROLLING_COLS)), np.empty((0,))
    return np.asarray(rows_x, dtype=float), np.asarray(rows_y, dtype=float)


def next_feature_row(values: Sequence[float], *, n_lags: int = 6) -> np.ndarray:
    vals = [float(v) for v in values]
    return np.asarray([_row(vals[-n_lags:])], dtype=float)
