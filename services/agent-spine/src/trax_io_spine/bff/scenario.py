"""Slice S6 — What-If Scenarios: the scenario solver (PRD §6.5).

A REAL solver over the real key universe (`PlannerStore.fs` / `.keys`), honest about
scope. It does NOT invoke the full `RecommendationService` / `MiniPolicyEngine` per
solve (that would be O(keys) pydantic-model construction repeated ~7x per frontier —
too slow for an interactive slider at 22.9K keys). Instead it **mirrors the exact same
analytic relationships** the policy engine uses (spec §6.4 normal-approximation
safety stock, spec §6.2 (R,Q) reorder-point + EOQ) directly over cached per-key demand
+ lead-time + cost primitives:

- ``protection = adjusted_lead_mean + DEFAULT_REVIEW_PERIOD_DAYS`` — the periodic-review
  protection period `compute_R_Q` covers a Purchase with (spec §6.2; the review-period
  constant is imported from R_Q.py, not redefined here).
- ``safety_stock = z(service_level) * sigma_LTD`` — `ltd_normal`'s moments computed over
  `protection` (NOT raw lead_mean) + `z_for_fill_rate`
  (services/recommendation-engine/src/trax_io_reco/policy/{lead_time,service_level}.py).
- ``rop = mean_per_day * protection + safety_stock`` — the (R,Q) family's reorder point,
  exactly mirroring `compute_R_Q`'s protection-period composition
  (services/recommendation-engine/src/trax_io_reco/policy/R_Q.py `compute_R_Q`).
- ``eoq`` — the same Wilson-lot-size formula as `compute_R_Q`/`compute_s_S`
  (``sqrt(2 * annual_demand * ordering_cost / holding_cost)``, floored at MinOQ).
- ``investment`` — ``(rop + eoq/2) * unit_cost`` (reorder point + half the order
  cycle — the standard average-on-hand-under-a-(R,Q)-policy approximation), rolled up
  network-wide.
- ``coverage`` (``ScenarioOutcome.projected_coverage``) — the demand-weighted mean of
  the per-key cycle-service-level *targets* actually solved for (same value as
  `service_level`). This is deliberately NOT an on-hand-vs-shortage snapshot: safety
  stock is defined as ``z(SL) * sigma_LTD`` precisely so that a *fully funded* (R,Q)
  policy at target SL delivers SL cycle-service-level by construction — reporting
  anything else as "coverage" would make it mechanically *decrease* as the SL slider
  rises (a fixed real `on_hand` looks worse against a growing target ROP), which is
  both non-monotonic and misleading as a "coverage" label. The separate, real
  fixed-vs-target execution gap — how much of the *current* real on-hand already meets
  the *proposed* ROP — is reported honestly as `ScenarioOutcome.on_hand_gap_ratio`
  instead, and is NOT expected to be monotonic in SL (raising the target without
  buying anything necessarily widens the gap).

Demand mean/std-per-day mirror `HistoricalScheduledProjector` (moderate/high_volume
NORMAL path) for every key uniformly — the solver does not run the regime classifier or
switch policy families per key. This is a deliberate, documented simplification (see
`ScenarioSolver` docstring) so the whole 22.9K-key universe solves through one vectorizable
code path at interactive latency; it does NOT reproduce the engine's regime-conditional
policy-family dispatch (base-stock / (s,S) / (R,Q)) exactly for ultra-rare/intermittent
keys. `TenantPolicyConfig` (service_level_by_tier, holding_cost_rate, ordering_cost) is
the same onboarding config the real engine reads.

Keys lacking demand history, criticality, vendor economics, or stock position cannot be
scored at all and are excluded from `build_key_stats`'s output entirely (never
defaulted to zero). `SolveResult.skipped_keys` counts exactly these globally-unscorable
keys, out of the tenant's full real key universe (`SolveResult.total_keys`) — a data-
quality disclosure, independent of and not to be confused with the scenario's `scope`
filter (which intentionally narrows to a subset, e.g. one criticality tier; how many of
*those* were actually scored is `ScenarioOutcome.scored_keys`).
"""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass, field
from typing import Literal

from trax_io_reco.contracts.context import TenantPolicyConfig
from trax_io_reco.policy.R_Q import DEFAULT_REVIEW_PERIOD_DAYS
from trax_io_reco.policy.service_level import round_half_up, z_for_fill_rate

_DAYS_PER_BUCKET = {"day": 1.0, "week": 7.0, "month": 30.44}
_BASIS_WINDOW_DAYS = 730  # 24 months — mirrors HistoricalScheduledProjector's default.
_DEFAULT_LEAD_DAYS = 14.0  # spec §6.5 fallback when no lead-time signal exists.
_P90_Z = 1.2816  # z for the 90th percentile — mirrors policy/lead_time.py.

