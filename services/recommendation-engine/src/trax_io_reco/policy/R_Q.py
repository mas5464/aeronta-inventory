"""(R, Q) periodic-review policy for moderate/high-volume parts (spec §6.2).

Protection period = lead + review; safety stock via the normal fast-path; Q = EOQ
floored at MinOQ. v1 uses a fixed 14-day review cycle.
"""

from __future__ import annotations

import math

from trax_io_reco.contracts.context import DemandProjection
from trax_io_reco.policy.lead_time import ltd_normal
from trax_io_reco.policy.service_level import round_half_up, safety_stock_normal

DEFAULT_REVIEW_PERIOD_DAYS = 14


def compute_R_Q(
    *,
    projection: DemandProjection,
    lead_mean: float,
    lead_var: float,
    service_level: float,
    ordering_cost: float,
    holding_cost_rate: float,
    unit_cost: float,
    min_order_qty: int,
    review_period_days: int = DEFAULT_REVIEW_PERIOD_DAYS,
) -> tuple[int, int, int, int]:
    """Return (rop, eoq, safety_stock, max_stock)."""
    protection = lead_mean + review_period_days
    mean, sigma = ltd_normal(
        demand_mean_per_day=projection.mean_per_day,
        demand_var_per_day=projection.std_per_day**2,
        lead_mean=protection,
        lead_var=lead_var,
    )
    safety_stock = max(
        0, round_half_up(safety_stock_normal(sigma_ltd=sigma, service_level=service_level))
    )
    rop = round_half_up(mean) + safety_stock

    annual_demand = projection.mean_per_day * 365.0
    holding = holding_cost_rate * unit_cost
    if holding > 0 and annual_demand > 0:
        wilson = round_half_up(math.sqrt(2.0 * annual_demand * ordering_cost / holding))
        eoq = max(min_order_qty, wilson)
    else:
        eoq = max(min_order_qty, 1)

    q = max(min_order_qty, eoq)
    max_stock = rop + q
    return rop, q, safety_stock, max_stock
