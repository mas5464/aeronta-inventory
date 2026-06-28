import math

from trax_io_forecasting.eb import (
    GammaPrior,
    fit_prior,
    posterior_predictive_var,
    posterior_rate,
)


def test_posterior_rate_shrinks_between_prior_mean_and_own_rate():
    prior = GammaPrior(alpha=2.0, beta=100.0)  # prior mean 0.02/day
    # sparse part: 1 event over 730 days (own rate ~0.00137)
    lam = posterior_rate(prior, count=1.0, exposure=730.0)
    assert 0.00137 < lam < 0.02  # pulled up toward the peer mean

def test_posterior_rate_zero_count_returns_prior_mean():
    prior = GammaPrior(alpha=4.0, beta=200.0)  # mean 0.02
    lam = posterior_rate(prior, count=0.0, exposure=0.0)
    assert lam == prior.mean == 0.02

def test_posterior_rate_ample_count_approaches_own_rate():
    prior = GammaPrior(alpha=2.0, beta=100.0)
    lam = posterior_rate(prior, count=10_000.0, exposure=10_000.0)
    assert abs(lam - 1.0) < 0.01  # own rate 1.0 dominates the weak prior

def test_posterior_predictive_var_exceeds_poisson_for_sparse_data():
    prior = GammaPrior(alpha=2.0, beta=10.0)
    lam = posterior_rate(prior, count=0.0, exposure=0.0)
    var = posterior_predictive_var(prior, count=0.0, exposure=0.0)
    assert var > lam  # wider than Poisson (var == mean) due to estimation uncertainty
    assert math.isfinite(var)


def test_posterior_rate_rejects_non_finite():
    prior = GammaPrior(alpha=2.0, beta=100.0)
    for bad in (math.inf, math.nan, -math.inf):
        try:
            posterior_rate(prior, count=bad, exposure=10.0)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for count={bad}")


def test_gamma_prior_rejects_non_positive():
    for bad in (0.0, -1.0, math.inf, math.nan):
        try:
            GammaPrior(alpha=bad, beta=1.0)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for alpha={bad}")


def test_fit_prior_recovers_mean_under_overdispersion():
    # rates spread well beyond Poisson sampling noise -> a real Gamma prior
    rates = [0.01, 0.05, 0.0, 0.08, 0.03, 0.12]
    exposures = [730.0] * len(rates)
    prior = fit_prior(rates, exposures)
    assert abs(prior.mean - (sum(rates) / len(rates))) < 1e-9
    assert prior.alpha > 0.0 and prior.beta > 0.0


def test_fit_prior_near_poisson_fallback_when_no_overdispersion():
    # identical rates -> zero sample variance -> excess <= 0 -> fallback beta = t_bar
    rates = [0.02, 0.02, 0.02, 0.02]
    exposures = [730.0] * 4
    prior = fit_prior(rates, exposures)
    assert prior.beta == 730.0
    assert abs(prior.mean - 0.02) < 1e-9


def test_fit_prior_single_peer_uses_fallback():
    prior = fit_prior([0.05], [365.0])
    assert prior.beta == 365.0
    assert abs(prior.mean - 0.05) < 1e-9


def test_fit_prior_empty_returns_floor():
    prior = fit_prior([], [])
    assert prior.alpha > 0.0 and prior.beta > 0.0


def test_fit_prior_deterministic():
    rates, exp = [0.01, 0.05, 0.0, 0.08], [730.0] * 4
    assert fit_prior(rates, exp) == fit_prior(rates, exp)
