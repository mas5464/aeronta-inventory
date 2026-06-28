"""GradientBoostedProjector — a DemandProjector for the MODERATE/HIGH_VOLUME regimes.

Returns the deterministic NORMAL projection (mirroring HistoricalScheduledProjector) with the
historical mean + variance replaced by a gradient-boosted next-period prediction + residual
variance. Every other regime, and any too-short history, delegates to the fallback projector.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.projection import DemandProjectorProtocol, HistoricalScheduledProjector

from trax_io_forecasting.gradient_boosted import gb_forecast
from trax_io_forecasting.series import to_period_series

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_reco.contracts.context import PartLocationContext

_DEFAULT_BASIS_DAYS = 730
_GB_REGIMES = (Regime.MODERATE, Regime.HIGH_VOLUME)


class GradientBoostedProjector:
    def __init__(
        self,
        fallback: DemandProjectorProtocol | None = None,
        *,
        n_lags: int = 6,
        min_train_rows: int = 8,
        random_state: int = 0,
        basis_window_days: int = _DEFAULT_BASIS_DAYS,
    ) -> None:
        self._fallback = fallback or HistoricalScheduledProjector(
            basis_window_days=basis_window_days
        )
        self._n_lags = n_lags
        self._min_train_rows = min_train_rows
        self._random_state = random_state
        self._basis = basis_window_days

    def project(self, *, context: PartLocationContext, regime: Regime) -> DemandProjection:
        if regime not in _GB_REGIMES:
            return self._fallback.project(context=context, regime=regime)

        series = to_period_series(context.demand_history)
        fit = gb_forecast(
            series.values, n_lags=self._n_lags,
            min_train_rows=self._min_train_rows, random_state=self._random_state,
        )
        if fit is None:  # cold-start: too little history to train
            return self._fallback.project(context=context, regime=regime)

        mean_per_period, std_per_period = fit
        dpp = series.days_per_period or 1.0
        gb_per_day = mean_per_period / dpp
        residual_var_per_day = (std_per_period / dpp) ** 2
        # same Poisson-ish floor as deterministic projector
        var_per_day = max(gb_per_day, residual_var_per_day)

        sched_total = float(sum(s.qty for s in context.scheduled_demand))
        scheduled_per_day = sched_total / self._basis
        by_aircraft: dict[str, float] = {}
        by_task: dict[str, float] = {}
        for s in context.scheduled_demand:
            if s.ac_type:
                by_aircraft[s.ac_type] = by_aircraft.get(s.ac_type, 0.0) + s.qty
            by_task[s.source_ref] = by_task.get(s.source_ref, 0.0) + s.qty

        mean_per_day = gb_per_day + scheduled_per_day
        return DemandProjection(
            mean_per_day=mean_per_day,
            std_per_day=math.sqrt(var_per_day),
            dist_kind="NORMAL",
            dist_params={"mean": mean_per_day, "var": var_per_day},
            historical_component=gb_per_day,
            scheduled_component=scheduled_per_day,
            by_aircraft=by_aircraft,
            by_task=by_task,
            basis_window_days=self._basis,
        )
