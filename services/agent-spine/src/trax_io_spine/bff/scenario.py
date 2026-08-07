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
from datetime import date, datetime
from typing import Literal

from trax_io_feature_store.schemas import LeadTimeDistribution
from trax_io_reco.contracts.context import TenantPolicyConfig
from trax_io_reco.contracts.repair import RepairPipeline
from trax_io_reco.demand.basis import historical_demand_stats
from trax_io_reco.policy.R_Q import DEFAULT_REVIEW_PERIOD_DAYS
from trax_io_reco.policy.service_level import round_half_up, z_for_fill_rate
from trax_io_reco.position.repair_pipeline import build_repair_pipeline
from trax_io_reco.repair_returns import project_repair_returns

_DEFAULT_LEAD_DAYS = 14.0  # spec §6.5 fallback when no lead-time signal exists.
_P90_Z = 1.2816  # z for the 90th percentile — mirrors policy/lead_time.py.
_SCENARIO_SERVICEABLE_YIELD = 1.0

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
    # Legacy compatibility input. It has always changed the NEW procurement
    # lead and therefore must never be reinterpreted as repair TAT.
    lead_time_delta_pct: float = 0.0
    procurement_lead_time_delta_pct: float | None = None
    repair_tat_delta_pct: float = 0.0
    scope: ScenarioScope = "all"
    scope_value: str | None = None  # required when scope != "all"

    @property
    def effective_procurement_lead_time_delta_pct(self) -> float:
        return (
            self.procurement_lead_time_delta_pct
            if self.procurement_lead_time_delta_pct is not None
            else self.lead_time_delta_pct
        )


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
    scored_key_ids: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class FrontierPoint:
    service_level: float
    projected_investment: float
    projected_coverage: float


@dataclass(frozen=True)
class RepairScenarioInput:
    """One repairable key's immutable Phase-5/REP projection inputs."""

    pn: str
    location: str
    criticality_tier: int | None
    ata_chapter: str | None
    pipeline: RepairPipeline
    repair_cycle_time: LeadTimeDistribution | None


@dataclass(frozen=True)
class RepairScenarioOutcome:
    """Portfolio repair-return summary at one fixed scenario horizon."""

    horizon_days: int
    eligible_quantity: int
    expected_units: float
    modeled_keys: int
    unavailable_keys: int
    unscoped_keys: int
    serviceable_yield_assumption: float
    modeled_key_ids: tuple[tuple[str, str], ...] = ()


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
    repair_current: RepairScenarioOutcome | None = None
    repair_proposed: RepairScenarioOutcome | None = None


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

        demand_stats = historical_demand_stats(dh)
        if (
            demand_stats.trace.exposure_days <= 0
            or demand_stats.trace.observation_window_source == "unavailable"
        ):
            # Missing history is not observed zero demand. Exclude the key just
            # like any other unscorable feature gap; a configured empty interval
            # still has positive exposure and remains a genuine zero-demand key.
            continue
        mean_per_day = demand_stats.trace.historical_per_day
        # Shared demand stats expand leading/interior/trailing zero buckets over
        # the configured inclusive exposure before computing sample variance.
        # The max(mean, variance) floor matches the engine's NORMAL projection.
        std_per_day = math.sqrt(max(mean_per_day, demand_stats.variance_per_day))

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


def _as_evidence_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _matches_key(feature, *, tenant_id: str, pn: str, location: str):
    if feature is None:
        return None
    for field_name, expected in (
        ("tenant_id", tenant_id),
        ("pn", pn),
        ("location", location),
    ):
        actual = getattr(feature, field_name, None)
        if actual is not None and str(actual) != expected:
            return None
    return feature


