"""Gamma-Poisson empirical-Bayes primitives (closed form, deterministic)."""

from __future__ import annotations

import math
from dataclasses import dataclass


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
