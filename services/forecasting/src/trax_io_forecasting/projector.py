"""StatisticalProjector — a DemandProjector that fits the intermittent regime with statsforecast.

Reuses #11's exact intermittent distribution machinery (COMPOUND_POISSON, single-unit Poisson),
replacing only the historical-average rate with a fitted Croston/SBA/TSB rate. Every other regime
delegates to the injected deterministic projector.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.projection import DemandProjectorProtocol, HistoricalScheduledProjector

from trax_io_forecasting.classical import ClassicalModel, forecast_rate, select_model
from trax_io_forecasting.series import to_period_series

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_reco.contracts.context import PartLocationContext

_DEFAULT_BASIS_DAYS = 730


class StatisticalProjector:
    def __init__(
        self,
        fallback: DemandProjectorProtocol | None = None,
        *,
        model: ClassicalModel | None = None,
        basis_window_days: int = _DEFAULT_BASIS_DAYS,
    ) -> None:
        self._fallback = fallback or HistoricalScheduledProjector(
            basis_window_days=basis_window_days
        )
        self._model = model
        self._basis = basis_window_days

    def project(self, *, context: PartLocationContext, regime: Regime) -> DemandProjection:
        if regime is not Regime.INTERMITTENT:
            return self._fallback.project(context=context, regime=regime)

        series = to_period_series(context.demand_history)
        model = self._model or select_model(series.values)
        rate_per_period = forecast_rate(series.values, model)
        fitted_per_day = rate_per_period / series.days_per_period if series.days_per_period else 0.0

        sched_total = float(sum(s.qty for s in context.scheduled_demand))
        scheduled_per_day = sched_total / self._basis
        by_aircraft: dict[str, float] = {}
        by_task: dict[str, float] = {}
        for s in context.scheduled_demand:
            if s.ac_type:
                by_aircraft[s.ac_type] = by_aircraft.get(s.ac_type, 0.0) + s.qty
            by_task[s.source_ref] = by_task.get(s.source_ref, 0.0) + s.qty

        return DemandProjection(
            mean_per_day=fitted_per_day + scheduled_per_day,
            std_per_day=math.sqrt(fitted_per_day),
            dist_kind="COMPOUND_POISSON",
            dist_params={"lambda": fitted_per_day, "clump_p": 1.0},
            historical_component=fitted_per_day,
            scheduled_component=scheduled_per_day,
            by_aircraft=by_aircraft,
            by_task=by_task,
            basis_window_days=self._basis,
        )