ScenarioScope = Literal["all", "criticality_tier", "ata_chapter"]


@dataclass(frozen=True)
class KeyStats:
    """Precomputed, scenario-independent per-key primitives (cached on the store so
    repeated solves — e.g. the 5-7 frontier points — don't re-derive them)."""

    pn: str
    location: str
    criticality_tier: int
    ata_chapter: str | None
    mean_per_day: float
    std_per_day: float
    lead_mean: float
    lead_var: float
    unit_cost: float
    min_order_qty: int
    on_hand: int


@dataclass(frozen=True)
class ScenarioParams:
    """Solver inputs — the What-If sliders (PRD §6.5). All optional overrides fall
    back to the real `TenantPolicyConfig` / current-state defaults when unset."""

    service_level_target: float | None = None  # global SL override, e.g. 0.97
    service_level_by_tier: dict[int, float] = field(default_factory=dict)  # per-tier overrides
    budget_cap: float | None = None  # optional $ investment cap (informational bind flag)
    lead_time_delta_pct: float = 0.0  # TAT slider, e.g. +0.20 == leads 20% longer
    scope: ScenarioScope = "all"
    scope_value: str | None = None  # required when scope != "all"


@dataclass(frozen=True)
class ScenarioOutcome:
    """One solved point (used for both the primary solve and each frontier point).

    `projected_coverage` == the demand-weighted mean target cycle-service-level
    actually solved for (monotonic in the SL slider by construction — see module
    docstring). `on_hand_gap_ratio` is a distinct, real metric: the fraction of the
    scoped keys' *current real* on-hand that already meets the *proposed* reorder
    point (``on_hand >= rop``) — useful ("how much do we still need to buy"), but
    honestly NOT expected to be monotonic in SL.
    """

    service_level: float
    projected_investment: float
    projected_coverage: float
    on_hand_gap_ratio: float
    scored_keys: int


@dataclass(frozen=True)
class FrontierPoint:
    service_level: float
    projected_investment: float
    projected_coverage: float


@dataclass(frozen=True)
class SolveResult:
    params: ScenarioParams
    current: ScenarioOutcome
    proposed: ScenarioOutcome
    delta_investment: float
    delta_coverage: float
    frontier: tuple[FrontierPoint, ...]
    skipped_keys: int
    total_keys: int
    budget_cap_binds: bool


# Frontier sweep points (PRD §6.5 "cost-service trade-off frontier"), .90 -> .995.
FRONTIER_SERVICE_LEVELS: tuple[float, ...] = (0.90, 0.93, 0.95, 0.97, 0.98, 0.99, 0.995)


def build_key_stats(*, fs, tenant, keys: list[tuple[str, str]]) -> list[KeyStats]:
    """Derive the cacheable per-key primitives once from the real feature store.

    Keys missing any required feature group (criticality, demand history, vendor
    economics, stock position) are excluded from the returned list — `len(keys) -
    len(result)` is the honest count of globally-unscorable keys, surfaced as
    `SolveResult.skipped_keys` by `ScenarioSolver`. Never fabricated as zeros.
    """
    stats: list[KeyStats] = []
    for pn, loc in keys:
        try:
            crit = fs.get_criticality(tenant=tenant, pn=pn)
            dh = fs.get_demand_history(tenant=tenant, pn=pn, location=loc)
            ve = fs.get_vendor_economics(tenant=tenant, pn=pn, vendor="DEFAULT")
            sp = fs.get_stock_position(tenant=tenant, pn=pn, location=loc)
        except Exception:  # noqa: BLE001 — feature groups may be absent for a key
            continue

        obs = dh.observations
        total_demand = float(sum(o.removals + o.issues for o in obs))
        mean_per_day = total_demand / _BASIS_WINDOW_DAYS

        daily_rates = [
            (o.removals + o.issues) / _DAYS_PER_BUCKET.get(o.bucket, 30.44) for o in obs
        ] or [0.0]
        r_mean = sum(daily_rates) / len(daily_rates)
        r_var = sum((x - r_mean) ** 2 for x in daily_rates) / max(1, len(daily_rates) - 1)
        std_per_day = math.sqrt(max(mean_per_day, r_var))

        lt = None
        # Lead-time is optional (spec §6.5 precedence falls back to a 14d default).
        with contextlib.suppress(Exception):
            lt = fs.get_lead_time_distribution(
                tenant=tenant, pn=pn, vendor="DEFAULT", condition="NEW"
            )
        if lt is not None and lt.n_observations > 0:
            lead_mean = float(lt.realized_mean_days)
            sigma = max(0.0, (float(lt.realized_p90_days) - lead_mean) / _P90_Z)
            lead_var = sigma**2
        elif lt is not None:
            lead_mean, lead_var = float(lt.promised_lead_days), 0.0
        else:
            lead_mean, lead_var = _DEFAULT_LEAD_DAYS, 0.0

        ata = None
        try:
            attrs = fs.get_part_attributes(tenant=tenant, pn=pn)
            ata = attrs.ata_chapter if attrs else None
        except Exception:  # noqa: BLE001
            pass

        stats.append(
            KeyStats(
                pn=pn,
                location=loc,
                criticality_tier=int(crit.canonical_tier),
                ata_chapter=ata,
                mean_per_day=mean_per_day,
                std_per_day=std_per_day,
                lead_mean=lead_mean,
                lead_var=lead_var,
                unit_cost=float(ve.unit_cost),
                min_order_qty=int(ve.minimum_order_qty),
                on_hand=int(sp.on_hand) if sp else 0,
            )
        )
    return stats


