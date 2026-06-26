"""Service-level math primitives (spec §6.4). Bugs here cascade silently into wrong
stock levels, so every function is small and unit-tested against textbook values.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from scipy.stats import norm


def round_half_up(x: float) -> int:
    """Deterministic round-half-up for non-negative quantities. Avoids Python's
    round() banker's rounding (round(0.5)==0, round(2.5)==2) so integer policy levels
    are stable across platforms (spec §7.9 determinism)."""
    return int(math.floor(x + 0.5))


def z_for_fill_rate(fill_rate: float) -> float:
    """z-score for a cycle-service-level target."""
    if not 0.0 < fill_rate < 1.0:
        raise ValueError(f"fill_rate must be in (0,1), got {fill_rate}")
    return float(norm.ppf(fill_rate))


def safety_stock_normal(*, sigma_ltd: float, service_level: float) -> float:
    """Normal-approximation safety stock = z * sigma_LTD."""
    return z_for_fill_rate(service_level) * sigma_ltd


def ltd_quantile_from_pmf(pmf: Sequence[float], p: float) -> int:
    """Smallest integer S such that the cumulative PMF mass at S is >= p.

    Used for base-stock S (P(LTD > S) <= 1 - target) and intermittent safety stock.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0,1), got {p}")
    cumulative = 0.0
    for s, mass in enumerate(pmf):
        cumulative += mass
        if cumulative >= p:
            return s
    return max(0, len(pmf) - 1)
