"""Mapper: parsed canonical rows (Task 3's ``parse_csv``/``parse_xlsx``/``parse_uploads``,
already validated by ``validate()``) -> the engine's existing eMRO-native extract dir shape
(``<domain>.json`` files of lowercased eMRO keys + ``manifest.json``) that
``trax_io_reco.data.extract_loader.build_stores_from_extract`` reads unchanged.

The canonical column -> eMRO key mapping below is the public connector contract (spec §4);
see ``docs/superpowers/specs/2026-07-21-c3-upload-intake-design.md``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

# canonical column -> eMRO key (or a tuple of eMRO keys, when one canonical column feeds
# more than one loader-read field).
_PARTS_MAP: dict[str, str | tuple[str, ...]] = {
    "part_number": "hostpartid",
    "description": "partdescription",
    "criticality": "hostpartcriticalid",
    "part_class": "hostparttypeid",
    "unit_cost": ("marketunitcost", "averagecost"),
    "repairable": "partrepairable",
    "shelf_life_days": "shelflife",
    "hazmat": "hazmat",
    "ata_chapter": "atachapter",
    "is_kit": "ispartkit",
}

_STOCK_BASE_MAP: dict[str, str | tuple[str, ...]] = {
    "part_number": "hostpartid",
    "location_code": "hostlocid",
    "on_hand": "onhandnew",
    "allocated": "allocated",
    "in_repair": "inrepair",
}

_STOCK_LEVEL_MAP: dict[str, str | tuple[str, ...]] = {
    **_STOCK_BASE_MAP,
    "current_rop": "rop",
    "current_eoq": "eoq",
    "current_safety_stock": "safetylevel",
    "current_max": "stockmax",
}

_DEMAND_MAP: dict[str, str | tuple[str, ...]] = {
    "part_number": "hostpartid",
    "location_code": "hostlocid",
    "quantity": "historyamount",
    "period": "historybegdate",
    "transaction_type": "transactiontype",
}

_LOCATIONS_MAP: dict[str, str | tuple[str, ...]] = {
    "location_code": "hostlocid",
    "parent_location_code": "hostparentlocid",
}

_OPEN_ORDERS_MAP: dict[str, str | tuple[str, ...]] = {
    "part_number": "hostpartid",
    "location_code": "hostlocid",
    "quantity": "planquantity",
    "expected_date": "planrcvdate",
    "order_type": "ordertypeid",
}

_VENDORS_MAP: dict[str, str | tuple[str, ...]] = {
    "part_number": "hostpartid",
    "vendor_code": "hostvendorlocid",
    "unit_price": "price",
    "lead_time_days": "processinglength",
    "min_order_qty": "minoq",
    "condition": "condition",
    "preferred": "preferred",
}


def _map_row(
    row: dict[str, Any],
    mapping: dict[str, str | tuple[str, ...]],
    *,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for col, keys in mapping.items():
        if col not in row:
            continue
        value = row[col]
        if isinstance(keys, tuple):
            for key in keys:
                mapped[key] = value
        else:
            mapped[keys] = value
    if extra:
        mapped.update(extra)
    return mapped


def _map_rows(
    rows: list[dict[str, Any]],
    mapping: dict[str, str | tuple[str, ...]],
    *,
    extra: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    return [_map_row(row, mapping, extra=extra) for row in rows]


def _write_domain(out_dir: Path, domain: str, rows: list[dict[str, Any]]) -> None:
    (out_dir / f"{domain}.json").write_text(json.dumps(rows))


def to_extract_dir(parsed: dict[str, list[dict]], out_dir: Path, *, tenant_id: str) -> None:
    """Write ``parsed`` canonical rows into ``out_dir`` as engine extract domain files.

    The three required domains (``part_master``, ``stock_amount``, ``stock_level_upload``)
    are always written — empty when their canonical source is absent, though ``parts``/
    ``stock`` are themselves required canonical files so in practice they're present by the
    time ``validate()`` has passed. Optional domain files are written only when their
    canonical source is present in ``parsed``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    parts = parsed.get("parts", [])
    stock = parsed.get("stock", [])

    _write_domain(out_dir, "part_master", _map_rows(parts, _PARTS_MAP))
    _write_domain(out_dir, "stock_amount", _map_rows(stock, _STOCK_BASE_MAP))
    _write_domain(out_dir, "stock_level_upload", _map_rows(stock, _STOCK_LEVEL_MAP))

    if "demand_history" in parsed:
        part_class_by_pn = {
            row["part_number"]: str(row.get("part_class") or "").strip().lower()
            for row in parts
            if row.get("part_number")
        }
        rotables: list[dict[str, Any]] = []
        expendables: list[dict[str, Any]] = []
        for row in parsed["demand_history"]:
            mapped = _map_row(row, _DEMAND_MAP)
            if part_class_by_pn.get(row.get("part_number")) == "rotable":
                rotables.append(mapped)
            else:
                expendables.append(mapped)
        _write_domain(out_dir, "demand_history_rotables", rotables)
        _write_domain(out_dir, "demand_history_expendables", expendables)

    if "locations" in parsed:
        _write_domain(out_dir, "location_master", _map_rows(parsed["locations"], _LOCATIONS_MAP))

    if "open_orders" in parsed:
        # Rows in this canonical file ARE the open orders (the loader otherwise filters
        # order_plan rows on orderstatus == "OPEN"), so stamp that status on every row.
        _write_domain(
            out_dir,
            "order_plan",
            _map_rows(parsed["open_orders"], _OPEN_ORDERS_MAP, extra={"orderstatus": "OPEN"}),
        )

    if "vendors" in parsed:
        _write_domain(out_dir, "pn_vendor_price", _map_rows(parsed["vendors"], _VENDORS_MAP))

    manifest = {"tenant_id": tenant_id, "extract_date": date.today().isoformat()}
    (out_dir / "manifest.json").write_text(json.dumps(manifest))
