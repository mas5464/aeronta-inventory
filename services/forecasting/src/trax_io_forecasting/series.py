"""Turn demand history into a dense, exposure-normalized per-period series."""

from __future__ import annotations

from dataclasses import dataclass

from trax_io_feature_store.schemas import DemandHistory
from trax_io_reco.demand.basis import historical_demand_stats

_DAYS_PER_BUCKET = {"day": 1.0, "week": 7.0, "month": 30.44}
_DEFAULT_BUCKET = "month"


@dataclass(frozen=True)
class PeriodSeries:
    values: tuple[float, ...]
    bucket: str
    days_per_period: float


def to_period_series(history: DemandHistory) -> PeriodSeries:
    stats = historical_demand_stats(history)
    bucket = stats.trace.bucket or _DEFAULT_BUCKET
    days_per_period = (
        stats.trace.exposure_days / len(stats.period_units)
        if stats.trace.exposure_days > 0 and stats.period_units
        else _DAYS_PER_BUCKET[bucket]
    )
    # Train on each period's daily rate normalized to one common period
    # duration. This keeps partial first/last buckets from looking like
    # anomalously low full periods while retaining explicit zero periods.
    values = tuple(rate * days_per_period for rate in stats.daily_rates)
    return PeriodSeries(
        values=values,
        bucket=bucket,
        days_per_period=days_per_period,
    )
