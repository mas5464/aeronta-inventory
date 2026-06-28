"""Gamma-Poisson empirical-Bayes primitives (closed form, deterministic)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GammaPrior:
    """Conjugate prior for a Poisson rate: shape alpha, rate beta (mean = alpha/beta)."""

    alpha: float
    beta: float

    @property
    def mean(self) -> float:
        return self.alpha / self.beta


def posterior_rate(prior: GammaPrior, count: float, exposure: float) -> float:
    """Posterior mean daily rate after observing `count` events over `exposure` time."""
    return (prior.alpha + count) / (prior.beta + exposure)


def posterior_predictive_var(prior: GammaPrior, count: float, exposure: float) -> float:
    """Per-unit posterior-predictive variance (negative-binomial): >= Poisson var (= mean)."""
    lam = posterior_rate(prior, count, exposure)
    return lam * (1.0 + 1.0 / (prior.beta + exposure))
