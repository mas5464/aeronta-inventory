"""Hand-computed fixtures for the projected-savings decomposition (spec §2).

Fixture A (single applied change), rates = defaults, period_fraction = 1/12:
  old = {rop 3, eoq 10, safety_stock 2, max_stock 20}
  new = {rop 8, eoq 20, safety_stock 4, max_stock 30}
  econ: unit_cost 100.0, mean_per_day 0.5 (annual 182.5), lead_mean 10, tier 2
  holding  = ((2+10/2) - (4+20/2)) * 100 * 0.25 / 12 = (7-14)*100*0.25/12 = -14.5833…
  ordering = (182.5/10 - 182.5/20) * 85 / 12 = 9.125*85/12 = 64.6354…
  stockout = (min(8, 5) - min(3, 5)) * 100 * 0.10 * 0.8 / 12 = 2*16/2… = 16/12 = 1.3333…
             (lead-time demand = 0.5*10 = 5 units; covered_new 5, covered_old 3)
  totals quantized: holding -14.58, ordering 64.64, stockout 1.33, sum 51.39
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trax_io_spine.bvr.attribution import (
    AttributionRates,
    KeyEconomics,
    build_savings,
    value_change,
)
from trax_io_spine.contracts import HistoryEntry, WritebackStatus

_OLD = {"rop": 3, "eoq": 10, "safety_stock": 2, "max_stock": 20}
_NEW = {"rop": 8, "eoq": 20, "safety_stock": 4, "max_stock": 30}
_ECON = KeyEconomics(unit_cost=100.0, mean_per_day=0.5, lead_mean=10.0, criticality_tier=2)
_RATES = AttributionRates()


def _entry(status: WritebackStatus, old: dict | None = _OLD) -> HistoryEntry:
    return HistoryEntry(
        tenant_id="acme", pn="PN1", location="YYZ", version=1, status=status,
        old_values=old, new_values=_NEW, provenance_id="prov-1", tier=None,
        agent_version="spine-0.1.0", changed_by_principal="agent",
        idempotency_key=None, parent_version=None,
        changed_at=datetime(2024, 4, 15, tzinfo=UTC),
    )


def test_value_change_matches_hand_computation():
    cv = value_change(_OLD, _NEW, _ECON, _RATES)
    assert cv is not None
    assert round(cv.holding, 4) == -14.5833
    assert round(cv.ordering, 4) == 64.6354
    assert round(cv.stockout, 4) == 1.3333


def test_value_change_unvalued_when_no_unit_cost():
    econ = KeyEconomics(unit_cost=None, mean_per_day=0.5, lead_mean=10.0, criticality_tier=2)
    assert value_change(_OLD, _NEW, econ, _RATES) is None


def test_value_change_skips_ordering_when_eoq_nonpositive():
    old = dict(_OLD, eoq=0)
    cv = value_change(old, _NEW, _ECON, _RATES)
    assert cv is not None
    assert cv.ordering == 0.0  # component skipped, not infinite
    assert cv.ordering_skipped is True


def test_value_change_floors_negative_coverage_at_zero():
    # A negative ROP (bad data) must not produce negative "coverage" per spec's
    # "floored at 0" text. lead-time demand = 0.5*10 = 5; covered_old floored to 0
    # (unfloored would be -5), covered_new = min(8, 5) = 5.
    old = dict(_OLD, rop=-5)
    cv = value_change(old, _NEW, _ECON, _RATES)
    assert cv is not None
    # (5 - 0) * 100 * 0.10 * 0.8 / 12 = 3.3333..., not the unfloored 6.6667
    assert round(cv.stockout, 4) == 3.3333


def test_build_savings_splits_applied_and_shadowed_and_counts_coverage():
    ledger = (
        _entry(WritebackStatus.WRITTEN),
        _entry(WritebackStatus.SHADOWED),
        _entry(WritebackStatus.FAILED),  # not WRITTEN/SHADOWED: not attributed
        _entry(WritebackStatus.DEFERRED_OPEN_ORDER),  # never took effect: not attributed
    )

    def baseline_for(e):  # old_values present on the fixtures
        return e.old_values

    valued = {"PN1": _ECON}

    def econ_for(pn, location):
        return valued.get(pn)

    s = build_savings(ledger, baseline_for, econ_for, _RATES)
    # per-change total = -14.5833 + 64.6354 + 1.3333 = 51.3854 -> 51.39 quantized
    assert s.total_projected_applied == Decimal("51.39")
    assert s.total_projected_shadowed == Decimal("51.39")
    # total_projected is quantize-then-add (applied + shadowed), not quantize(applied +
    # shadowed): 51.39 + 51.39 = 102.78 exactly, preserving the printed identity
    # "applied + shadowed = total" even though 102.7708 unquantized would round to 102.77.
    assert s.total_projected == Decimal("102.78")
    assert s.changes_total == 2  # WRITTEN + SHADOWED only
    assert s.changes_valued == 2
    assert s.holding_cost_delta.amount == Decimal("-29.17")  # 2 × -14.5833 = -29.1667
    assert s.ordering_cost_delta.amount == Decimal("129.27")  # 2 × 64.6354 = 129.2708
    assert s.stockout_risk_delta.amount == Decimal("2.67")  # 2 × 1.3333 = 2.6667
    assert s.assumption_rates["holding_cost_rate"] == 0.25


def test_build_savings_counts_ordering_skips_on_ordering_component_only():
    ledger = (
        _entry(WritebackStatus.WRITTEN, old=_OLD),  # eoq=10 on both sides: not skipped
        _entry(WritebackStatus.WRITTEN, old=dict(_OLD, eoq=0)),  # eoq=0: skipped
    )
    s = build_savings(ledger, lambda e: e.old_values, lambda pn, loc: _ECON, _RATES)
    assert s.changes_valued == 2
    assert s.ordering_cost_delta.inputs["ordering_skipped"] == 1
    # the counter is ordering-specific, not smeared across the other components
    assert "ordering_skipped" not in s.holding_cost_delta.inputs
    assert "ordering_skipped" not in s.stockout_risk_delta.inputs


def test_build_savings_counts_unvalued_changes():
    ledger = (_entry(WritebackStatus.WRITTEN),)
    s = build_savings(ledger, lambda e: e.old_values, lambda pn, loc: None, _RATES)
    assert s.changes_total == 1
    assert s.changes_valued == 0
    assert s.total_projected == Decimal("0.00")


def test_build_savings_first_write_uses_baseline_for():
    ledger = (_entry(WritebackStatus.WRITTEN, old=None),)  # first agent write
    s = build_savings(ledger, lambda e: _OLD, lambda pn, loc: _ECON, _RATES)
    assert s.changes_valued == 1
    assert s.total_projected_applied == Decimal("51.39")


def test_build_savings_unresolvable_baseline_is_unvalued():
    ledger = (_entry(WritebackStatus.WRITTEN, old=None),)
    s = build_savings(ledger, lambda e: None, lambda pn, loc: _ECON, _RATES)
    assert s.changes_total == 1
    assert s.changes_valued == 0
