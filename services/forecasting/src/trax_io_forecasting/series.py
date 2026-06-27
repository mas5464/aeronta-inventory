"""Turn a bucketed DemandHistory into a dense, gap-filled per-period demand series."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from trax_io_feature_store.schemas import DemandHistory

_DAYS_PER_BUCKET = {"day": 1.0, "week": 7.0, "month": 30.44}
_DEFAULT_BUCKET = "month"


@dataclass(frozen=True)
class PeriodSeries:
    values: tuple[float, ...]
    bucket: str
    days_per_period: float


def _periods_between(bucket: str, start: date, end: date) -> int:
    if bucket == "month":
        return (end.year - start.year) * 12 + (end.month - start.month)
    return (end - start).days // int(_DAYS_PER_BUCKET[bucket])


def to_period_series(history: DemandHistory) -> PeriodSeries:
    obs = sorted(history.observations, key=lambda o: o.period_start)
    if not obs:
        return PeriodSeries(values=(), bucket=_DEFAULT_BUCKET,
                            days_per_period=_DAYS_PER_BUCKET[_DEFAULT_BUCKET])
    bucket = obs[0].bucket
    first = obs[0].period_start
    span = _periods_between(bucket, first, obs[-1].period_start) + 1
    dense = [0.0] * span
    for o in obs:
        idx = _periods_between(bucket, first, o.period_start)
        dense[idx] += float(o.removals + o.issues)
    return PeriodSeries(
        values=tuple(dense), bucket=bucket, days_per_period=_DAYS_PER_BUCKET[bucket]
    )
