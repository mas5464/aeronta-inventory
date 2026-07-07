"""apps/web CSV export — the pure QueueRow -> CSV serializer (no HTTP)."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from trax_io_spine.bff.csv_export import CSV_COLUMNS, queue_rows_to_csv
from trax_io_spine.bff.models import QueueRow


def _row(**overrides) -> QueueRow:
    base = dict(
        recommendation_id="rec-1",
        pn="19000-231-3",
        location="YYC",
        type="purchase",
        criticality_tier=2,
        aog_risk_level=3,
        confidence_score=0.92,
        recommended_quantity=4,
        estimated_cost_impact=Decimal("-1200.00"),
        tier=2,
        priority_score=88.4,
        status="pending",
        reason="Projected shortage within lead time",
        approvable=True,
        description="WATER TANK HEATER BLANKET",
        current_stock=1,
        shortage_quantity=3,
        recommended_location=None,
        horizon_days=90,
    )
    base.update(overrides)
    return QueueRow(**base)


def test_csv_columns_are_the_14_canonical_columns_in_order():
    assert CSV_COLUMNS == (
        "recommendation_id", "pn", "location", "description", "type", "tier",
        "criticality_tier", "aog_risk_level", "confidence_score",
        "recommended_quantity", "estimated_cost_impact", "priority_score",
        "status", "reason",
    )


def test_header_plus_one_row_per_entry():
    csv_text = queue_rows_to_csv([_row(recommendation_id="a"), _row(recommendation_id="b")])
    parsed = list(csv.reader(io.StringIO(csv_text)))
    assert parsed[0] == list(CSV_COLUMNS)
    assert len(parsed) == 3  # header + 2 rows
    assert parsed[1][0] == "a"
    assert parsed[2][0] == "b"


def test_enum_and_decimal_cells_render_as_bare_values():
    parsed = list(csv.reader(io.StringIO(queue_rows_to_csv([_row()]))))
    header, data = parsed[0], parsed[1]
    cell = dict(zip(header, data))
    assert cell["type"] == "purchase"          # StrEnum
    assert cell["tier"] == "2"                  # IntEnum
    assert cell["aog_risk_level"] == "3"        # IntEnum
    assert cell["status"] == "pending"          # StrEnum
    assert cell["estimated_cost_impact"] == "-1200.00"  # Decimal


def test_comma_and_quote_in_reason_round_trip():
    tricky = 'Shortage, per vendor "ACME", within lead time'
    parsed = list(csv.reader(io.StringIO(queue_rows_to_csv([_row(reason=tricky)]))))
    cell = dict(zip(parsed[0], parsed[1]))
    assert cell["reason"] == tricky  # csv.reader un-escapes what csv.writer escaped


def test_empty_rows_yields_header_only():
    parsed = list(csv.reader(io.StringIO(queue_rows_to_csv([]))))
    assert parsed == [list(CSV_COLUMNS)]