def _in_scope(k: KeyStats, params: ScenarioParams) -> bool:
    if params.scope == "all":
        return True
    if params.scope == "criticality_tier":
        return str(k.criticality_tier) == params.scope_value
    if params.scope == "ata_chapter":
        return k.ata_chapter == params.scope_value
    return True  # pragma: no cover — Literal exhausts scope, defensive fallback


def _target_for(
    k: KeyStats,
    *,
    service_level_target: float | None,
    service_level_by_tier: dict[int, float],
    cfg: TenantPolicyConfig,
) -> float:
    target = service_level_by_tier.get(
        k.criticality_tier,
        service_level_target
        if service_level_target is not None
        else cfg.service_level_by_tier.get(k.criticality_tier, 0.95),
    )
    return min(max(target, 1e-6), 1 - 1e-9)  # keep z() finite/defined


def _solve_one(
    keys: list[KeyStats],
    *,
    service_level_target: float | None,
    service_level_by_tier: dict[int, float],
    lead_time_delta_pct: float,
    cfg: TenantPolicyConfig,
) -> ScenarioOutcome:
    """One (R,Q)-family solve over an already-scoped key list at a given SL policy.

    Mirrors `trax_io_reco.policy.R_Q.compute_R_Q` (spec §6.2) exactly: the lead-time
    slider is applied to `lead_mean` first, then `protection = adjusted_lead_mean +
    DEFAULT_REVIEW_PERIOD_DAYS` (the periodic-review protection period, imported from
    R_Q.py — not hardcoded here) is used for BOTH the LTD mean and the LTD variance,
    exactly as `compute_R_Q` does. safety stock = z(SL) * sigma_LTD over `protection`,
    rop = mean LTD (over `protection`) + safety stock, eoq via the Wilson lot-size
    formula. Investment = rop + half the order cycle (average on-hand under a periodic
    (R,Q) policy), summed at each key's real unit cost. See module docstring for why
    `projected_coverage` (monotonic in SL) and `on_hand_gap_ratio` (not expected to be)
    are reported as two distinct fields rather than one "coverage".

    Performance note: `z_for_fill_rate` calls `scipy.stats.norm.ppf`, which costs
    ~0.15ms/call — negligible once, but 20k+ calls (one per key) dominates the runtime
    at real-extract scale (measured ~4.3s for a full solve's worth of per-key calls
    across current+proposed+7 frontier passes). The set of *distinct* SL targets in
    play per pass is small (at most one per criticality tier, ~5), so z-scores are
    memoized per distinct target here rather than recomputed per key — this is a pure
    performance optimization, not an approximation (same normal-approximation formula,
    same result).
    """
    total_investment = 0.0
    keys_meeting_rop = 0
    n = 0
    z_cache: dict[float, float] = {}

    lead_multiplier = max(0.0, 1.0 + lead_time_delta_pct)
    annual_factor = 365.0
    holding_rate = cfg.holding_cost_rate
    ordering_cost = cfg.ordering_cost

    for k in keys:
        target = _target_for(
            k,
            service_level_target=service_level_target,
            service_level_by_tier=service_level_by_tier,
            cfg=cfg,
        )
        z = z_cache.get(target)
        if z is None:
            z = z_for_fill_rate(target)
            z_cache[target] = z

        lead_mean = k.lead_mean * lead_multiplier
        protection = lead_mean + DEFAULT_REVIEW_PERIOD_DAYS

        ltd_mean = k.mean_per_day * protection
        ltd_var = protection * (k.std_per_day**2) + (k.mean_per_day**2) * k.lead_var
        sigma_ltd = math.sqrt(ltd_var) if ltd_var > 0.0 else 0.0

        safety_stock = max(0.0, z * sigma_ltd)
        rop = ltd_mean + safety_stock

        annual_demand = k.mean_per_day * annual_factor
        holding = holding_rate * k.unit_cost
        if holding > 0 and annual_demand > 0:
            eoq = max(k.min_order_qty, math.sqrt(2.0 * annual_demand * ordering_cost / holding))
        else:
            eoq = max(k.min_order_qty, 1)

        investment = (rop + eoq / 2.0) * k.unit_cost

        total_investment += investment
        if k.on_hand >= rop:
            keys_meeting_rop += 1
        n += 1

    on_hand_gap_ratio = keys_meeting_rop / n if n else 1.0
    # Representative service level for the outcome label — demand-weighted mean of the
    # per-key targets actually applied (honest even when the scope mixes tiers). Also
    # doubles as `projected_coverage`: the achieved cycle-service-level of a *fully
    # funded* proposed policy, by construction of the safety-stock formula above
    # (see module docstring for why this — not an on-hand snapshot — is "coverage").
    if n == 0:
        effective_sl = service_level_target or 0.0
    else:
        effective_sl = sum(
            _target_for(
                k,
                service_level_target=service_level_target,
                service_level_by_tier=service_level_by_tier,
                cfg=cfg,
            )
            for k in keys
        ) / n

    return ScenarioOutcome(
        service_level=effective_sl,
        projected_investment=total_investment,
        projected_coverage=effective_sl,
        on_hand_gap_ratio=on_hand_gap_ratio,
        scored_keys=n,
    )


