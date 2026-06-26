from __future__ import annotations

import math

from trax_io_reco.policy.lead_time import ltd_normal, ltd_pmf_compound_poisson
from trax_io_reco.policy.service_level import ltd_quantile_from_pmf


def test_ltd_normal_deterministic_lead() -> None:
    mean, sigma = ltd_normal(
        demand_mean_per_day=2.0, demand_var_per_day=2.0, lead_mean=10.0, lead_var=0.0
    )
    assert mean == 20.0
    assert math.isclose(sigma, math.sqrt(10.0 * 2.0), rel_tol=1e-9)


def test_compound_poisson_pmf_sums_to_one() -> None:
    pmf = ltd_pmf_compound_poisson(lam=0.1, clump_p=1.0, lead_mean=20.0, lead_var=0.0)
    assert math.isclose(sum(pmf), 1.0, abs_tol=1e-6)


def test_compound_poisson_quantile_matches_poisson() -> None:
    # lam*lead = 2.0, clump_p=1, deterministic lead -> Poisson(2.0).
    # Poisson(2) cdf: S=4 -> 0.947 (<0.95), S=5 -> 0.983 (>=0.95). Expect S=5.
    pmf = ltd_pmf_compound_poisson(lam=0.1, clump_p=1.0, lead_mean=20.0, lead_var=0.0)
    assert ltd_quantile_from_pmf(pmf, 0.95) == 5
