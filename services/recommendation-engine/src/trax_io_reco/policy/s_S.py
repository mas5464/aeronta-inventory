"""(s, S) continuous-review policy for intermittent parts (spec §6.2)."""

from __future__ import annotations

import math

from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.policy.lead_time import ltd_normal, ltd_pmf_compound_poisson
from trax_io_reco.policy.service_level import (
    ltd_quantile_from_pmf,
    round_half_up,
    z_for_fill_rate,
)


def _eoq(
    *, mean_per_day: float, ordering_cost: float, holding_cost_rate: float, unit_cost: float,
    min_order_qty: int,
) -> int:
    annual_demand = mean_per_day * 365.0
    holding = holding_cost_rate * unit_cost
    if holding > 0 and annual_demand > 0:
        wilson = round_half_up(math.sqrt(2.0 * annual_demand * ordering_cost / holding))
        return max(min_order_qty, wilson)
    return max(min_order_qty, 1)


def compute_s_S(
    *,
    projection: DemandProjection,
    lead_mean: float,
    lead_var: float,
    service_level: float,
    ordering_cost: float,
    holding_cost_rate: float,
    unit_cost: float,
    min_order_qty: int,
) -> tuple[int, int, int, int]:
    """Return (rop, eoq, safety_stock, max_stock). s = ROP, S = ROP + EOQ."""
    if projection.dist_kind in ("COMPOUND_POISSON", "NBD"):
        lam = projection.dist_params.get("lambda", projection.mean_per_day)
        clump_p = projection.dist_params.get("clump_p", 1.0)
        pmf = ltd_pmf_compound_poisson(
            lam=lam, clump_p=clump_p, lead_mean=lead_mean, lead_var=lead_var
        )
        rop = ltd_quantile_from_pmf(pmf, service_level)
        ltd_mean = lam * lead_mean
        safety_stock = max(0, rop - round_half_up(ltd_mean))
    else:
        ltd_mean, sigma = ltd_normal(
            demand_mean_per_day=projection.mean_per_day,
            demand_var_per_day=projection.std_per_day**2,
            lead_mean=lead_mean,
            lead_var=lead_var,
        )
        safety_stock = max(0, round_half_up(z_for_fill_rate(service_level) * sigma))
        rop = round_half_up(ltd_mean) + safety_stock

    eoq = _eoq(
        mean_per_day=projection.mean_per_day, ordering_cost=ordering_cost,
        holding_cost_rate=holding_cost_rate, unit_cost=unit_cost, min_order_qty=min_order_qty,
    )
    max_stock = rop + eoq
    return rop, eoq, safety_stock, max_stock
