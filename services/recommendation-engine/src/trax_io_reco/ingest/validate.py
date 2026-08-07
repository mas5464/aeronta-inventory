"""Validation rules over parsed canonical rows (spec §5). Produces a flat
``list[IngestError]``; the ingest handler runs the engine ONLY when this list is empty —
a dirty upload must never partially seed a tenant's data.

``parsed`` maps canonical file name -> list of row dicts: lowercased canonical headers,
raw string values, exactly as Task 3's ``parse_csv``/``parse_xlsx``/``parse_uploads``
produce them. Row indices in returned errors are 0-based over that file's row list.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from trax_io_reco.contracts.repair import parse_repair_timestamp
from trax_io_reco.data.extract_loader import _DEFAULT_ESSENTIALITY_MAP, _parse_date
from trax_io_reco.ingest.canonical import CANONICAL_FILES, REQUIRED_FILES

# Columns that must parse as numbers when present (a blank/absent cell is tolerated —
# "required but missing" is a separate concern handled by the header/row-emptiness
# checks below; this check only flags a *non-empty* value that fails to parse).
_NUMERIC_COLUMNS: dict[str, tuple[str, ...]] = {
    "parts": ("unit_cost", "shelf_life_days"),
    "stock": (
        "on_hand",
        "allocated",
        "in_repair",
        "current_rop",
        "current_eoq",
        "current_safety_stock",
        "current_max",
    ),
    "demand_history": ("quantity",),
    "locations": (),
    "open_orders": ("quantity",),
    "requisitions": ("quantity",),
    "vendors": ("unit_price", "lead_time_days", "min_order_qty"),
    "repair_history": ("quantity",),
}

_NONNEGATIVE_INTEGER_COLUMNS: dict[str, frozenset[str]] = {
    "parts": frozenset({"shelf_life_days"}),
    "stock": frozenset(
        {
            "on_hand",
            "allocated",
            "in_repair",
            "current_rop",
            "current_eoq",
            "current_safety_stock",
            "current_max",
        }
    ),
    "demand_history": frozenset({"quantity"}),
    "open_orders": frozenset({"quantity"}),
    "requisitions": frozenset({"quantity"}),
    "vendors": frozenset({"min_order_qty"}),
    "repair_history": frozenset({"quantity"}),
}

_POSITIVE_COLUMNS: dict[str, frozenset[str]] = {
    "parts": frozenset({"unit_cost"}),
    "requisitions": frozenset({"quantity"}),
    "vendors": frozenset({"unit_price", "lead_time_days", "min_order_qty"}),
    "repair_history": frozenset({"quantity"}),
}

# Columns that must parse via the engine's `_parse_date` (ISO or MM/DD/YYYY) when present.
_DATE_COLUMNS: dict[str, tuple[str, ...]] = {
    "parts": (),
    "stock": (),
    "demand_history": ("period", "observation_start", "observation_end"),
    "demand_window": ("observation_start", "observation_end"),
    "locations": (),
    "open_orders": ("expected_date",),
    "requisitions": ("need_by",),
    "vendors": (),
    "repair_history": (),
}

_TIMESTAMP_COLUMNS: dict[str, tuple[str, ...]] = {
    "open_orders": ("opened_at",),
    "repair_history": ("started_at", "completed_at"),
}

_REPAIR_STATUSES = {
    "completed",
    "closed",
    "cancelled",
    "scrapped",
    "condemned",
}
_REPAIR_OUTCOMES = {
    "serviceable",
    "unserviceable",
    "scrapped",
    "condemned",
}

# Files whose rows reference a `parts` row (and a `locations` row, when provided) by
# (part_number, location_code).
_REFERENCING_FILES = (
    "stock",
    "demand_history",
    "open_orders",
    "requisitions",
    "vendors",
    "repair_history",
)


@dataclass(frozen=True)
class IngestError:
    """One validation failure. ``row``/``column`` are ``None`` for file-level errors
    (missing file, missing column, quota) and set for row-level errors."""

    file: str
    row: int | None
    column: str | None
    message: str


def _as_decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


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
        if not rows and not spec.required:
            # Presence of an optional file with headers but no data rows is the
            # canonical observed-empty signal. The row-only parsed contract does
            # not retain header metadata, so file presence is authoritative.
            continue
        headers: set[str] = set()
        for row in rows:
            headers.update(row.keys())
        for col in spec.required_columns:
            if col not in headers:
                errors.append(IngestError(name, None, col, f"missing required column '{col}'"))

    # 2. per-row typing: every required column (per that file's canonical spec) must be
    # non-empty in every row — independent of the numeric/date parse checks below, so a
    # blank required cell is never silently tolerated. Numeric/date columns are parse-
    # checked only when non-empty; a required column that is also numeric/date-typed
    # reports just the "required" error when empty (the parse check is skipped for
    # empty values, so it never double-reports the same blank cell).
    for name, rows in parsed.items():
        spec = CANONICAL_FILES.get(name)
        if spec is None:
            continue
        numeric_cols = _NUMERIC_COLUMNS.get(name, ())
        date_cols = _DATE_COLUMNS.get(name, ())
        timestamp_cols = _TIMESTAMP_COLUMNS.get(name, ())
        required_cols = spec.required_columns
        for i, row in enumerate(rows):
            for col in required_cols:
                val = row.get(col)
                if val is None or str(val).strip() == "":
                    msg = f"'{col}' is required and must not be empty"
                    errors.append(IngestError(name, i, col, msg))

            for col in numeric_cols:
                val = row.get(col)
                if val is None or str(val).strip() == "":
                    continue  # empty cell tolerated — required-emptiness reported above
                number = _as_decimal(val)
                if number is None:
                    errors.append(IngestError(name, i, col, f"'{val}' in '{col}' is not numeric"))
                    continue
                if col in _NONNEGATIVE_INTEGER_COLUMNS.get(name, frozenset()):
                    if number != number.to_integral_value():
                        errors.append(
                            IngestError(
                                name,
                                i,
                                col,
                                f"'{val}' in '{col}' must be an integer",
                            )
                        )
                        continue
                    if number < 0:
                        errors.append(
                            IngestError(
                                name,
                                i,
                                col,
                                f"'{val}' in '{col}' must be non-negative",
                            )
                        )
                        continue
                if col in _POSITIVE_COLUMNS.get(name, frozenset()) and number <= 0:
                    errors.append(
                        IngestError(
                            name,
                            i,
                            col,
                            f"'{val}' in '{col}' must be positive",
                        )
                    )

            for col in date_cols:
                val = row.get(col)
                if val is None or str(val).strip() == "":
                    continue  # empty cell tolerated — required-emptiness reported above
                if _parse_date(val) is None:
                    msg = f"'{val}' in '{col}' is not a valid date"
                    errors.append(IngestError(name, i, col, msg))

            for col in timestamp_cols:
                val = row.get(col)
                if val is None or str(val).strip() == "":
                    continue
                if parse_repair_timestamp(val) is None:
                    errors.append(
                        IngestError(
                            name,
                            i,
                            col,
                            f"'{val}' in '{col}' is not a valid ISO timestamp",
                        )
                    )

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
            if name == "requisitions" and locations_provided:
                alt_location = row.get("alt_source_location")
                if (
                    alt_location
                    and alt_location not in location_keys
                ):
                    errors.append(
                        IngestError(
                            name,
                            i,
                            "alt_source_location",
                            f"location_code '{alt_location}' not found in locations",
                        )
                    )

    # Requisition IDs identify source lines and must remain unambiguous so
    # scheduled-demand evidence can cite a stable source_ref.
    requisition_ids: dict[str, int] = {}
    for i, row in enumerate(parsed.get("requisitions", [])):
        raw_id = row.get("requisition_id")
        if raw_id is None or str(raw_id).strip() == "":
            continue  # Required-cell validation already reports this.
        requisition_id = str(raw_id).strip()
        if requisition_id in requisition_ids:
            errors.append(
                IngestError(
                    "requisitions",
                    i,
                    "requisition_id",
                    f"duplicate requisition_id '{requisition_id}'",
                )
            )
        else:
            requisition_ids[requisition_id] = i

    # Repair terminal identity and lifecycle semantics. One line produces at
    # most one terminal observation; duplicates are quarantinable validation
    # failures, never silently double-counted TAT samples.
    repair_terminal_ids: dict[tuple[str, str], int] = {}
    for i, row in enumerate(parsed.get("repair_history", [])):
        order_id = str(row.get("repair_order_id") or "").strip()
        line_id = str(row.get("repair_line_id") or "").strip()
        identity = (order_id, line_id)
        if order_id and line_id:
            if identity in repair_terminal_ids:
                errors.append(
                    IngestError(
                        "repair_history",
                        i,
                        "repair_line_id",
                        "duplicate terminal event for "
                        f"repair_order_id '{order_id}' line '{line_id}'",
                    )
                )
            else:
                repair_terminal_ids[identity] = i

        status = str(row.get("status") or "").strip().lower()
        outcome = str(row.get("outcome") or "").strip().lower()
        if status and status not in _REPAIR_STATUSES:
            errors.append(
                IngestError(
                    "repair_history",
                    i,
                    "status",
                    f"unknown terminal repair status '{row.get('status')}'",
                )
            )
        if outcome and outcome not in _REPAIR_OUTCOMES:
            errors.append(
                IngestError(
                    "repair_history",
                    i,
                    "outcome",
                    f"unknown repair outcome '{row.get('outcome')}'",
                )
            )

        started = parse_repair_timestamp(row.get("started_at"))
        completed = parse_repair_timestamp(row.get("completed_at"))
        if started is not None and completed is not None and completed < started:
            errors.append(
                IngestError(
                    "repair_history",
                    i,
                    "completed_at",
                    "completed_at must not precede started_at",
                )
            )

        quantity = _as_decimal(row.get("quantity"))
        serial = str(row.get("serial_number") or "").strip()
        if serial and quantity is not None and quantity != 1:
            errors.append(
                IngestError(
                    "repair_history",
                    i,
                    "quantity",
                    "a serial-number observation must have quantity 1",
                )
            )
        if status == "cancelled" and outcome:
            errors.append(
                IngestError(
                    "repair_history",
                    i,
                    "outcome",
                    "cancelled repair cannot carry a completion outcome",
                )
            )
        if status == "scrapped" and outcome not in {"", "scrapped"}:
            errors.append(
                IngestError(
                    "repair_history",
                    i,
                    "outcome",
                    "scrapped status contradicts the supplied outcome",
                )
            )
        if status == "condemned" and outcome not in {"", "condemned"}:
            errors.append(
                IngestError(
                    "repair_history",
                    i,
                    "outcome",
                    "condemned status contradicts the supplied outcome",
                )
            )

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

    # 3c. Every stock part needs a positive commercial cost source. A canonical
    # vendor row wins when supplied; otherwise the mapper deliberately derives the
    # DEFAULT vendor from parts.unit_cost.
    vendor_pns = {
        row.get("part_number")
        for row in parsed.get("vendors", [])
        if row.get("part_number")
        and (price := _as_decimal(row.get("unit_price"))) is not None
        and price > 0
    }
    part_rows_by_pn = {
        row.get("part_number"): (index, row)
        for index, row in enumerate(parsed.get("parts", []))
        if row.get("part_number")
    }
    stock_pns = {
        row.get("part_number")
        for row in parsed.get("stock", [])
        if row.get("part_number")
    }
    for pn in sorted(stock_pns - vendor_pns):
        part_entry = part_rows_by_pn.get(pn)
        if part_entry is None:
            continue  # The referential rule already reports the missing part.
        index, part_row = part_entry
        raw_cost = part_row.get("unit_cost")
        if raw_cost is None or str(raw_cost).strip() == "":
            errors.append(
                IngestError(
                    "parts",
                    index,
                    "unit_cost",
                    "a positive unit_cost is required when no vendor price serves "
                    f"stock part '{pn}'",
                )
            )

    # 3d. Demand exposure is always an explicit, consistent closed interval.
    # Non-empty history may repeat it on every row for backwards compatibility,
    # or one `demand_window` metadata row may define it once. The metadata form is
    # required for a genuinely observed-empty history because there are no demand
    # rows on which to carry the bounds.
    demand_rows = parsed.get("demand_history", [])
    window_rows = parsed.get("demand_window", [])
    file_window: tuple[object, object] | None = None
    if "demand_window" in parsed:
        if "demand_history" not in parsed:
            errors.append(
                IngestError(
                    "demand_window",
                    None,
                    None,
                    "demand_window requires a supplied demand_history file",
                )
            )
        if len(window_rows) != 1:
            errors.append(
                IngestError(
                    "demand_window",
                    None,
                    None,
                    "demand_window must contain exactly one row",
                )
            )
        else:
            start = _parse_date(window_rows[0].get("observation_start"))
            end = _parse_date(window_rows[0].get("observation_end"))
            if start is not None and end is not None:
                if end < start:
                    errors.append(
                        IngestError(
                            "demand_window",
                            0,
                            "observation_end",
                            "observation bounds must form a valid closed interval",
                        )
                    )
                else:
                    file_window = (start, end)

    row_windows: set[tuple[object, object]] = set()
    rows_with_window = 0
    for i, row in enumerate(demand_rows):
        raw_start = row.get("observation_start")
        raw_end = row.get("observation_end")
        has_start = raw_start is not None and str(raw_start).strip() != ""
        has_end = raw_end is not None and str(raw_end).strip() != ""
        if has_start != has_end:
            errors.append(
                IngestError(
                    "demand_history",
                    i,
                    "observation_start" if not has_start else "observation_end",
                    "observation_start and observation_end must both be supplied",
                )
            )
            continue
        if not has_start:
            continue
        rows_with_window += 1
        start = _parse_date(raw_start)
        end = _parse_date(raw_end)
        if start is None or end is None:
            continue  # The generic date parser already emitted the precise column error.
        if end < start:
            errors.append(
                IngestError(
                    "demand_history",
                    i,
                    "observation_end",
                    "observation bounds must form a valid closed interval",
                )
            )
            continue
        row_windows.add((start, end))

    row_window = (
        next(iter(row_windows))
        if rows_with_window == len(demand_rows) and len(row_windows) == 1
        else None
    )
    if rows_with_window not in {0, len(demand_rows)} or len(row_windows) > 1:
        errors.append(
            IngestError(
                "demand_history",
                None,
                None,
                "all demand_history rows must use the same observation window",
            )
        )
    if file_window is not None and row_window is not None and file_window != row_window:
        errors.append(
            IngestError(
                "demand_window",
                None,
                None,
                "demand_window must match the observation bounds on demand_history rows",
            )
        )

    resolved_window = file_window or row_window
    if "demand_history" in parsed and resolved_window is None:
        errors.append(
            IngestError(
                "demand_history",
                None,
                None,
                "demand_history requires an explicit closed observation window",
            )
        )
    if resolved_window is not None:
        start, end = resolved_window
        for i, row in enumerate(demand_rows):
            period = _parse_date(row.get("period"))
            if period is not None and not start <= period <= end:
                errors.append(
                    IngestError(
                        "demand_history",
                        i,
                        "period",
                        "period must fall inside the observation closed interval",
                    )
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
                    "stock",
                    None,
                    None,
                    f"{n} keys exceeds your plan limit of {key_quota}; contact support to "
                    "raise your quota or reduce the upload",
                )
            )

    return errors
