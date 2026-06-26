"""Lead-time-demand (LTD) distribution (spec §6.4).

Two paths:
- ``ltd_normal``: fast normal-approximation moments for moderate/high-volume demand.
- ``ltd_pmf_compound_poisson``: numeric LTD PMF for the sparse regimes (the slow path,
  implemented now, not deferred), so base-stock and (s,S) tail math actually closes.
"""

from __future__ import annotations

import math

from scipy.stats import nbinom, poisson

from trax_io_reco.contracts.context import PartLocationContext

_P90_Z = 1.2816  # z for the 90th percentile, to back out sigma from a p90


def lead_mean_var(context: PartLocationContext) -> tuple[float, float]:
    """Resolve the lead-time mean/variance by the spec §6.5 precedence:
    realized mean (if observed) → promised → current-policy replenishment → 14d.
    """
    lt = context.lead_time
    if lt is not None and lt.n_observations > 0:
        mean = float(lt.realized_mean_days)
        sigma = max(0.0, (float(lt.realized_p90_days) - mean) / _P90_Z)
        return mean, sigma**2
    if lt is not None:
        return float(lt.promised_lead_days), 0.0
    if context.current_policy.replenishment_lead_days > 0:
        return float(context.current_policy.replenishment_lead_days), 0.0
    return 14.0, 0.0


def protection_period_days(context: PartLocationContext) -> float:
    """The replenishment protection period a Purchase must cover (spec §6.5)."""
    return lead_mean_var(context)[0]


def ltd_normal(
    *, demand_mean_per_day: float, demand_var_per_day: float, lead_mean: float, lead_var: float
) -> tuple[float, float]:
    """Mean and sigma of lead-time demand for a random (demand-rate, lead-time) product.

    Standard random-sum result: mean = mu_d * mu_L; var = mu_L * sigma_d^2 + mu_d^2 * sigma_L^2.
    """
    mean = demand_mean_per_day * lead_mean
    var = lead_mean * demand_var_per_day + (demand_mean_per_day**2) * lead_var
    return mean, math.sqrt(max(0.0, var))


def ltd_pmf_compound_poisson(
    *,
    lam: float,
    clump_p: float,
    lead_mean: float,
    lead_var: float,
    support_max: int | None = None,
) -> list[float]:
    """Numeric LTD PMF over ``0..support_max`` for compound-Poisson demand.

    Arrivals over the lead time are Poisson(lam*lead_mean); a positive lead variance is
    folded in as a Poisson-Gamma (Negative-Binomial) mixture. Each arrival contributes a
    Geometric(clump_p) batch (clump_p == 1 → single-unit arrivals → demand == arrivals).
    """
    mu = max(1e-9, lam * lead_mean)
    if support_max is None:
        support_max = int(mu + 10.0 * math.sqrt(mu) + 10)

    # Arrival-count PMF (Poisson, or NB when lead variance overdisperses it).
    var_n = mu + (lam**2) * lead_var
    if lead_var > 1e-9 and var_n > mu + 1e-9:
        r = mu**2 / (var_n - mu)
        prob = r / (r + mu)
        arrival = [float(nbinom.pmf(n, r, prob)) for n in range(support_max + 1)]
    else:
        arrival = [float(poisson.pmf(n, mu)) for n in range(support_max + 1)]

    if clump_p >= 0.999:
        pmf = arrival
    else:
        # demand | N=n is the sum of n Geometric(clump_p) batches on {1,2,...},
        # i.e. NB(n, clump_p) shifted by n. P(sum=k) for k>=n.
        pmf = [0.0] * (support_max + 1)
        for n, pn in enumerate(arrival):
            if pn <= 0.0:
                continue
            if n == 0:
                pmf[0] += pn
                continue
            for k in range(n, support_max + 1):
                pmf[k] += pn * float(nbinom.pmf(k - n, n, clump_p))

    total = sum(pmf)
    return [x / total for x in pmf] if total > 0 else pmf
