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

_DAYS_PER_BUCKET = {"day": 1.0, "week": 7.0, "month": 30.44}
_DEFAULT_BASIS_DAYS = 730  # 24 months


class DemandProjectorProtocol(Protocol):
    def project(self, *, context: PartLocationContext, regime: Regime) -> DemandProjection: ...


class HistoricalScheduledProjector:
    """v1 deterministic projector. Pluggable; the ML forecaster (#5) swaps in later."""

    def __init__(self, *, basis_window_days: int = _DEFAULT_BASIS_DAYS) -> None:
        self._basis = basis_window_days

    def project(self, *, context: PartLocationContext, regime: Regime) -> DemandProjection:
        obs = context.demand_history.observations
        total_demand = float(sum(o.removals + o.issues for o in obs))
        historical_per_day = total_demand / self._basis

        # Scheduled (forward) demand as a per-day rate over the basis window + by-dimension.
        sched_total = float(sum(s.qty for s in context.scheduled_demand))
        scheduled_per_day = sched_total / self._basis
        by_aircraft: dict[str, float] = {}
        by_task: dict[str, float] = {}
        for s in context.scheduled_demand:
            if s.ac_type:
                by_aircraft[s.ac_type] = by_aircraft.get(s.ac_type, 0.0) + s.qty
            by_task[s.source_ref] = by_task.get(s.source_ref, 0.0) + s.qty

        mean_per_day = historical_per_day + scheduled_per_day

        if regime in (Regime.ULTRA_RARE, Regime.INTERMITTENT):
            dist_kind = "COMPOUND_POISSON"
            lam = historical_per_day  # single-unit Poisson arrivals/day
            dist_params = {"lambda": lam, "clump_p": 1.0}
            std_per_day = math.sqrt(lam)  # Poisson
        else:
            dist_kind = "NORMAL"
            # Convert each observation to a per-DAY rate honoring its bucket (day/week/month),
            # then take the variance of those rates as the per-day demand variance.
            daily_rates = [
                (o.removals + o.issues) / _DAYS_PER_BUCKET.get(o.bucket, 30.44) for o in obs
            ] or [0.0]
            r_mean = sum(daily_rates) / len(daily_rates)
            r_var = sum((x - r_mean) ** 2 for x in daily_rates) / max(1, len(daily_rates) - 1)
            var_per_day = max(historical_per_day, r_var)
            dist_params = {"mean": mean_per_day, "var": var_per_day}
            std_per_day = math.sqrt(var_per_day)

        return DemandProjection(
            mean_per_day=mean_per_day,
            std_per_day=std_per_day,
            dist_kind=dist_kind,  # type: ignore[arg-type]
            dist_params=dist_params,
            historical_component=historical_per_day,
            scheduled_component=scheduled_per_day,
            by_aircraft=by_aircraft,
            by_task=by_task,
            basis_window_days=self._basis,
        )
