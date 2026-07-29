"""Repair-history normalization and truthful coverage summaries."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

from pydantic import ValidationError

from trax_io_reco.contracts.repair import RepairCycleObservation

_TRUTHY = {"1", "true", "yes", "y"}
_REPAIRABLE_PART_CLASSES = {"repairable", "rotable"}


def observation_from_row(
    row: dict,
    *,
    tenant_id: str,
) -> RepairCycleObservation:
    """Validate a canonical row against the public observation contract."""

    return RepairCycleObservation(
        tenant_id=tenant_id,
        repair_order_id=row.get("repair_order_id"),
        repair_line_id=row.get("repair_line_id"),
        part_number=row.get("part_number"),
        quantity=row.get("quantity"),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        status=row.get("status"),
        shop_code=row.get("shop_code"),
        vendor_code=row.get("vendor_code"),
        location_code=row.get("location_code"),
        outcome=row.get("outcome"),
        serial_number=row.get("serial_number"),
    )


@dataclass(frozen=True)
class RepairHistoryCoverage:
    """Reader-facing summary at the canonical-ingest boundary."""

    accepted: int
    excluded: int
    quarantined: int
    parts_covered: int
    shops_covered: int
    observed: int
    pooled: int
    proxy: int
    unavailable: int
    proxy_definition: str = "order_creation_to_last_receipt"

    def as_dict(self) -> dict[str, int | str]:
        return asdict(self)


def repair_history_coverage(
    parsed: dict[str, list[dict]],
    *,
    tenant_id: str,
    validation_errors: Iterable[object] = (),
) -> RepairHistoryCoverage:
    """Summarize canonical repair rows without weakening fail-closed validation.

    Callers still decide whether any validation error rejects the whole ingest.
    This function only makes the rejected batch observable: valid return rows
    are accepted, valid non-return terminal outcomes are excluded, and any row
    carrying a repair-history validation error is counted once as quarantined.
    """

    invalid_rows: set[int] = set()
    file_level_invalid = False
    for error in validation_errors:
        if isinstance(error, dict):
            file_name = error.get("file")
            row_index = error.get("row")
        else:
            file_name = getattr(error, "file", None)
            row_index = getattr(error, "row", None)
        if file_name != "repair_history":
            continue
        if row_index is None:
            file_level_invalid = True
        elif isinstance(row_index, int) and row_index >= 0:
            invalid_rows.add(row_index)

    observations: list[RepairCycleObservation] = []
    quarantined = 0
    for index, row in enumerate(parsed.get("repair_history", [])):
        if file_level_invalid or index in invalid_rows:
            quarantined += 1
            continue
        try:
            observations.append(
                observation_from_row(row, tenant_id=tenant_id)
            )
        except ValidationError:
            # Defensive parity for callers that request coverage before running
            # the canonical validator. The row remains rejected and never
            # contributes observations or coverage.
            quarantined += 1

    accepted = [row for row in observations if row.is_observed_return]
    excluded = len(observations) - len(accepted)
    observed_parts = {row.part_number for row in accepted}
    observed_shops = {
        shop
        for row in accepted
        if (shop := row.shop_identity) is not None
    }
    pooled_parts = {
        row.part_number
        for row in accepted
        if row.shop_identity is None
    }

    repairable_parts = {
        str(row.get("part_number") or "").strip()
        for row in parsed.get("parts", [])
        if str(row.get("part_number") or "").strip()
        and (
            str(row.get("part_class") or "").strip().lower()
            in _REPAIRABLE_PART_CLASSES
            or str(row.get("repairable") or "").strip().lower() in _TRUTHY
        )
    }
    proxy_parts = {
        str(row.get("part_number") or "").strip()
        for row in parsed.get("vendors", [])
        if str(row.get("part_number") or "").strip()
        and str(row.get("condition") or "").strip().upper() in {"REP", "RO"}
    } - observed_parts
    unavailable_parts = repairable_parts - observed_parts - proxy_parts
    return RepairHistoryCoverage(
        accepted=len(accepted),
        excluded=excluded,
        quarantined=quarantined,
        parts_covered=len(observed_parts),
        shops_covered=len(observed_shops),
        observed=len(observed_parts),
        pooled=len(pooled_parts),
        proxy=len(proxy_parts),
        unavailable=len(unavailable_parts),
    )
