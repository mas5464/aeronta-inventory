"""Tests for the frozen domain registry."""

from __future__ import annotations

from trax_io_extract.domains import DOMAINS, DOMAINS_BY_NAME

EXPECTED_NAMES = [
    "causal_values",
    "demand_history_rotables",
    "demand_history_expendables",
    "events",
    "location_master",
    "location_type",
    "order_plan_closed_orders",
    "order_plan",
    "order_plan_data_requisition",
    "part_chain",
    "part_chain_details",
    "part_criticality",
    "part_kit_bom",
    "part_location",
    "part_master",
    "pn_vendor_price",
    "sales_order",
    "stock_amount",
    "stock_level_upload",
    "trans_code",
    "vendor",
]

EXPECTED_WINDOWED = {
    "causal_values",
    "demand_history_rotables",
    "demand_history_expendables",
    "events",
}


def test_registry_has_21_entries() -> None:
    assert len(DOMAINS) == 21


def test_registry_names_in_order() -> None:
    assert [d.name for d in DOMAINS] == EXPECTED_NAMES


def test_registry_positions_are_1_through_21() -> None:
    assert [d.position for d in DOMAINS] == list(range(1, 22))


def test_date_windowed_domains_exact() -> None:
    windowed = {d.name for d in DOMAINS if d.date_windowed}
    assert windowed == EXPECTED_WINDOWED


def test_snapshot_domains_have_no_binds() -> None:
    for d in DOMAINS:
        if not d.date_windowed:
            assert d.bind_vars == ()


def test_index_matches_tuple() -> None:
    for d in DOMAINS:
        assert DOMAINS_BY_NAME[d.name] is d
