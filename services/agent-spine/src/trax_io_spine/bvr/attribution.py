"""Projected-savings decomposition (spec §2) — pure, deterministic.

Baseline = the pre-agent policy (a change's `old_values`, or the extract's
CurrentPolicy for a first write — resolved by the caller via `baseline_for`).
Positive amounts = projected benefit; negatives reported as-is, never clamped.
Changes that cannot be valued (no unit cost / no baseline) are COUNTED
(`changes_total` vs `changes_valued`), never silently dropped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from trax_io_spine.bvr.models import ProjectedComponent, SavingsAttribution
from trax_io_spine.contracts import HistoryEntry, WritebackStatus

_ATTRIBUTED = (WritebackStatus.WRITTEN, WritebackStatus.SHADOWED)


@dataclass(frozen=True)
class AttributionRates:
    holding_cost_rate: float = 0.25  # per year
    per_order_cost: float = 85.0
    stockout_proxy_fraction: float = 0.10
    period_fraction: float = 1 / 12  # monthly-shaped report
    tier_weights: dict[int, float] = field(
        default_factory=lambda: {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.4, 5: 0.2}
    )

    def as_dict(self) -> dict[str, float]:
        return {
            "holding_cost_rate": self.holding_cost_rate,
            "per_order_cost": self.per_order_cost,
            "stockout_proxy_fraction": self.stockout_proxy_fraction,
            "period_fraction": self.period_fraction,
        }


@dataclass(frozen=True)
class KeyEconomics:
    unit_cost: float | None  # None => the change cannot be valued
    mean_per_day: float
    lead_mean: float  # days
    criticality_tier: int  # 1..5


@dataclass(frozen=True)
class ChangeValue:
    holding: float
    ordering: float
    stockout: float
    status: WritebackStatus

    @property
    def total(self) -> float:
        return self.holding + self.ordering + self.stockout


def _money(x: float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"))


def value_change(
    old: dict[str, int], new: dict[str, int], econ: KeyEconomics, rates: AttributionRates,
    *, status: WritebackStatus = WritebackStatus.WRITTEN,
) -> ChangeValue | None:
    """Value one policy change against its baseline. None ⇔ unvalued (no unit cost)."""
    if econ.unit_cost is None:
        return None
    frac = rates.period_fraction
    # Holding: Δ(safety_stock + EOQ/2) — average-position model; benefit when reduced.
    old_pos = old["safety_stock"] + old["eoq"] / 2
    new_pos = new["safety_stock"] + new["eoq"] / 2
    holding = (old_pos - new_pos) * econ.unit_cost * rates.holding_cost_rate * frac
    # Ordering frequency: annual_demand/EOQ; skipped (0.0) when either EOQ <= 0.
    annual = econ.mean_per_day * 365.0
    if old["eoq"] > 0 and new["eoq"] > 0:
        ordering = (annual / old["eoq"] - annual / new["eoq"]) * rates.per_order_cost * frac
    else:
        ordering = 0.0
    # Stockout-risk proxy: Δ units of lead-time demand covered at ROP, tier-weighted.
    ltd = econ.mean_per_day * econ.lead_mean
    covered_old = min(float(old["rop"]), ltd)
    covered_new = min(float(new["rop"]), ltd)
    weight = rates.tier_weights.get(econ.criticality_tier, 0.2)
    stockout = (
        (covered_new - covered_old)
        * econ.unit_cost * rates.stockout_proxy_fraction * weight * frac
    )
    return ChangeValue(holding=holding, ordering=ordering, stockout=stockout, status=status)


_HOLDING_FORMULA = (
    "Δ(safety_stock + EOQ/2) × unit_cost × holding_cost_rate × period_fraction"
)
_ORDERING_FORMULA = (
    "(annual_demand/EOQ_old − annual_demand/EOQ_new) × per_order_cost × period_fraction"
)
_STOCKOUT_FORMULA = (
    "Δ(lead-time demand covered at ROP) × unit_cost × stockout_proxy_fraction "
    "× tier_weight × period_fraction"
)


def build_savings(
    ledger: Iterable[HistoryEntry],
    baseline_for: Callable[[HistoryEntry], dict[str, int] | None],
    econ_for: Callable[[str, str], KeyEconomics | None],
    rates: AttributionRates,
) -> SavingsAttribution:
    holding = ordering = stockout = 0.0
    applied = shadowed = 0.0
    total = valued = 0
    for entry in ledger:
        if entry.status not in _ATTRIBUTED:
            continue
        total += 1
        baseline = entry.old_values if entry.old_values is not None else baseline_for(entry)
        econ = econ_for(entry.pn, entry.location)
        if baseline is None or econ is None:
            continue
        cv = value_change(baseline, entry.new_values, econ, rates, status=entry.status)
        if cv is None:
            continue
        valued += 1
        holding += cv.holding
        ordering += cv.ordering
        stockout += cv.stockout
        if entry.status is WritebackStatus.WRITTEN:
            applied += cv.total
        else:
            shadowed += cv.total

    assumptions = tuple(f"{k}={v}" for k, v in sorted(rates.as_dict().items()))

    def component(name: str, amount: float, formula: str) -> ProjectedComponent:
        return ProjectedComponent(
            name=name, amount=_money(amount), formula=formula,
            inputs={"changes_valued": valued, "changes_total": total},
            assumptions=assumptions,
        )

    return SavingsAttribution(
        holding_cost_delta=component("holding_cost_delta", holding, _HOLDING_FORMULA),
        ordering_cost_delta=component("ordering_cost_delta", ordering, _ORDERING_FORMULA),
        stockout_risk_delta=component("stockout_risk_delta", stockout, _STOCKOUT_FORMULA),
        total_projected_applied=_money(applied),
        total_projected_shadowed=_money(shadowed),
        total_projected=_money(applied + shadowed),
        changes_total=total,
        changes_valued=valued,
        assumption_rates=rates.as_dict(),
    )