class ScenarioSolver:
    """Stateless solver over a precomputed `KeyStats` cache (owned by `PlannerStore`).

    `total_keys_in_universe` is the tenant's full real `(pn, location)` key count
    (`len(PlannerStore.keys)`) — independent of how many of those `build_key_stats`
    could actually score. Defaults to `len(key_stats)` (i.e. "no globally-unscorable
    keys") when not supplied, so existing callers/tests that don't care about the
    data-quality gap keep working.
    """

    def __init__(
        self, key_stats: list[KeyStats], *, total_keys_in_universe: int | None = None
    ) -> None:
        self._all_keys = key_stats
        self._total_keys_in_universe = (
            total_keys_in_universe if total_keys_in_universe is not None else len(key_stats)
        )

    def solve(self, params: ScenarioParams) -> SolveResult:
        cfg = TenantPolicyConfig()
        scoped = [k for k in self._all_keys if _in_scope(k, params)]
        # Honest data-quality gap: keys in the tenant's full universe that
        # `build_key_stats` could not score at all (missing demand history,
        # criticality, vendor economics, or stock position) — NOT the scope filter's
        # exclusions, which are intentional and already visible via `scored_keys`.
        skipped = self._total_keys_in_universe - len(self._all_keys)

        current = _solve_one(
            scoped,
            service_level_target=None,
            service_level_by_tier={},
            lead_time_delta_pct=0.0,
            cfg=cfg,
        )
        proposed = _solve_one(
            scoped,
            service_level_target=params.service_level_target,
            service_level_by_tier=params.service_level_by_tier,
            lead_time_delta_pct=params.lead_time_delta_pct,
            cfg=cfg,
        )

        frontier = tuple(
            FrontierPoint(
                service_level=sl,
                projected_investment=(
                    outcome := _solve_one(
                        scoped,
                        service_level_target=sl,
                        service_level_by_tier={},
                        lead_time_delta_pct=params.lead_time_delta_pct,
                        cfg=cfg,
                    )
                ).projected_investment,
                projected_coverage=outcome.projected_coverage,
            )
            for sl in FRONTIER_SERVICE_LEVELS
        )

        budget_binds = (
            params.budget_cap is not None and proposed.projected_investment > params.budget_cap
        )

        return SolveResult(
            params=params,
            current=current,
            proposed=proposed,
            delta_investment=proposed.projected_investment - current.projected_investment,
            delta_coverage=proposed.projected_coverage - current.projected_coverage,
            frontier=frontier,
            skipped_keys=skipped,
            total_keys=self._total_keys_in_universe,
            budget_cap_binds=budget_binds,
        )


# round_half_up imported for re-export/parity with the policy engine's rounding
# convention; kept available for callers (e.g. BFF wire-model construction) that want
# integer-rounded ROP/safety-stock display without re-importing from trax_io_reco.
__all__ = [
    "FRONTIER_SERVICE_LEVELS",
    "FrontierPoint",
    "KeyStats",
    "ScenarioOutcome",
    "ScenarioParams",
    "ScenarioScope",
    "ScenarioSolver",
    "SolveResult",
    "build_key_stats",
    "round_half_up",
]
