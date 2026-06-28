"""Gamma-Poisson empirical-Bayes primitives (closed form, deterministic)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

_EPS = 1e-9


@dataclass(frozen=True)
class GammaPrior:
    """Conjugate prior for a Poisson rate: shape alpha, rate beta (mean = alpha/beta)."""

    alpha: float
    beta: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.alpha) or self.alpha <= 0:
            raise ValueError(f"alpha must be positive and finite, got {self.alpha}")
        if not math.isfinite(self.beta) or self.beta <= 0:
            raise ValueError(f"beta must be positive and finite, got {self.beta}")

    @property
    def mean(self) -> float:
        return self.alpha / self.beta


def posterior_rate(prior: GammaPrior, count: float, exposure: float) -> float:
    """Posterior mean daily rate after observing `count` events over `exposure` time."""
    if not math.isfinite(count):
        raise ValueError(f"count must be finite, got {count}")
    if not math.isfinite(exposure):
        raise ValueError(f"exposure must be finite, got {exposure}")
    return (prior.alpha + count) / (prior.beta + exposure)


def posterior_predictive_var(prior: GammaPrior, count: float, exposure: float) -> float:
    """Per-unit posterior-predictive variance (negative-binomial): >= Poisson var (= mean)."""
    lam = posterior_rate(prior, count, exposure)
    return lam * (1.0 + 1.0 / (prior.beta + exposure))


def fit_prior(rates: Sequence[float], exposures: Sequence[float]) -> GammaPrior:
    """Method-of-moments Gamma prior from peer per-unit rates + exposures (closed form)."""
    rs = [float(r) for r in rates]
    ts = [float(t) for t in exposures if float(t) > 0.0]
    if not rs or not ts:
        return GammaPrior(alpha=_EPS, beta=1.0)
    m = sum(rs) / len(rs)
    t_bar = sum(ts) / len(ts)
    if len(rs) >= 2 and m > 0.0:
        mean_r = m
        var_r = sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1)
        excess = var_r - m / t_bar
        if excess > 0.0:
            beta = m / excess
            alpha = m * beta
            return GammaPrior(alpha=max(alpha, _EPS), beta=max(beta, _EPS))
    # near-Poisson fallback: prior as informative as one average peer's exposure
    return GammaPrior(alpha=max(m, _EPS) * t_bar, beta=t_bar)