def build_repair_scenario_inputs(
    *,
    fs,
    tenant,
    keys: list[tuple[str, str]],
) -> list[RepairScenarioInput]:
    """Build immutable repair inputs once for repeated interactive solves.

    Repair inputs are built over the tenant's full part/location universe, not
    the narrower procurement-scorable ``KeyStats`` cache. This keeps a missing
    demand, cost, or NEW lead-time feature from silently removing otherwise
    valid repair WIP. Criticality and ATA metadata remain optional so a scoped
    solve can disclose keys it could not classify instead of treating them as
    non-matches. The REP lane is the sole duration source, and evidence newer
    than the physical WIP snapshot is withheld to prevent lookahead.
    """

    tenant_id = str(getattr(tenant, "tenant_id", ""))
    inputs: list[RepairScenarioInput] = []
    for pn, location in sorted(keys):
        try:
            attrs = fs.get_part_attributes(tenant=tenant, pn=pn)
        except Exception:  # noqa: BLE001
            continue
        if attrs is not None and (
            str(getattr(attrs, "tenant_id", tenant_id)) != tenant_id
            or str(getattr(attrs, "pn", pn)) != pn
        ):
            continue
        if str(getattr(attrs, "part_class", "") or "").lower() not in {
            "repairable",
            "rotable",
        }:
            continue

        try:
            stock = _matches_key(
                fs.get_stock_position(
                    tenant=tenant,
                    pn=pn,
                    location=location,
                ),
                tenant_id=tenant_id,
                pn=pn,
                location=location,
            )
        except Exception:  # noqa: BLE001
            stock = None
        if stock is None:
            continue

        try:
            open_orders = _matches_key(
                fs.get_open_orders_snapshot(
                    tenant=tenant,
                    pn=pn,
                    location=location,
                ),
                tenant_id=tenant_id,
                pn=pn,
                location=location,
            )
        except Exception:  # noqa: BLE001
            open_orders = None

        as_of = next(
            (
                parsed
                for value in (
                    getattr(open_orders, "snapshot_at", None),
                    getattr(open_orders, "extract_date", None),
                    getattr(stock, "extract_date", None),
                )
                if (parsed := _as_evidence_date(value)) is not None
            ),
            None,
        )
        if as_of is None:
            continue
        try:
            pipeline = build_repair_pipeline(
                tenant_id=tenant_id,
                part_number=pn,
                location_code=location,
                open_orders=open_orders,
                aggregate_wip_quantity=int(stock.unserviceable_in_repair),
                as_of=as_of,
            )
        except Exception:  # noqa: BLE001
            continue

        try:
            rep = fs.get_lead_time_distribution(
                tenant=tenant,
                pn=pn,
                vendor="DEFAULT",
                condition="REP",
            )
        except Exception:  # noqa: BLE001
            rep = None
        if rep is not None and (
            str(getattr(rep, "tenant_id", tenant_id)) != tenant_id
            or str(getattr(rep, "pn", pn)) != pn
            or str(getattr(rep, "condition", "REP")) != "REP"
        ):
            rep = None
        cutoff = _as_evidence_date(
            getattr(rep, "data_cutoff", None)
            or getattr(rep, "extract_date", None)
        )
        if cutoff is not None and cutoff > as_of:
            rep = None

        criticality_tier = None
        try:
            criticality = fs.get_criticality(tenant=tenant, pn=pn)
            if criticality is not None and (
                str(getattr(criticality, "tenant_id", tenant_id)) == tenant_id
                and str(getattr(criticality, "pn", pn)) == pn
            ):
                criticality_tier = int(criticality.canonical_tier)
        except Exception:  # noqa: BLE001
            pass

        inputs.append(
            RepairScenarioInput(
                pn=pn,
                location=location,
                criticality_tier=criticality_tier,
                ata_chapter=getattr(attrs, "ata_chapter", None),
                pipeline=pipeline,
                repair_cycle_time=rep,
            )
        )
    return inputs


def _in_scope(k: KeyStats, params: ScenarioParams) -> bool:
    if params.scope == "all":
        return True
    if params.scope == "criticality_tier":
        return str(k.criticality_tier) == params.scope_value
    if params.scope == "ata_chapter":
        return k.ata_chapter == params.scope_value
    return True  # pragma: no cover — Literal exhausts scope, defensive fallback


def _repair_in_scope(
    k: RepairScenarioInput,
    params: ScenarioParams,
) -> bool | None:
    if params.scope == "all":
        return True
    if params.scope == "criticality_tier":
        if k.criticality_tier is None:
            return None
        return str(k.criticality_tier) == params.scope_value
    if params.scope == "ata_chapter":
        if k.ata_chapter is None:
            return None
        return k.ata_chapter == params.scope_value
    return True


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
    investments: list[float] = []
    keys_meeting_rop = 0
    z_cache: dict[float, float] = {}
    ordered_keys = sorted(keys, key=lambda key: (key.pn, key.location))

    lead_multiplier = max(0.0, 1.0 + lead_time_delta_pct)
    annual_factor = 365.0
    holding_rate = cfg.holding_cost_rate
    ordering_cost = cfg.ordering_cost

    for k in ordered_keys:
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

        investments.append(investment)
        if k.on_hand >= rop:
            keys_meeting_rop += 1

    n = len(ordered_keys)
    total_investment = math.fsum(investments)
    on_hand_gap_ratio = keys_meeting_rop / n if n else 1.0
    # Representative service level for the outcome label — demand-weighted mean of the
    # per-key targets actually applied (honest even when the scope mixes tiers). Also
    # doubles as `projected_coverage`: the achieved cycle-service-level of a *fully
    # funded* proposed policy, by construction of the safety-stock formula above
    # (see module docstring for why this — not an on-hand snapshot — is "coverage").
    if n == 0:
        effective_sl = service_level_target or 0.0
    else:
        effective_sl = math.fsum(
            _target_for(
                k,
                service_level_target=service_level_target,
                service_level_by_tier=service_level_by_tier,
                cfg=cfg,
            )
            for k in ordered_keys
        ) / n

    return ScenarioOutcome(
        service_level=effective_sl,
        projected_investment=total_investment,
        projected_coverage=effective_sl,
        on_hand_gap_ratio=on_hand_gap_ratio,
        scored_keys=n,
        scored_key_ids=tuple((key.pn, key.location) for key in ordered_keys),
    )


