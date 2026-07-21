"""Validation rules over parsed canonical rows (spec §5). Produces a flat
``list[IngestError]``; the ingest handler runs the engine ONLY when this list is empty —
a dirty upload must never partially seed a tenant's data.

``parsed`` maps canonical file name -> list of row dicts: lowercased canonical headers,
raw string values, exactly as Task 3's ``parse_csv``/``parse_xlsx``/``parse_uploads``
produce them. Row indices in returned errors are 0-based over that file's row list.
"""

from __future__ import annotations

from dataclasses import dataclass

from trax_io_reco.data.extract_loader import _DEFAULT_ESSENTIALITY_MAP, _parse_date
from trax_io_reco.ingest.canonical import CANONICAL_FILES, REQUIRED_FILES

# Columns that must parse as numbers when present (a blank/absent cell is tolerated —
# "required but missing" is a separate concern handled by the header/row-emptiness
# checks below; this check only flags a *non-empty* value that fails to parse).
_NUMERIC_COLUMNS: dict[str, tuple[str, ...]] = {
    "parts": ("unit_cost", "shelf_life_days"),
    "stock": (
        "on_hand", "allocated", "in_repair", "current_rop", "current_eoq",
        "current_safety_stock", "current_max",
    ),
    "demand_history": ("quantity",),
    "locations": (),
    "open_orders": ("quantity",),
    "vendors": ("unit_price", "lead_time_days", "min_order_qty"),
}

# Columns that must parse via the engine's `_parse_date` (ISO or MM/DD/YYYY) when present.
_DATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "parts": (),
    "stock": (),
    "demand_history": ("period",),
    "locations": (),
    "open_orders": ("expected_date",),
    "vendors": (),
}

# Columns that carry the (part_number, location_code) referential key; must be non-empty
# whenever they're a required column of that file.
_KEY_COLUMNS = ("part_number", "location_code")

# Files whose rows reference a `parts` row (and a `locations` row, when provided) by
# (part_number, location_code).
_REFERENCING_FILES = ("stock", "demand_history", "open_orders")


@dataclass(frozen=True)
class IngestError:
    """One validation failure. ``row``/``column`` are ``None`` for file-level errors
    (missing file, missing column, quota) and set for row-level errors."""

    file: str
    row: int | None
    column: str | None
    message: str


def _is_numeric(value: object) -> bool:
    try:
        float(str(value))
    except (TypeError, ValueError):
        return False
    return True


def validate(
    parsed: dict[str, list[dict]],
    *,
    key_quota: int | None = None,
    essentiality_map: dict[str, int] | None = None,
) -> list[IngestError]:
    errors: list[IngestError] = []
    emap = essentiality_map or _DEFAULT_ESSENTIALITY_MAP

    # 1a. required files present.
    for name in REQUIRED_FILES:
        if name not in parsed:
            errors.append(
                IngestError(name, None, None, f"'{name}' is a required file and was not provided")
            )

    # 1b. required columns present per provided file (header-level; row=None). Headers are
    # the union of keys actually present across that file's rows.
    for name, rows in parsed.items():
        spec = CANONICAL_FILES.get(name)
        if spec is None:
            continue
        headers: set[str] = set()
        for row in rows:
            headers.update(row.keys())
        for col in spec.required_columns:
            if col not in headers:
                errors.append(IngestError(name, None, col, f"missing required column '{col}'"))

    # 2. per-row typing: numeric columns parse (tolerant of empty), date columns parse via
    # `_parse_date`, and part_number/location_code are non-empty wherever required.
    for name, rows in parsed.items():
        spec = CANONICAL_FILES.get(name)
        if spec is None:
            continue
        numeric_cols = _NUMERIC_COLUMNS.get(name, ())
        date_cols = _DATE_COLUMNS.get(name, ())
        for i, row in enumerate(rows):
            for col in _KEY_COLUMNS:
                if col not in spec.required_columns:
                    continue
                val = row.get(col)
                if val is None or str(val).strip() == "":
                    errors.append(IngestError(name, i, col, f"'{col}' must not be empty"))

            for col in numeric_cols:
                val = row.get(col)
                if val is None or str(val).strip() == "":
                    continue  # empty cell tolerated — only non-empty non-numeric flagged
                if not _is_numeric(val):
                    errors.append(IngestError(name, i, col, f"'{val}' in '{col}' is not numeric"))

            for col in date_cols:
                val = row.get(col)
                if val is None or str(val).strip() == "":
                    continue  # empty cell tolerated
                if _parse_date(val) is None:
                    msg = f"'{val}' in '{col}' is not a valid date"
                    errors.append(IngestError(name, i, col, msg))

    # 3a. referential: every (part_number, location_code) in stock/demand_history/open_orders
    # references a parts row (and a locations row, when that file was provided).
    parts_keys = {
        row.get("part_number") for row in parsed.get("parts", []) if row.get("part_number")
    }
    locations_provided = "locations" in parsed
    location_keys = {
        row.get("location_code") for row in parsed.get("locations", []) if row.get("location_code")
    }
    for name in _REFERENCING_FILES:
        for i, row in enumerate(parsed.get(name, [])):
            pn = row.get("part_number")
            loc = row.get("location_code")
            if pn and pn not in parts_keys:
                errors.append(
                    IngestError(name, i, "part_number", f"part_number '{pn}' not found in parts")
                )
            if locations_provided and loc and loc not in location_keys:
                msg = f"location_code '{loc}' not found in locations"
                errors.append(IngestError(name, i, "location_code", msg))

    # 3b. criticality codes map via the essentiality map; unknown -> error, never silently
    # defaulted (design §4.3).
    for i, row in enumerate(parsed.get("parts", [])):
        raw = row.get("criticality")
        if raw is None or str(raw).strip() == "":
            continue
        if str(raw).strip().upper() not in emap:
            errors.append(
                IngestError("parts", i, "criticality", f"unknown criticality code '{raw}'")
            )

    # 4. quota: distinct (part_number, location_code) across stock over the tenant's limit.
    if key_quota is not None:
        stock_keys = {
            (row.get("part_number"), row.get("location_code"))
            for row in parsed.get("stock", [])
            if row.get("part_number") and row.get("location_code")
        }
        n = len(stock_keys)
        if n > key_quota:
            errors.append(
                IngestError(
                    "stock", None, None,
                    f"{n} keys exceeds your plan limit of {key_quota}; contact support to "
                    "raise your quota or reduce the upload",
                )
            )

    return errors
