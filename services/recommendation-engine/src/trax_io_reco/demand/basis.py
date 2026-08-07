"""Pure demand-basis and requested-horizon calculations.

This module is intentionally free of service/store side effects so the recommendation
engine, scenario solver, and BFF can share one source of truth without replaying each
other's business math.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from trax_io_feature_store.schemas import DemandHistory, DemandObservation

from trax_io_reco.contracts.context import ScheduledDemandItem

DemandBucket = Literal["day", "week", "month"]
EventCountSource = Literal["observed", "bucket_fallback", "unavailable"]
ObservationWindowSource = Literal["configured", "observed_span", "unavailable"]


@dataclass(frozen=True)
class DemandBasisTrace:
    """Reader-facing evidence for the historical rate used by a decision."""

    observation_start: date | None
    observation_end: date | None
    observation_window_source: ObservationWindowSource
    exposure_days: int
    bucket: DemandBucket | None
    observed_periods: int
    zero_filled_periods: int
    demand_event_count: int | None
    event_count_source: EventCountSource
    demanded_units: int
    historical_per_day: float


@dataclass(frozen=True)
class HistoricalDemandStats:
    """Historical summary plus the exact zero-filled rate series used for dispersion."""

    trace: DemandBasisTrace
    period_units: tuple[float, ...]
    daily_rates: tuple[float, ...]
    variance_per_day: float


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _bucket_start(value: date, bucket: DemandBucket) -> date:
    if bucket == "month":
        return _month_start(value)
    if bucket == "week":
        return value - timedelta(days=value.weekday())
    return value


def _bucket_end(start: date, bucket: DemandBucket) -> date:
    if bucket == "month":
        return start.replace(day=calendar.monthrange(start.year, start.month)[1])
    if bucket == "week":
        return start + timedelta(days=6)
    return start


def _next_bucket(start: date, bucket: DemandBucket) -> date:
    if bucket == "month":
        if start.month == 12:
            return date(start.year + 1, 1, 1)
        return date(start.year, start.month + 1, 1)
    if bucket == "week":
        return start + timedelta(days=7)
    return start + timedelta(days=1)


def _history_bucket(history: DemandHistory) -> DemandBucket | None:
    if history.bucket is not None:
        return history.bucket
    buckets = {observation.bucket for observation in history.observations}
    if len(buckets) == 1:
        return buckets.pop()
    return None


def _window(
    history: DemandHistory,
    bucket: DemandBucket | None,
) -> tuple[date | None, date | None, ObservationWindowSource]:
    if history.observation_start is not None and history.observation_end is not None:
        return history.observation_start, history.observation_end, "configured"
    if not history.observations or bucket is None:
        return None, None, "unavailable"
    starts = [
        _bucket_start(observation.period_start, bucket) for observation in history.observations
    ]
    return min(starts), _bucket_end(max(starts), bucket), "observed_span"


def _period_starts(start: date, end: date, bucket: DemandBucket) -> tuple[date, ...]:
    current = _bucket_start(start, bucket)
    periods: list[date] = []
    while current <= end:
        if _bucket_end(current, bucket) >= start:
            periods.append(current)
        current = _next_bucket(current, bucket)
    return tuple(periods)


def _units_by_period(
    observations: Iterable[DemandObservation],
    *,
    bucket: DemandBucket,
    observation_start: date,
    observation_end: date,
) -> dict[date, int]:
    units: dict[date, int] = defaultdict(int)
    for observation in observations:
        if observation.bucket != bucket:
            raise ValueError("demand history mixes bucket granularities")
        period = _bucket_start(observation.period_start, bucket)
        if _bucket_end(period, bucket) < observation_start or period > observation_end:
            continue
        units[period] += int(observation.removals + observation.issues)
    return dict(units)


def _event_count(
    history: DemandHistory,
    observations: Iterable[DemandObservation],
) -> tuple[int | None, EventCountSource]:
    items = tuple(observations)
    if history.event_count_source == "observed":
        return (
            sum(
                int(observation.removal_events or 0) + int(observation.issue_events or 0)
                for observation in items
            ),
            "observed",
        )

    has_any_explicit = any(
        observation.removal_events is not None or observation.issue_events is not None
        for observation in items
    )
    if not has_any_explicit:
        if not items:
            return None, "unavailable"
        nonzero_buckets = {
            (observation.bucket, _bucket_start(observation.period_start, observation.bucket))
            for observation in items
            if observation.removals + observation.issues > 0
        }
        return len(nonzero_buckets), "bucket_fallback"

    total = 0
    used_fallback = False
    saw_explicit = False
    for observation in items:
        for units, events in (
            (observation.removals, observation.removal_events),
            (observation.issues, observation.issue_events),
        ):
            if events is not None:
                total += int(events)
                saw_explicit = True
            elif units > 0:
                total += 1
                used_fallback = True

    if used_fallback:
        return total, "bucket_fallback"
    if saw_explicit:
        return total, "observed"
    return 0, "bucket_fallback"


def demand_event_count(
    history: DemandHistory,
    *,
    observation_start: date | None = None,
    observation_end: date | None = None,
) -> tuple[int | None, EventCountSource]:
    """Count events, optionally inside a closed interval, with labeled legacy fallback.

    Bucketed observations are indivisible. A boundary bucket is included when it
    intersects the requested interval; day-grained Glue history therefore has exact
    boundaries while legacy monthly history is conservatively bucket-inclusive.
    """

    observations = _observations_in_window(
        history,
        observation_start=observation_start,
        observation_end=observation_end,
    )
    return _event_count(history, observations)


def _observations_in_window(
    history: DemandHistory,
    *,
    observation_start: date | None,
    observation_end: date | None,
) -> tuple[DemandObservation, ...]:
    if observation_start is None and observation_end is None:
        return tuple(history.observations)
    start = observation_start or date.min
    end = observation_end or date.max
    return tuple(
        observation
        for observation in history.observations
        if _bucket_end(
            _bucket_start(observation.period_start, observation.bucket),
            observation.bucket,
        )
        >= start
        and _bucket_start(observation.period_start, observation.bucket) <= end
    )


def demanded_units_in_window(
    history: DemandHistory,
    *,
    observation_start: date | None = None,
    observation_end: date | None = None,
) -> int:
    """Sum demanded units in the same bucket-inclusive closed interval as events."""

    return sum(
        int(observation.removals + observation.issues)
        for observation in _observations_in_window(
            history,
            observation_start=observation_start,
            observation_end=observation_end,
        )
    )


def historical_demand_stats(history: DemandHistory) -> HistoricalDemandStats:
    """Build the inclusive exposure, zero-filled series, and event/unit provenance."""

    if len({observation.bucket for observation in history.observations}) > 1:
        raise ValueError("demand history mixes bucket granularities")
    bucket = _history_bucket(history)
    observation_start, observation_end, window_source = _window(history, bucket)
    if observation_start is None or observation_end is None:
        event_count, event_source = demand_event_count(history)
        demanded_units = sum(
            int(observation.removals + observation.issues) for observation in history.observations
        )
        trace = DemandBasisTrace(
            observation_start=None,
            observation_end=None,
            observation_window_source=window_source,
            exposure_days=0,
            bucket=bucket,
            observed_periods=len(
                {
                    (observation.bucket, observation.period_start)
                    for observation in history.observations
                }
            ),
            zero_filled_periods=0,
            demand_event_count=event_count,
            event_count_source=event_source,
            demanded_units=demanded_units,
            historical_per_day=0.0,
        )
        return HistoricalDemandStats(
            trace=trace,
            period_units=(),
            daily_rates=(),
            variance_per_day=0.0,
        )

    exposure_days = (observation_end - observation_start).days + 1
    if bucket is None:
        raise ValueError("cannot calculate demand exposure for mixed or missing bucket")
    expected_periods = _period_starts(observation_start, observation_end, bucket)
    units_by_period = _units_by_period(
        history.observations,
        bucket=bucket,
        observation_start=observation_start,
        observation_end=observation_end,
    )
    # ``observed_periods`` means periods with observed positive demand. Native
    # zero-demand stock keys carry one persisted zero marker so the Iceberg row
    # retains the configured window; that marker (and ordinary zero buckets)
    # must still count as zero-filled rather than demand-observed.
    observed_periods = sum(1 for period in expected_periods if units_by_period.get(period, 0) > 0)
    demanded_units = sum(units_by_period.get(period, 0) for period in expected_periods)
    event_count, event_source = demand_event_count(
        history,
        observation_start=observation_start,
        observation_end=observation_end,
    )

    daily_rates: list[float] = []
    for period in expected_periods:
        period_start = max(period, observation_start)
        period_end = min(_bucket_end(period, bucket), observation_end)
        period_exposure = (period_end - period_start).days + 1
        daily_rates.append(units_by_period.get(period, 0) / period_exposure)

    rate_mean = sum(daily_rates) / len(daily_rates) if daily_rates else 0.0
    variance = (
        sum((value - rate_mean) ** 2 for value in daily_rates) / max(1, len(daily_rates) - 1)
        if daily_rates
        else 0.0
    )
    trace = DemandBasisTrace(
        observation_start=observation_start,
        observation_end=observation_end,
        observation_window_source=window_source,
        exposure_days=exposure_days,
        bucket=bucket,
        observed_periods=observed_periods,
        zero_filled_periods=max(0, len(expected_periods) - observed_periods),
        demand_event_count=event_count,
        event_count_source=event_source,
        demanded_units=demanded_units,
        historical_per_day=demanded_units / exposure_days if exposure_days else 0.0,
    )
    return HistoricalDemandStats(
        trace=trace,
        period_units=tuple(float(units_by_period.get(period, 0)) for period in expected_periods),
        daily_rates=tuple(daily_rates),
        variance_per_day=variance,
    )


def demand_basis_trace(history: DemandHistory) -> DemandBasisTrace:
    """Return only the compact evidence contract."""

    return historical_demand_stats(history).trace


def scheduled_items_in_horizon(
    items: Iterable[ScheduledDemandItem],
    *,
    as_of: date,
    horizon_days: int,
) -> tuple[ScheduledDemandItem, ...]:
    """Known demand due in the inclusive requested horizon."""

    if horizon_days < 0:
        raise ValueError("horizon_days must be non-negative")
    horizon_end = as_of + timedelta(days=horizon_days)
    return tuple(item for item in items if as_of <= item.due_date <= horizon_end)


def scheduled_units_in_horizon(
    items: Iterable[ScheduledDemandItem],
    *,
    as_of: date,
    horizon_days: int,
) -> int:
    """Total scheduled units due in the inclusive requested horizon."""

    return sum(
        int(item.qty)
        for item in scheduled_items_in_horizon(
            items,
            as_of=as_of,
            horizon_days=horizon_days,
        )
    )


def projected_demand_in_horizon(
    *,
    historical_per_day: float,
    scheduled_items: Iterable[ScheduledDemandItem],
    as_of: date,
    horizon_days: int,
) -> float:
    """Historical rate scaled to a horizon plus only scheduled units due in it."""

    if horizon_days < 0:
        raise ValueError("horizon_days must be non-negative")
    return historical_per_day * horizon_days + scheduled_units_in_horizon(
        scheduled_items,
        as_of=as_of,
        horizon_days=horizon_days,
    )


__all__ = [
    "DemandBasisTrace",
    "EventCountSource",
    "HistoricalDemandStats",
    "ObservationWindowSource",
    "demand_basis_trace",
    "demand_event_count",
    "demanded_units_in_window",
    "historical_demand_stats",
    "projected_demand_in_horizon",
    "scheduled_items_in_horizon",
    "scheduled_units_in_horizon",
]
