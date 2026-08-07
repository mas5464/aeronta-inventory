"""Deterministic regime classifier (spec §6.1). Replaces the Regime Router (#4) for
the deterministic v1 path: 24-month event-count thresholds + ±20% hysteresis.
"""

from __future__ import annotations

import calendar
from datetime import date

from trax_io_feature_store.schemas import DemandHistory

from trax_io_reco.contracts.enums import Regime
from trax_io_reco.demand.basis import (
    demand_basis_trace,
    demand_event_count,
    demanded_units_in_window,
)

_ORDER = [Regime.ULTRA_RARE, Regime.INTERMITTENT, Regime.MODERATE, Regime.HIGH_VOLUME]
# Boundary between adjacent regimes (the lower edge of the upper regime).
_BOUNDARIES = {
    (Regime.ULTRA_RARE, Regime.INTERMITTENT): 6,
    (Regime.INTERMITTENT, Regime.MODERATE): 25,
    (Regime.MODERATE, Regime.HIGH_VOLUME): 201,
}
_HYSTERESIS = 0.20


def events_24mo_from(history: DemandHistory) -> int:
    """Demand events in the trailing 24-month closed interval.

    The interval is anchored at the persisted observation end, falling back to
    ``extract_date`` for legacy history. Monthly boundary buckets are included when
    they intersect the interval because their within-month source dates are no longer
    available; day-grained history is exact.
    """

    # Configured windows are authoritative. Legacy histories anchor to their
    # derived observed span so reprocessing identical bytes on a later date
    # cannot silently change the regime.
    end = demand_basis_trace(history).observation_end or history.extract_date
    start = _subtract_months(end, 24)
    count, _source = demand_event_count(
        history,
        observation_start=start,
        observation_end=end,
    )
    return count or 0


def demanded_units_24mo_from(history: DemandHistory) -> int:
    """Demanded units in the same trailing closed interval used for regime events."""

    end = demand_basis_trace(history).observation_end or history.extract_date
    start = _subtract_months(end, 24)
    return demanded_units_in_window(
        history,
        observation_start=start,
        observation_end=end,
    )


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + (value.month - 1) - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _raw_regime(events_24mo: int) -> Regime:
    if events_24mo < 6:
        return Regime.ULTRA_RARE
    if events_24mo <= 24:
        return Regime.INTERMITTENT
    if events_24mo <= 200:
        return Regime.MODERATE
    return Regime.HIGH_VOLUME


def classify(*, events_24mo: int, history_days: int, prior: Regime | None = None) -> Regime:
    """Classify a (PN, Location). New PNs (<90d history) are ULTRA_RARE. With a prior
    regime supplied, a ±20% hysteresis band around the crossed boundary keeps the prior."""
    if history_days < 90:
        return Regime.ULTRA_RARE
    raw = _raw_regime(events_24mo)
    if prior is None or prior == raw:
        return raw
    i_raw, i_prior = _ORDER.index(raw), _ORDER.index(prior)
    if abs(i_raw - i_prior) == 1:
        key = (raw, prior) if i_raw < i_prior else (prior, raw)
        boundary = _BOUNDARIES[key]
        if boundary * (1 - _HYSTERESIS) <= events_24mo <= boundary * (1 + _HYSTERESIS):
            return prior
    return raw
