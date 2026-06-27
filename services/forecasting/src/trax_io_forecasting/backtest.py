"""Hold-out backtest + MASE — does the fitted forecast beat the historical average out-of-sample?"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from trax_io_forecasting.classical import forecast_rate, select_model


def mase(actual: Sequence[float], forecast: float, *, naive_scale: float) -> float:
    mae = sum(abs(float(a) - forecast) for a in actual) / len(actual)
    if mae == 0.0:
        return 0.0  # perfect forecast: zero error regardless of scale
    if naive_scale <= 0.0:
        return math.inf
    return mae / naive_scale


def naive_scale(values: Sequence[float]) -> float:
    vals = [float(v) for v in values]
    if len(vals) < 2:
        return 0.0
    return sum(abs(vals[t] - vals[t - 1]) for t in range(1, len(vals))) / (len(vals) - 1)


def backtest_key(
    values: Sequence[float], rate_fn: Callable[[Sequence[float]], float], *, holdout: int
) -> float:
    vals = [float(v) for v in values]
    train, test = vals[:-holdout], vals[-holdout:]
    return mase(test, rate_fn(train), naive_scale=naive_scale(train))


@dataclass(frozen=True)
class BacktestReport:
    champion_mase: float
    challenger_mase: float
    n_keys: int
    champion_wins: bool


def _champion_rate(values: Sequence[float]) -> float:
    return forecast_rate(values, select_model(values))


def _challenger_rate(values: Sequence[float]) -> float:
    return sum(float(v) for v in values) / len(values) if values else 0.0


def compare(series_values: list[Sequence[float]], *, holdout: int = 6) -> BacktestReport:
    champ = [backtest_key(v, _champion_rate, holdout=holdout) for v in series_values]
    chal = [backtest_key(v, _challenger_rate, holdout=holdout) for v in series_values]
    finite_champ = [s for s in champ if math.isfinite(s)]
    finite_chal = [s for s in chal if math.isfinite(s)]
    champ_mean = sum(finite_champ) / len(finite_champ) if finite_champ else math.inf
    chal_mean = sum(finite_chal) / len(finite_chal) if finite_chal else math.inf
    return BacktestReport(
        champion_mase=champ_mean,
        challenger_mase=chal_mean,
        n_keys=len(series_values),
        champion_wins=champ_mean <= chal_mean,
    )
