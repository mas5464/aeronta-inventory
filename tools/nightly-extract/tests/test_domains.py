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

EXPECTED_PART_LOCATION_SCOPE = {
    "events",
    "part_location",
    "sales_order",
    "stock_level_upload",
}

EXPECTED_PART_SCOPE = {
    "demand_history_rotables",
    "demand_history_expendables",
    "order_plan_closed_orders",
    "order_plan",
    "order_plan_data_requisition",
    "part_chain_details",
    "part_kit_bom",
    "part_master",
    "pn_vendor_price",
    "stock_amount",
}

EXPECTED_NONE_SCOPE = {
    "causal_values",
    "location_master",
    "location_type",
    "part_chain",
    "part_criticality",
    "trans_code",
    "vendor",
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


def test_every_domain_has_a_valid_scope_key() -> None:
    valid = {None, "part", "part_location"}
    for d in DOMAINS:
        assert d.scope_key in valid, f"{d.name} has invalid scope_key {d.scope_key!r}"


def test_scope_key_assignment_matches_verified_sql_output_columns() -> None:
    part_location = {d.name for d in DOMAINS if d.scope_key == "part_location"}
    part_only = {d.name for d in DOMAINS if d.scope_key == "part"}
    none_scope = {d.name for d in DOMAINS if d.scope_key is None}

    assert part_location == EXPECTED_PART_LOCATION_SCOPE
    assert part_only == EXPECTED_PART_SCOPE
    assert none_scope == EXPECTED_NONE_SCOPE

    # Sanity: network-pooled model — poolable stock/demand domains pull the
    # part's network-wide data (scope_key="part"); pooling to planning keys
    # happens in the reco loader. The domains that DEFINE the planning keys
    # (the ROP/EOQ policy row and the interchange/location graph) stay
    # part_location-scopable.
    for name in ("stock_amount", "demand_history_rotables", "demand_history_expendables"):
        assert name in part_only
    for name in ("stock_level_upload", "part_location"):
        assert name in part_location

    assert len(part_location) + len(part_only) + len(none_scope) == len(DOMAINS)
