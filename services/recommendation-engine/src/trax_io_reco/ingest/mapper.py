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

from trax_io_reco.contracts.repair import parse_repair_timestamp
from trax_io_reco.data.extract_loader import _parse_date
from trax_io_reco.ingest.repair import (
    observation_from_row,
    repair_history_coverage,
)

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
    "order_id": "hostorderid",
    "order_line_id": "orderlineid",
    "vendor_code": "hostvendorlocid",
    "shop_code": "hostshopid",
    "opened_at": "planorderdate",
    "status": "orderstatus",
    "serial_number": "serialnumber",
}

_REQUISITIONS_MAP: dict[str, str | tuple[str, ...]] = {
    "requisition_id": "hostorderid",
    "part_number": "hostpartid",
    "location_code": "hostlocid",
    "quantity": "planquantity",
    "need_by": "planrcvdate",
    "alt_source_location": "hostreplsourcelocid",
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

_UNDATED_CANONICAL_SNAPSHOT = date(1970, 1, 1)


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


def _demand_window(parsed: dict[str, list[dict]]) -> tuple[date, date]:
    """Resolve the required canonical demand interval defensively.

    Non-empty uploads may repeat the interval on each history row. A one-row
    ``demand_window`` file defines it once and is the only way an empty demand
    upload can preserve observed exposure.
    """

    windows: set[tuple[date, date]] = set()
    unconfigured = 0
    demand_rows = parsed.get("demand_history", [])
    for row in demand_rows:
        raw_start = row.get("observation_start")
        raw_end = row.get("observation_end")
        has_start = raw_start is not None and str(raw_start).strip() != ""
        has_end = raw_end is not None and str(raw_end).strip() != ""
        if not has_start and not has_end:
            unconfigured += 1
            continue
        if has_start != has_end:
            raise ValueError("canonical demand observation bounds must be paired")
        start, end = _parse_date(raw_start), _parse_date(raw_end)
        if start is None or end is None or end < start:
            raise ValueError("canonical demand observation bounds must be a valid closed interval")
        windows.add((start, end))
    if windows and (unconfigured or len(windows) != 1):
        raise ValueError("canonical demand rows must use one consistent observation window")

    row_window = next(iter(windows)) if windows else None
    window_rows = parsed.get("demand_window")
    file_window: tuple[date, date] | None = None
    if window_rows is not None:
        if len(window_rows) != 1:
            raise ValueError("canonical demand_window must contain exactly one row")
        start = _parse_date(window_rows[0].get("observation_start"))
        end = _parse_date(window_rows[0].get("observation_end"))
        if start is None or end is None or end < start:
            raise ValueError("canonical demand_window must be a valid closed interval")
        file_window = (start, end)
    if row_window is not None and file_window is not None and row_window != file_window:
        raise ValueError("canonical demand_window must match demand row observation bounds")
    resolved = file_window or row_window
    if resolved is None:
        raise ValueError("canonical demand_history requires an explicit closed observation window")
    start, end = resolved
    for row in demand_rows:
        period = _parse_date(row.get("period"))
        if period is None or not start <= period <= end:
            raise ValueError("canonical demand period must fall inside the observation window")
    return resolved


def _resolve_snapshot_as_of(
    parsed: dict[str, list[dict]],
    *,
    demand_window: tuple[date, date] | None,
    snapshot_as_of: date | None,
) -> tuple[date, str]:
    """Choose an immutable canonical snapshot date without consulting wall time."""

    if snapshot_as_of is not None:
        return snapshot_as_of, "explicit"
    repair_completions = [
        completion.date()
        for row in parsed.get("repair_history", [])
        if (
            completion := parse_repair_timestamp(row.get("completed_at"))
        )
        is not None
    ]
    if demand_window is not None:
        if repair_completions:
            return (
                max(demand_window[1], *repair_completions),
                "latest_canonical_observation",
            )
        return demand_window[1], "demand_observation_end"
    demand_periods = [
        parsed_period
        for row in parsed.get("demand_history", [])
        if (parsed_period := _parse_date(row.get("period"))) is not None
    ]
    if demand_periods:
        if repair_completions:
            return (
                max(*demand_periods, *repair_completions),
                "latest_canonical_observation",
            )
        return max(demand_periods), "latest_demand_period"
    if repair_completions:
        return max(repair_completions), "latest_repair_completion"
    # Required canonical parts/stock files contain no source as-of date. Use a
    # stable, labeled sentinel rather than making identical bytes vary by wall
    # clock. Production callers should pass their immutable upload/job date.
    return _UNDATED_CANONICAL_SNAPSHOT, "undated_canonical_input"


def to_extract_dir(
    parsed: dict[str, list[dict]],
    out_dir: Path,
    *,
    tenant_id: str,
    snapshot_as_of: date | None = None,
) -> None:
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

    demand_window: tuple[date, date] | None = None
    artifacts: list[dict[str, Any]] = []
    if "demand_history" in parsed:
        demand_window = _demand_window(parsed)
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
        bind_vars = (
            {
                "from_date": demand_window[0].isoformat(),
                "to_date": demand_window[1].isoformat(),
            }
            if demand_window
            else {}
        )
        artifacts.extend(
            [
                {
                    "domain": domain,
                    "status": "succeeded",
                    "bind_vars": bind_vars,
                }
                for domain in (
                    "demand_history_rotables",
                    "demand_history_expendables",
                )
            ]
        )

    if "locations" in parsed:
        _write_domain(out_dir, "location_master", _map_rows(parsed["locations"], _LOCATIONS_MAP))
        artifacts.append({"domain": "location_master", "status": "succeeded"})

    if "open_orders" in parsed:
        # Rows in this canonical file ARE the open orders (the loader otherwise filters
        # order_plan rows on orderstatus == "OPEN"). Default a missing status to OPEN,
        # but preserve an explicitly supplied lifecycle status so the repair pipeline
        # can exclude terminal or otherwise ineligible work with a truthful reason.
        open_order_rows = _map_rows(parsed["open_orders"], _OPEN_ORDERS_MAP)
        for row in open_order_rows:
            row.setdefault("orderstatus", "OPEN")
        _write_domain(
            out_dir,
            "order_plan",
            open_order_rows,
        )
        artifacts.append({"domain": "order_plan", "status": "succeeded"})

    if "requisitions" in parsed:
        _write_domain(
            out_dir,
            "order_plan_data_requisition",
            _map_rows(
                parsed["requisitions"],
                _REQUISITIONS_MAP,
                extra={
                    "orderstatus": "OPEN",
                    "receivedquantity": "0",
                },
            ),
        )
        artifacts.append(
            {
                "domain": "order_plan_data_requisition",
                "status": "succeeded",
                "source": "canonical.requisitions",
            }
        )

    if "repair_history" in parsed:
        observations = [
            observation_from_row(row, tenant_id=tenant_id)
            for row in parsed["repair_history"]
        ]
        accepted = [
            observation
            for observation in observations
            if observation.is_observed_return
        ]
        repair_rows = [
            {
                "hostorderid": observation.repair_order_id,
                "orderid": observation.repair_line_id,
                "hostpartid": observation.part_number,
                "hostvendorlocid": observation.shop_identity or "DEFAULT",
                "hostlocid": observation.location_code or "",
                "planquantity": str(observation.quantity),
                "receivedquantity": str(observation.quantity),
                "planorderdate": observation.started_at.isoformat(),
                "actualrcvdate": observation.completed_at.isoformat(),
                "ordertypeid": "RO",
                "orderstatus": "CLOSED",
                "repairoutcome": observation.outcome or "",
                "serialnumber": observation.serial_number or "",
                "canonicalprovenance": "canonical.repair_history",
                "repair_contract_version": observation.contract_version,
            }
            for observation in accepted
        ]
        _write_domain(
            out_dir,
            "order_plan_closed_orders",
            repair_rows,
        )
        coverage = repair_history_coverage(
            parsed,
            tenant_id=tenant_id,
        )
        artifacts.append(
            {
                "domain": "order_plan_closed_orders",
                "status": "succeeded",
                "source": "canonical.repair_history",
                "row_count": len(repair_rows),
                "repair_history": coverage.as_dict(),
            }
        )

    canonical_vendor_rows = parsed.get("vendors", [])
    vendor_rows = _map_rows(canonical_vendor_rows, _VENDORS_MAP)
    vendor_pns = {
        row.get("part_number")
        for row in canonical_vendor_rows
        if row.get("part_number")
    }
    derived_vendor_rows = [
        {
            "hostpartid": row["part_number"],
            "hostvendorlocid": "DEFAULT",
            "price": row["unit_cost"],
            "processinglength": "21",
            "minoq": "1",
            "condition": "NEW",
            "preferred": "Y",
            "canonicalprovenance": "canonical.parts.unit_cost",
        }
        for row in parts
        if row.get("part_number") not in vendor_pns
        and row.get("unit_cost") is not None
        and str(row["unit_cost"]).strip() != ""
    ]
    if "vendors" in parsed or derived_vendor_rows:
        _write_domain(
            out_dir,
            "pn_vendor_price",
            [*vendor_rows, *derived_vendor_rows],
        )
        sources = []
        if "vendors" in parsed:
            sources.append("canonical.vendors")
        if derived_vendor_rows:
            sources.append("canonical.parts.unit_cost")
        vendor_artifact: dict[str, Any] = {
            "domain": "pn_vendor_price",
            "status": "succeeded",
            "source": "+".join(sources),
            "derived": bool(derived_vendor_rows),
            "canonical_file_supplied": "vendors" in parsed,
        }
        if derived_vendor_rows:
            vendor_artifact["defaults"] = {
                "vendor": "DEFAULT",
                "minimum_order_qty": 1,
                "lead_time_days": 21,
            }
        artifacts.append(vendor_artifact)

    resolved_as_of, as_of_source = _resolve_snapshot_as_of(
        parsed,
        demand_window=demand_window,
        snapshot_as_of=snapshot_as_of,
    )
    manifest = {
        "tenant_id": tenant_id,
        "extract_date": resolved_as_of.isoformat(),
        "extract_date_source": as_of_source,
        "artifacts": artifacts,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest))
