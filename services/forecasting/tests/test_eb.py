import math

from trax_io_forecasting.eb import (
    GammaPrior,
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
    lam = posterior_rate(prior, count=1000.0, exposure=1000.0)
    assert abs(lam - 1.0) < 0.1  # own rate 1.0 dominates the weak prior

def test_posterior_predictive_var_exceeds_poisson_for_sparse_data():
    prior = GammaPrior(alpha=2.0, beta=10.0)
    lam = posterior_rate(prior, count=0.0, exposure=0.0)
    var = posterior_predictive_var(prior, count=0.0, exposure=0.0)
    assert var > lam  # wider than Poisson (var == mean) due to estimation uncertainty
    assert math.isfinite(var)
