"""Deterministic regime classifier (spec §6.1). Replaces the Regime Router (#4) for
the deterministic v1 path: 24-month event-count thresholds + ±20% hysteresis.
"""

from __future__ import annotations

from trax_io_feature_store.schemas import DemandHistory

from trax_io_reco.contracts.enums import Regime

_ORDER = [Regime.ULTRA_RARE, Regime.INTERMITTENT, Regime.MODERATE, Regime.HIGH_VOLUME]
# Boundary between adjacent regimes (the lower edge of the upper regime).
_BOUNDARIES = {
    (Regime.ULTRA_RARE, Regime.INTERMITTENT): 6,
    (Regime.INTERMITTENT, Regime.MODERATE): 25,
    (Regime.MODERATE, Regime.HIGH_VOLUME): 201,
}
_HYSTERESIS = 0.20


def events_24mo_from(history: DemandHistory) -> int:
    """Total demand events (removals + issues) over the demand history."""
    return sum(o.removals + o.issues for o in history.observations)


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
