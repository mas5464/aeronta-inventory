"""Deterministic demand projection (spec §5.3 / §6.4).

Produces a per-DAY demand rate plus a parameterized distribution so the policy engine
can invert P(LTD > S) for every regime. No ML; method-of-moments fits only. Historical
intensity + known scheduled (forward) demand, itemized by aircraft and task.
"""

from __future__ import annotations

import math
from typing import Protocol

from trax_io_reco.contracts.context import DemandProjection, PartLocationContext
from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.basis import historical_demand_stats

HISTORICAL_PROJECTOR_VERSION = "historical-scheduled-v1"
_HISTORICAL_MODEL_BY_REGIME = {
    Regime.ULTRA_RARE: "historical-compound-poisson",
    Regime.INTERMITTENT: "historical-compound-poisson",
    Regime.MODERATE: "historical-normal-moments",
    Regime.HIGH_VOLUME: "historical-normal-moments",
}


class DemandProjectorProtocol(Protocol):
    def project(self, *, context: PartLocationContext, regime: Regime) -> DemandProjection: ...


class HistoricalScheduledProjector:
    """v1 deterministic projector. Pluggable; the ML forecaster (#5) swaps in later."""

    def __init__(self, *, basis_window_days: int | None = None) -> None:
        """Build a projector.

        ``basis_window_days`` remains as an explicit legacy-history override for
        callers that intentionally supplied one. Persisted configured windows are
        authoritative and never replaced by the override.
        """
        self._basis = basis_window_days

    def project(self, *, context: PartLocationContext, regime: Regime) -> DemandProjection:
        stats = historical_demand_stats(context.demand_history)
        trace = stats.trace
        basis_days = trace.exposure_days
        historical_per_day = trace.historical_per_day
        if (
            context.demand_history.observation_start is None
            and self._basis is not None
            and self._basis > 0
        ):
            # Compatibility for callers that explicitly configured a legacy basis.
            basis_days = self._basis
            historical_per_day = trace.demanded_units / self._basis

        # Scheduled demand stays itemized/datetime-bound and is included only by
        # requested-horizon consumers (never spread across the historical basis).
        sched_total = float(sum(s.qty for s in context.scheduled_demand))
        by_aircraft: dict[str, float] = {}
        by_task: dict[str, float] = {}
        by_date: dict = {}
        for s in context.scheduled_demand:
            if s.ac_type:
                by_aircraft[s.ac_type] = by_aircraft.get(s.ac_type, 0.0) + s.qty
            by_task[s.source_ref] = by_task.get(s.source_ref, 0.0) + s.qty
            by_date[s.due_date] = by_date.get(s.due_date, 0.0) + s.qty

        mean_per_day = historical_per_day

        if regime in (Regime.ULTRA_RARE, Regime.INTERMITTENT):
            dist_kind = "COMPOUND_POISSON"
            event_count = trace.demand_event_count
            lam = (
                event_count / basis_days
                if event_count is not None and basis_days > 0
                else historical_per_day
            )
            clump_p = (
                min(1.0, event_count / trace.demanded_units)
                if event_count is not None and trace.demanded_units > 0
                else 1.0
            )
            dist_params = {"lambda": lam, "clump_p": clump_p}
            std_per_day = math.sqrt(max(historical_per_day, lam))
        else:
            dist_kind = "NORMAL"
            var_per_day = max(historical_per_day, stats.variance_per_day)
            dist_params = {"mean": mean_per_day, "var": var_per_day}
            std_per_day = math.sqrt(var_per_day)

        return DemandProjection(
            mean_per_day=mean_per_day,
            std_per_day=std_per_day,
            dist_kind=dist_kind,  # type: ignore[arg-type]
            dist_params=dist_params,
            historical_component=historical_per_day,
            scheduled_component=0.0,
            scheduled_demand_total=sched_total,
            scheduled_by_date=by_date,
            by_aircraft=by_aircraft,
            by_task=by_task,
            basis_window_days=basis_days,
            forecast_model=_HISTORICAL_MODEL_BY_REGIME[regime],
            forecast_version=HISTORICAL_PROJECTOR_VERSION,
        )


__all__ = [
    "DemandProjectorProtocol",
    "HISTORICAL_PROJECTOR_VERSION",
    "HistoricalScheduledProjector",
]
