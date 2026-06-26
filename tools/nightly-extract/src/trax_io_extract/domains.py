"""Frozen registry of the 21 canonical extract domains.

Single source of truth for CLI, tests, and Phase 2 driver code. Do not
reorder — positions 1–4 are the date-windowed domains called out by the
ExtractManifest contract's atomicity rules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Domain:
    """One of the 21 raw landing domains."""

    position: int
    name: str
    sql_file: str
    date_windowed: bool
    bind_vars: tuple[str, ...]


DOMAINS: tuple[Domain, ...] = (
    Domain(1, "causal_values", "01_causal_values.sql", True, ("start_date", "end_date")),
    Domain(
        2,
        "demand_history_rotables",
        "02_demand_history_rotables.sql",
        True,
        ("from_date", "to_date"),
    ),
    Domain(
        3,
        "demand_history_expendables",
        "03_demand_history_expendables.sql",
        True,
        ("from_date", "to_date"),
    ),
    Domain(4, "events", "04_events.sql", True, ("as_of_date", "transaction")),
    Domain(5, "location_master", "05_location_master.sql", False, ()),
    Domain(6, "location_type", "06_location_type.sql", False, ()),
    Domain(
        7,
        "order_plan_closed_orders",
        "07_order_plan_closed_orders.sql",
        False,
        (),
    ),
    Domain(8, "order_plan", "08_order_plan.sql", False, ()),
    Domain(
        9,
        "order_plan_data_requisition",
        "09_order_plan_data_requisition.sql",
        False,
        (),
    ),
    Domain(10, "part_chain", "10_part_chain.sql", False, ()),
    Domain(11, "part_chain_details", "11_part_chain_details.sql", False, ()),
    Domain(12, "part_criticality", "12_part_criticality.sql", False, ()),
    Domain(13, "part_kit_bom", "13_part_kit_bom.sql", False, ()),
    Domain(14, "part_location", "14_part_location.sql", False, ()),
    Domain(15, "part_master", "15_part_master.sql", False, ()),
    Domain(16, "pn_vendor_price", "16_pn_vendor_price.sql", False, ()),
    Domain(17, "sales_order", "17_sales_order.sql", False, ()),
    Domain(18, "stock_amount", "18_stock_amount.sql", False, ()),
    Domain(19, "stock_level_upload", "19_stock_level_upload.sql", False, ()),
    Domain(20, "trans_code", "20_trans_code.sql", False, ()),
    Domain(21, "vendor", "21_vendor.sql", False, ()),
)


DOMAINS_BY_NAME: dict[str, Domain] = {d.name: d for d in DOMAINS}


def get_domain(name: str) -> Domain:
    """Return the registry entry for ``name`` or raise ``KeyError``."""
    return DOMAINS_BY_NAME[name]