def _solve_repair_returns(
    inputs: list[RepairScenarioInput],
    *,
    repair_tat_delta_pct: float,
    horizon_days: int = 90,
    unscoped_keys: int = 0,
) -> RepairScenarioOutcome:
    # Keep the core multiplier strictly positive. The UI constrains this input
    # to -50%..+100%; the floor is a defensive compatibility boundary for old
    # or direct API payloads.
    tat_multiplier = max(0.01, 1.0 + repair_tat_delta_pct)
    eligible_quantity = 0
    expectations: list[float] = []
    modeled_keys = 0
    unavailable_keys = 0
    modeled_key_ids: list[tuple[str, str]] = []

    for item in sorted(inputs, key=lambda value: (value.pn, value.location)):
        try:
            profile = project_repair_returns(
                pipeline=item.pipeline,
                horizons=(horizon_days,),
                completed_cycle_days=(
                    item.repair_cycle_time.observed_cycle_days
                    if item.repair_cycle_time is not None
                    else ()
                ),
                repair_cycle_time=item.repair_cycle_time,
                serviceable_yield=_SCENARIO_SERVICEABLE_YIELD,
                tat_multiplier=tat_multiplier,
            )
        except Exception:  # noqa: BLE001
            if item.pipeline.eligible_quantity:
                unavailable_keys += 1
                eligible_quantity += item.pipeline.eligible_quantity
            continue

        if profile.eligible_quantity == 0:
            continue
        eligible_quantity += profile.eligible_quantity
        if profile.evidence.method == "unavailable":
            unavailable_keys += 1
            continue
        modeled_keys += 1
        modeled_key_ids.append((item.pn, item.location))
        expectations.append(profile.horizons[0].expected_units)

    return RepairScenarioOutcome(
        horizon_days=horizon_days,
        eligible_quantity=eligible_quantity,
        expected_units=math.fsum(expectations),
        modeled_keys=modeled_keys,
        unavailable_keys=unavailable_keys,
        unscoped_keys=unscoped_keys,
        serviceable_yield_assumption=_SCENARIO_SERVICEABLE_YIELD,
        modeled_key_ids=tuple(modeled_key_ids),
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
        self,
        key_stats: list[KeyStats],
        *,
        total_keys_in_universe: int | None = None,
        repair_inputs: list[RepairScenarioInput] | None = None,
    ) -> None:
        self._all_keys = key_stats
        self._repair_inputs = repair_inputs
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
            lead_time_delta_pct=params.effective_procurement_lead_time_delta_pct,
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
                        lead_time_delta_pct=params.effective_procurement_lead_time_delta_pct,
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
        repair_scoped: list[RepairScenarioInput] | None = None
        repair_unscoped_keys = 0
        if self._repair_inputs is not None:
            repair_scoped = []
            for item in self._repair_inputs:
                scope_match = _repair_in_scope(item, params)
                if scope_match:
                    repair_scoped.append(item)
                elif scope_match is None and item.pipeline.eligible_quantity > 0:
                    repair_unscoped_keys += 1
        repair_current = (
            _solve_repair_returns(
                repair_scoped,
                repair_tat_delta_pct=0.0,
                unscoped_keys=repair_unscoped_keys,
            )
            if repair_scoped is not None
            else None
        )
        repair_proposed = (
            _solve_repair_returns(
                repair_scoped,
                repair_tat_delta_pct=params.repair_tat_delta_pct,
                unscoped_keys=repair_unscoped_keys,
            )
            if repair_scoped is not None
            else None
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
            repair_current=repair_current,
            repair_proposed=repair_proposed,
        )


# round_half_up imported for re-export/parity with the policy engine's rounding
# convention; kept available for callers (e.g. BFF wire-model construction) that want
# integer-rounded ROP/safety-stock display without re-importing from trax_io_reco.
__all__ = [
    "FRONTIER_SERVICE_LEVELS",
    "FrontierPoint",
    "KeyStats",
    "RepairScenarioInput",
    "RepairScenarioOutcome",
    "ScenarioOutcome",
    "ScenarioParams",
    "ScenarioScope",
    "ScenarioSolver",
    "SolveResult",
    "build_key_stats",
    "build_repair_scenario_inputs",
    "round_half_up",
]
