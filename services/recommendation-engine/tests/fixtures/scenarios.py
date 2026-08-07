"""The eight canonical acceptance scenarios (spec §9.1). Each builder seeds the real
InMemoryFeatureStore + InMemoryInventoryState and returns the work-list to run through
the full RecommendationService.
"""

from __future__ import annotations

from datetime import date

from trax_io_feature_store import InMemoryFeatureStore

from tests.fixtures.builders import interchange, seed_part
from trax_io_reco.contracts.context import RepairTat
from trax_io_reco.data.inventory_state import InMemoryInventoryState

TENANT_ID = "acme"
Scenario = tuple[InMemoryFeatureStore, InMemoryInventoryState, str, list[tuple[str, str]]]


def _stores() -> tuple[InMemoryFeatureStore, InMemoryInventoryState]:
    return InMemoryFeatureStore(), InMemoryInventoryState()


def scenario_1_demand_exceeds_stock() -> Scenario:
    fs, inv = _stores()
    seed_part(fs, inv, tenant_id=TENANT_ID, pn="P-1", location="YYZ", monthly_units=[20] * 12,
              serviceable=2, lead_mean_days=60.0, current_policy=(5, 5, 2, 40), tier=3,
              unit_cost="400")
    return fs, inv, TENANT_ID, [("P-1", "YYZ")]


def scenario_2_transfer_better() -> Scenario:
    fs, inv = _stores()
    seed_part(fs, inv, tenant_id=TENANT_ID, pn="P-2", location="YYZ", monthly_units=[20] * 12,
              serviceable=0, lead_mean_days=60.0, current_policy=(5, 5, 2, 40), tier=3)
    seed_part(fs, inv, tenant_id=TENANT_ID, pn="P-2", location="YOW", monthly_units=[0] * 12,
              serviceable=50, current_policy=(5, 5, 2, 10), tier=3)
    return fs, inv, TENANT_ID, [("P-2", "YYZ"), ("P-2", "YOW")]


def scenario_3_high_value_unused() -> Scenario:
    fs, inv = _stores()
    seed_part(fs, inv, tenant_id=TENANT_ID, pn="P-3", location="YYZ", monthly_units=[0] * 12,
              serviceable=100, current_policy=(2, 2, 1, 10), tier=4, unit_cost="8000")
    inv.seed(TENANT_ID, "scheduled_demand", ("P-3", "YYZ"), ())
    return fs, inv, TENANT_ID, [("P-3", "YYZ")]


def scenario_4_min_max_adjustment() -> Scenario:
    fs, inv = _stores()
    seed_part(fs, inv, tenant_id=TENANT_ID, pn="P-4", location="YYZ", monthly_units=[30] * 12,
              serviceable=20, current_policy=(1, 1, 0, 2), tier=4, unit_cost="200")
    return fs, inv, TENANT_ID, [("P-4", "YYZ")]


def scenario_5_interchangeable_available() -> Scenario:
    fs, inv = _stores()
    members = ["P-5A", "P-5B"]
    edges = [("P-5A", "P-5B", False), ("P-5B", "P-5A", False)]  # two-way
    seed_part(fs, inv, tenant_id=TENANT_ID, pn="P-5A", location="YYZ", monthly_units=[5] * 12,
              serviceable=0, current_policy=(5, 5, 2, 20), tier=3)
    seed_part(fs, inv, tenant_id=TENANT_ID, pn="P-5B", location="YYZ", monthly_units=[0] * 12,
              serviceable=50, current_policy=(5, 5, 2, 20), tier=3)
    for pn in members:
        fs.seed(TENANT_ID, "interchangeable_graph", (pn,),
                interchange(tenant_id=TENANT_ID, pn=pn, group_id="G5", members=members, edges=edges))
    return fs, inv, TENANT_ID, [("P-5A", "YYZ"), ("P-5B", "YYZ")]


def scenario_6_open_po_covers() -> Scenario:
    fs, inv = _stores()
    seed_part(fs, inv, tenant_id=TENANT_ID, pn="P-6", location="YYZ", monthly_units=[20] * 12,
              serviceable=2, lead_mean_days=60.0, current_policy=(5, 5, 2, 40), tier=3,
              open_qty=80, open_rcv_date=date(2026, 5, 1))
    return fs, inv, TENANT_ID, [("P-6", "YYZ")]


def scenario_7_location_specific_shortage() -> Scenario:
    fs, inv = _stores()
    seed_part(fs, inv, tenant_id=TENANT_ID, pn="P-7", location="YYZ", monthly_units=[20] * 12,
              serviceable=0, lead_mean_days=60.0, current_policy=(5, 5, 2, 40), tier=3)
    # YOW: stocked just above the actual 30-day demand from its represented
    # one-year history -> no excess donor and no location-specific shortage.
    seed_part(fs, inv, tenant_id=TENANT_ID, pn="P-7", location="YOW", monthly_units=[20] * 12,
              serviceable=20, current_policy=(5, 5, 2, 20), tier=3)
    return fs, inv, TENANT_ID, [("P-7", "YYZ"), ("P-7", "YOW")]


def scenario_8_long_tat_aog() -> Scenario:
    fs, inv = _stores()
    seed_part(fs, inv, tenant_id=TENANT_ID, pn="P-8", location="YYZ", monthly_units=[2] * 8,
              rotable=True, serviceable=0, tier=2, part_class="rotable", lead_mean_days=30.0,
              current_policy=(2, 2, 1, 5), unit_cost="20000",
              repair_tat=RepairTat(mean_days=40.0, p90_days=60.0, n_observations=8))
    return fs, inv, TENANT_ID, [("P-8", "YYZ")]


ALL_SCENARIOS = [
    scenario_1_demand_exceeds_stock,
    scenario_2_transfer_better,
    scenario_3_high_value_unused,
    scenario_4_min_max_adjustment,
    scenario_5_interchangeable_available,
    scenario_6_open_po_covers,
    scenario_7_location_specific_shortage,
    scenario_8_long_tat_aog,
]
