"""Base-stock (S-1, S) policy for ultra-rare parts (spec §6.2). Returns values that
satisfy the §6.2 floors by construction."""

from __future__ import annotations

import math

from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.policy.lead_time import ltd_normal, ltd_pmf_compound_poisson
from trax_io_reco.policy.service_level import (
    ltd_quantile_from_pmf,
    round_half_up,
    z_for_fill_rate,
)


def compute_base_stock(
    *, projection: DemandProjection, lead_mean: float, lead_var: float, service_level: float
) -> tuple[int, int, int, int]:
    """Return (rop, eoq, safety_stock, max_stock) for a one-for-one base-stock policy.

    S = smallest integer with P(LTD > S) <= 1 - target; order-up-to max = S, eoq = 1,
    rop = S - 1, safety_stock = clamp(S - round(ltd_mean), 0..rop).
    """
    if projection.dist_kind in ("COMPOUND_POISSON", "NBD"):
        lam = projection.dist_params.get("lambda", projection.mean_per_day)
        clump_p = projection.dist_params.get("clump_p", 1.0)
        pmf = ltd_pmf_compound_poisson(
            lam=lam, clump_p=clump_p, lead_mean=lead_mean, lead_var=lead_var
        )
        s_level = ltd_quantile_from_pmf(pmf, service_level)
        ltd_mean = lam * lead_mean
    else:
        ltd_mean, sigma = ltd_normal(
            demand_mean_per_day=projection.mean_per_day,
            demand_var_per_day=projection.std_per_day**2,
            lead_mean=lead_mean,
            lead_var=lead_var,
        )
        s_level = int(math.ceil(ltd_mean + z_for_fill_rate(service_level) * sigma))

    s_level = max(1, s_level)
    max_stock = s_level
    eoq = 1
    rop = max_stock - eoq
    safety_stock = max(0, min(rop, s_level - round_half_up(ltd_mean)))
    return rop, eoq, safety_stock, max_stock
