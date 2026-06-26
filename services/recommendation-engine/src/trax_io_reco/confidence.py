"""Deterministic confidence scoring (spec §7.9). Degrades when inputs come from
InMemoryInventoryState stubs / empty signals, so the min_confidence API filter is real."""

from __future__ import annotations

from trax_io_reco.contracts.enums import Regime

# Regime event-count midpoints used to gauge demand-history sufficiency.
_REGIME_SUFFICIENCY_TARGET = {
    Regime.ULTRA_RARE: 3,
    Regime.INTERMITTENT: 12,
    Regime.MODERATE: 60,
    Regime.HIGH_VOLUME: 200,
}
_STUB_PENALTY = 0.15
_CONSTRAINT_PENALTY = 0.85
_HYSTERESIS_PENALTY = 0.9


def confidence_score(
    *,
    events_24mo: int,
    regime: Regime,
    used_stub_inputs: set[str],
    constraint_bound: bool = False,
    within_hysteresis: bool = False,
) -> float:
    """Product of component scores, clamped to [0,1]."""
    target = _REGIME_SUFFICIENCY_TARGET[regime]
    sufficiency = min(1.0, events_24mo / target) if target else 1.0
    sufficiency = max(0.1, sufficiency)

    provenance = max(0.1, 1.0 - _STUB_PENALTY * len(used_stub_inputs))
    constraint = _CONSTRAINT_PENALTY if constraint_bound else 1.0
    regime_fit = _HYSTERESIS_PENALTY if within_hysteresis else 1.0

    return max(0.0, min(1.0, sufficiency * provenance * constraint * regime_fit))
