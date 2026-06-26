from __future__ import annotations

from datetime import date

from trax_io_feature_store import InMemoryFeatureStore, TenantContext

from tests.fixtures.builders import seed_part
from trax_io_reco.contracts.context import ScheduledDemandItem
from trax_io_reco.contracts.enums import EvidenceKind, Regime
from trax_io_reco.data.assembler import ContextAssembler
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.demand.projection import HistoricalScheduledProjector

TENANT = TenantContext(tenant_id="acme")


def _ctx(**kw):
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P", location="L", **kw)
    return ContextAssembler(features=FeatureReader(fs), inventory_state=inv).assemble(
        tenant=TENANT, pn="P", location="L"
    )


def test_intermittent_is_compound_poisson() -> None:
    ctx = _ctx(monthly_units=[1, 0, 2, 0, 1, 0, 1])  # ~6 events / 24mo
    proj = HistoricalScheduledProjector().project(context=ctx, regime=Regime.INTERMITTENT)
    assert proj.dist_kind == "COMPOUND_POISSON"
    assert proj.mean_per_day > 0
    assert proj.dist_params["lambda"] > 0


def test_high_volume_is_normal() -> None:
    ctx = _ctx(monthly_units=[30] * 12)
    proj = HistoricalScheduledProjector().project(context=ctx, regime=Regime.HIGH_VOLUME)
    assert proj.dist_kind == "NORMAL"
    assert proj.std_per_day > 0


def test_scheduled_demand_itemized_by_aircraft_and_task() -> None:
    sched = [
        ScheduledDemandItem(due_date=date(2026, 5, 1), qty=3, source_ref="TC-100",
                            source_kind=EvidenceKind.TASK_CARD, ac_type="A320"),
        ScheduledDemandItem(due_date=date(2026, 5, 2), qty=2, source_ref="TC-101",
                            source_kind=EvidenceKind.TASK_CARD, ac_type="B737"),
    ]
    ctx = _ctx(monthly_units=[1, 1], scheduled=sched)
    proj = HistoricalScheduledProjector().project(context=ctx, regime=Regime.INTERMITTENT)
    assert proj.by_aircraft == {"A320": 3.0, "B737": 2.0}
    assert proj.by_task == {"TC-100": 3.0, "TC-101": 2.0}
    assert proj.scheduled_component > 0
