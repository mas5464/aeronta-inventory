"""Assert the 21 SQL files exist, are non-empty, and carry the expected
bind variables for windowed domains.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trax_io_extract.domains import DOMAINS

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


@pytest.mark.parametrize("domain", DOMAINS, ids=[d.name for d in DOMAINS])
def test_sql_file_exists_and_non_empty(domain) -> None:  # type: ignore[no-untyped-def]
    path = SQL_DIR / domain.sql_file
    assert path.is_file(), f"missing SQL file {path}"
    content = path.read_text()
    assert content.strip(), f"empty SQL file {path}"
    # Header comment convention.
    assert content.startswith("-- Domain:"), f"missing header in {path}"
    assert f"Domain: {domain.name}" in content.splitlines()[0]


@pytest.mark.parametrize(
    "domain",
    [d for d in DOMAINS if d.date_windowed],
    ids=[d.name for d in DOMAINS if d.date_windowed],
)
def test_windowed_sql_has_bind_vars(domain) -> None:  # type: ignore[no-untyped-def]
    path = SQL_DIR / domain.sql_file
    content = path.read_text()
    for bind in domain.bind_vars:
        assert f":{bind}" in content, f"bind :{bind} missing from {path}"


def test_no_legacy_string_placeholders() -> None:
    """The legacy string-literal placeholders must not appear anywhere."""
    legacy = [
        "' startDate '",
        "' endDate   '",
        "'endDate'",
        "' fromDate '",
        "'  toDate '",
        "' date '",
        "' transaction '",
    ]
    for path in SQL_DIR.glob("*.sql"):
        content = path.read_text()
        for placeholder in legacy:
            assert placeholder not in content, (
                f"legacy placeholder {placeholder!r} still in {path.name}"
            )


@pytest.mark.parametrize(
    ("sql_file", "timestamp_column"),
    [
        ("02_demand_history_rotables.sql", "apn.transaction_date"),
        ("03_demand_history_expendables.sql", "apn.CREATED_DATE"),
    ],
)
def test_demand_sql_treats_to_date_as_inclusive_calendar_day(
    sql_file: str,
    timestamp_column: str,
) -> None:
    """A closed date bind is a half-open timestamp predicate in Oracle SQL."""

    normalized = " ".join((SQL_DIR / sql_file).read_text().split()).lower()
    column = timestamp_column.lower()
    assert f"{column} >= :from_date" in normalized
    assert f"{column} < :to_date + 1" in normalized
    assert f"{column} <= :to_date" not in normalized


def test_closed_orders_selects_explicit_order_type_classifier() -> None:
    """Closed PO/RO rows expose their source lane as an authoritative field."""

    sql = (SQL_DIR / "07_order_plan_closed_orders.sql").read_text()
    assert re.search(r"\bd\.order_type\s+as\s+ordertypeid\b", sql, re.IGNORECASE)


def test_closed_orders_allows_purchase_and_repair_lanes() -> None:
    """The closed-order population continues to include both PO and RO rows."""

    sql = (SQL_DIR / "07_order_plan_closed_orders.sql").read_text()
    lane_filter = re.search(
        r"\bd\.order_type\s+in\s*\((?P<lanes>[^)]*)\)",
        sql,
        re.IGNORECASE,
    )
    assert lane_filter
    lanes = {lane.upper() for lane in re.findall(r"'([^']+)'", lane_filter["lanes"])}
    assert lanes == {"PO", "RO"}


@pytest.mark.parametrize("alias", ["HostOrderID", "OrderID"])
def test_closed_orders_retains_legacy_order_identifiers(alias: str) -> None:
    """Legacy consumers keep their order-type/number/line composite IDs."""

    sql = (SQL_DIR / "07_order_plan_closed_orders.sql").read_text()
    composite = (
        r"d\.order_type\s*\|\|\s*'_'\s*\|\|\s*d\.order_number"
        r"\s*\|\|\s*'_'\s*\|\|\s*d\.order_line"
    )
    assert re.search(rf"{composite}\s+as\s+{alias}\b", sql, re.IGNORECASE)


def test_open_orders_separate_stable_order_and_line_identities() -> None:
    sql = (SQL_DIR / "08_order_plan.sql").read_text()
    stable_order = (
        r"a\.order_type\s*\|\|\s*'_'\s*\|\|\s*a\.order_number"
    )
    legacy_composite = (
        rf"{stable_order}\s*\|\|\s*'_'\s*\|\|\s*b\.order_line"
    )

    assert re.search(
        rf"{stable_order}\s+as\s+hostorderid\b",
        sql,
        re.IGNORECASE,
    )
    assert re.search(
        r"\bb\.order_line\s+as\s+orderlineid\b",
        sql,
        re.IGNORECASE,
    )
    assert re.search(
        rf"{legacy_composite}\s+as\s+orderid\b",
        sql,
        re.IGNORECASE,
    )
    assert not re.search(
        rf"{legacy_composite}\s+as\s+hostorderid\b",
        sql,
        re.IGNORECASE,
    )


def test_open_orders_selects_explicit_order_type_classifier() -> None:
    sql = (SQL_DIR / "08_order_plan.sql").read_text()
    assert re.search(
        r"\ba\.order_type\s+as\s+ordertypeid\b",
        sql,
        re.IGNORECASE,
    )


def test_open_orders_emit_authoritative_repair_lifecycle_evidence_only() -> None:
    sql = (SQL_DIR / "08_order_plan.sql").read_text()

    assert re.search(
        r"\ba\.created_date\s+as\s+planorderdate\b",
        sql,
        re.IGNORECASE,
    )
    assert re.search(
        r"\bb\.status\s+as\s+orderstatus\b",
        sql,
        re.IGNORECASE,
    )
    assert re.search(
        r"\ba\.relation_code\s+as\s+hostshopid\b",
        sql,
        re.IGNORECASE,
    )
    assert not re.search(r"\bas\s+serialnumber\b", sql, re.IGNORECASE)


def test_open_orders_keep_terminal_repair_evidence_without_broadening_po_receipts() -> None:
    sql = (SQL_DIR / "08_order_plan.sql").read_text()
    normalized = " ".join(sql.lower().split())

    assert re.search(
        r"upper\(trim\(a\.order_type\)\)\s*=\s*'po'"
        r".*?upper\(trim\(b\.status\)\)\s*=\s*'open'",
        normalized,
    )
    repair_branch = re.search(
        r"or\s*\(\s*upper\(trim\(a\.order_type\)\)\s*=\s*'ro'"
        r"(?P<body>.*?)\)\s*order\s+by",
        normalized,
    )
    assert repair_branch
    assert "upper(trim(b.status))" not in repair_branch["body"]
    assert re.search(
        r"nvl\(b\.qty_require,\s*0\)\s*>\s*nvl\(b\.qty_received,\s*0\)",
        repair_branch["body"],
    )
