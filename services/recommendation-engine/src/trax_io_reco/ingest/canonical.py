"""The canonical model v1 contract: six planner-friendly files a tenant uploads (CSV or
one sheet per file in a single .xlsx workbook), mapped downstream (Task 3's mapper) to the
engine's existing eMRO-native extract domains. Column names here ARE the public connector
spec — see spec §4 (docs/superpowers/specs/2026-07-21-c3-upload-intake-design.md).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalFile:
    """One canonical upload file's shape: which columns are mandatory vs. nice-to-have."""

    name: str
    required: bool
    required_columns: tuple[str, ...]
    optional_columns: tuple[str, ...]


# Required files: the engine cannot run without at least parts + stock (mirrors the
# loader's `_REQUIRED_DOMAINS`: part_master, stock_amount, stock_level_upload).
REQUIRED_FILES: tuple[str, ...] = ("parts", "stock")

CANONICAL_FILES: dict[str, CanonicalFile] = {
    "parts": CanonicalFile(
        name="parts",
        required=True,
        required_columns=("part_number",),
        optional_columns=(
            "description", "criticality", "part_class", "unit_cost", "repairable",
            "shelf_life_days", "hazmat", "ata_chapter", "is_kit",
        ),
    ),
    "stock": CanonicalFile(
        name="stock",
        required=True,
        required_columns=("part_number", "location_code", "on_hand"),
        optional_columns=(
            "allocated", "in_repair", "current_rop", "current_eoq",
            "current_safety_stock", "current_max",
        ),
    ),
    "demand_history": CanonicalFile(
        name="demand_history",
        required=False,
        required_columns=("part_number", "location_code", "period", "quantity"),
        optional_columns=("transaction_type",),
    ),
    "locations": CanonicalFile(
        name="locations",
        required=False,
        required_columns=("location_code",),
        optional_columns=("parent_location_code",),
    ),
    "open_orders": CanonicalFile(
        name="open_orders",
        required=False,
        required_columns=("part_number", "location_code", "quantity", "expected_date"),
        optional_columns=("order_type",),
    ),
    "vendors": CanonicalFile(
        name="vendors",
        required=False,
        required_columns=("part_number", "vendor_code", "unit_price", "lead_time_days"),
        optional_columns=("min_order_qty", "condition", "preferred"),
    ),
}
