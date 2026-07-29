"""StatisticalProjector — a DemandProjector that fits the intermittent regime with statsforecast.

Reuses #11's exact intermittent distribution machinery (COMPOUND_POISSON, single-unit Poisson),
replacing only the historical-average rate with a fitted Croston/SBA/TSB rate. Every other regime
delegates to the injected deterministic projector.
"""

from __future__ import annotations

import math
from importlib.metadata import version
from typing import TYPE_CHECKING

from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.basis import demand_basis_trace
from trax_io_reco.demand.projection import DemandProjectorProtocol, HistoricalScheduledProjector

from trax_io_forecasting.classical import ClassicalModel, forecast_rate, select_model
from trax_io_forecasting.series import to_period_series

CLASSICAL_PROJECTOR_VERSION = "classical-intermittent-v1"

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_reco.contracts.context import PartLocationContext

class StatisticalProjector:
    def __init__(
        self,
        fallback: DemandProjectorProtocol | None = None,
        *,
        model: ClassicalModel | None = None,
        basis_window_days: int | None = None,
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
        if len(series.values) < 2 or sum(series.values) <= 0.0:
            # No classical estimator runs on this path; preserve the fallback's
            # identity instead of claiming Croston/SBA/TSB served the result.
            return self._fallback.project(context=context, regime=regime)
        model = self._model or select_model(series.values)
        rate_per_period = forecast_rate(series.values, model)
        fitted_per_day = (
            rate_per_period / series.days_per_period
            if series.days_per_period
            else 0.0
        )

        trace = demand_basis_trace(context.demand_history)
        event_count = trace.demand_event_count
        clump_p = (
            min(1.0, event_count / trace.demanded_units)
            if event_count is not None and trace.demanded_units > 0
            else 1.0
        )
        fitted_event_rate = fitted_per_day * clump_p
        compound_variance = (
            fitted_event_rate * (2.0 - clump_p) / (clump_p**2)
            if clump_p > 0.0
            else fitted_per_day
        )
        basis_days = trace.exposure_days
        if (
            self._basis is not None
            and context.demand_history.observation_start is None
            and basis_days > 0
        ):
            basis_days = self._basis
        sched_total = float(sum(s.qty for s in context.scheduled_demand))
        by_aircraft: dict[str, float] = {}
        by_task: dict[str, float] = {}
        by_date: dict = {}
        for s in context.scheduled_demand:
            if s.ac_type:
                by_aircraft[s.ac_type] = by_aircraft.get(s.ac_type, 0.0) + s.qty
            by_task[s.source_ref] = by_task.get(s.source_ref, 0.0) + s.qty
            by_date[s.due_date] = by_date.get(s.due_date, 0.0) + s.qty

        return DemandProjection(
            mean_per_day=fitted_per_day,
            std_per_day=math.sqrt(max(0.0, compound_variance)),
            dist_kind="COMPOUND_POISSON",
            dist_params={"lambda": fitted_event_rate, "clump_p": clump_p},
            historical_component=fitted_per_day,
            scheduled_component=0.0,
            scheduled_demand_total=sched_total,
            scheduled_by_date=by_date,
            by_aircraft=by_aircraft,
            by_task=by_task,
            basis_window_days=basis_days,
            forecast_model=f"statsforecast-{model.value}",
            forecast_version=(
                f"{CLASSICAL_PROJECTOR_VERSION}+statsforecast-{version('statsforecast')}"
            ),
        )


__all__ = ["CLASSICAL_PROJECTOR_VERSION", "StatisticalProjector"]
