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
    scope_key: str | None = None
    """How this domain's output can be scoped to a station + part-cap subset.

    ``"part_location"`` — output exposes both ``hostpartid`` and ``hostlocid``
    (scopable on part list AND station). ``"part"`` — output exposes only
    ``hostpartid`` (scopable on part list only, no location key to filter on).
    ``None`` — small reference domain with neither key; always extracted
    unscoped. Verified against each domain's ``sql/NN_*.sql`` output aliases,
    not guessed — see task W1-2.
    """


DOMAINS: tuple[Domain, ...] = (
    Domain(
        1,
        "causal_values",
        "01_causal_values.sql",
        True,
        ("start_date", "end_date"),
        scope_key=None,  # output keys: HostProductID + HostLocID (no hostpartid)
    ),
    Domain(
        2,
        "demand_history_rotables",
        "02_demand_history_rotables.sql",
        True,
        ("from_date", "to_date"),
        scope_key="part_location",
    ),
    Domain(
        3,
        "demand_history_expendables",
        "03_demand_history_expendables.sql",
        True,
        ("from_date", "to_date"),
        scope_key="part_location",
    ),
    Domain(
        4,
        "events",
        "04_events.sql",
        True,
        ("as_of_date", "transaction"),
        scope_key="part_location",
    ),
    Domain(5, "location_master", "05_location_master.sql", False, (), scope_key=None),
    Domain(6, "location_type", "06_location_type.sql", False, (), scope_key=None),
    Domain(
        7,
        "order_plan_closed_orders",
        "07_order_plan_closed_orders.sql",
        False,
        (),
        scope_key="part_location",
    ),
    Domain(8, "order_plan", "08_order_plan.sql", False, (), scope_key="part_location"),
    Domain(
        9,
        "order_plan_data_requisition",
        "09_order_plan_data_requisition.sql",
        False,
        (),
        scope_key="part_location",
    ),
    Domain(10, "part_chain", "10_part_chain.sql", False, (), scope_key=None),
    Domain(
        11,
        "part_chain_details",
        "11_part_chain_details.sql",
        False,
        (),
        scope_key="part",
    ),
    Domain(12, "part_criticality", "12_part_criticality.sql", False, (), scope_key=None),
    Domain(13, "part_kit_bom", "13_part_kit_bom.sql", False, (), scope_key="part"),
    Domain(14, "part_location", "14_part_location.sql", False, (), scope_key="part_location"),
    Domain(15, "part_master", "15_part_master.sql", False, (), scope_key="part"),
    Domain(
        16,
        "pn_vendor_price",
        "16_pn_vendor_price.sql",
        False,
        (),
        scope_key="part_location",
    ),
    Domain(17, "sales_order", "17_sales_order.sql", False, (), scope_key="part_location"),
    Domain(18, "stock_amount", "18_stock_amount.sql", False, (), scope_key="part_location"),
    Domain(
        19,
        "stock_level_upload",
        "19_stock_level_upload.sql",
        False,
        (),
        scope_key="part_location",
    ),
    Domain(20, "trans_code", "20_trans_code.sql", False, (), scope_key=None),
    Domain(21, "vendor", "21_vendor.sql", False, (), scope_key=None),
)


DOMAINS_BY_NAME: dict[str, Domain] = {d.name: d for d in DOMAINS}


def get_domain(name: str) -> Domain:
    """Return the registry entry for ``name`` or raise ``KeyError``."""
    return DOMAINS_BY_NAME[name]
