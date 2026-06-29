import math

from trax_io_forecasting.backtest import backtest_key, eb_rate_fn
from trax_io_forecasting.eb import GammaPrior


def test_eb_rate_fn_returns_posterior_per_period_rate():
    prior = GammaPrior(alpha=1.0, beta=5.0)  # prior mean 0.2/period
    fn = eb_rate_fn(prior)
    # 2 events over 4 periods -> (1+2)/(5+4) = 0.3333...
    assert abs(fn([1.0, 0.0, 1.0, 0.0]) - (3.0 / 9.0)) < 1e-9


def test_eb_rate_fn_slots_into_backtest_without_nan():
    prior = GammaPrior(alpha=1.0, beta=10.0)
    score = backtest_key([0, 1, 0, 0, 2, 0, 1, 0], eb_rate_fn(prior), holdout=3)
    assert math.isfinite(score) or score == math.inf  # well-defined, never NaN
    assert not (isinstance(score, float) and math.isnan(score))


def test_eb_rate_fn_deterministic():
    prior = GammaPrior(alpha=2.0, beta=7.0)
    fn = eb_rate_fn(prior)
    assert fn([1.0, 0.0, 3.0]) == fn([1.0, 0.0, 3.0])
