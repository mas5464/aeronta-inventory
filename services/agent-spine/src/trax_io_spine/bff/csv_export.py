"""Pure QueueRow -> CSV serialization for the apps/web export route.

The 14-column set + order below is canonical here. Cells are the bare
str() of each value: StrEnum/IntEnum fields stringify to their value
("pending", "3"), Decimal to its numeric string — a flat, spreadsheet-
friendly rendering of each field.
"""

from __future__ import annotations

import csv
import io

from trax_io_spine.bff.models import QueueRow

CSV_COLUMNS: tuple[str, ...] = (
    "recommendation_id",
    "pn",
    "location",
    "description",
    "type",
    "tier",
    "criticality_tier",
    "aog_risk_level",
    "confidence_score",
    "recommended_quantity",
    "estimated_cost_impact",
    "priority_score",
    "status",
    "reason",
)


def queue_rows_to_csv(rows: list[QueueRow]) -> str:
    """Serialize queue rows to a CSV document (header + one line per row)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([str(getattr(row, col)) for col in CSV_COLUMNS])
    return buffer.getvalue()
