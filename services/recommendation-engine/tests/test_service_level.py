from __future__ import annotations

import math

import pytest

from trax_io_reco.policy.service_level import (
    ltd_quantile_from_pmf,
    safety_stock_normal,
    z_for_fill_rate,
)


def test_z_for_95pct() -> None:
    assert math.isclose(z_for_fill_rate(0.95), 1.6449, abs_tol=1e-3)


def test_z_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        z_for_fill_rate(1.0)


def test_safety_stock_normal_textbook() -> None:
    # mu_LTD irrelevant; SS = z * sigma = 1.645 * 20 ~ 32.9
    assert math.isclose(safety_stock_normal(sigma_ltd=20.0, service_level=0.95), 32.9, abs_tol=0.5)


def test_quantile_from_pmf() -> None:
    # P(X<=3) = 0.96; smallest S with cumulative >= 0.95 is 3.
    pmf = [0.5, 0.3, 0.1, 0.06, 0.04]
    assert ltd_quantile_from_pmf(pmf, 0.95) == 3
    assert ltd_quantile_from_pmf(pmf, 0.5) == 0
